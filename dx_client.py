"""dx_client.py - starts DXHost (the C++ plugin host, dxhost/) and talks to it over a local socket.

DXHost does all the audio: it hosts the plugins, runs the mixer channels, inserts and master chain, opens the plugin windows
and compensates plugin delay. This module is the arranger's side of the conversation:
    * commands as JSON lines, answered asynchronously (same request()/send() shape as plugin_bridge.Bridge),
    * MIDI as short text lines ("M <slot> <hex> ...") with no answer, for the lowest delay.
NativeBridge makes one DXHost strip (or the master chain) look like a plugin_bridge.Bridge, so plugin_host.py
works with either engine.
"""
import itertools
import json
import os
import socket
import subprocess
import sys
import threading
import time

NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def find_dxhost(app_dir, cfg):
    """Path of DXHost.exe if it is there, else None."""
    names = ("DXHost.exe",) if os.name == "nt" else ("DXHost", "DXHost.exe")
    cands = []
    if cfg.get("dxhost_path"):
        cands.append(str(cfg["dxhost_path"]))
    for base in (app_dir, os.path.join(app_dir, "dxhost")):
        for n in names:
            cands.append(os.path.join(base, n))
    for p in cands:
        if os.path.isfile(p):
            return p
    return None


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class DXClient:
    _ids = itertools.count(1)

    def __init__(self, exe, log_path=None):
        self.exe = exe
        self.port = _free_port()
        self.dead = False
        self.waiting, self.wlock = {}, threading.Lock()
        self.wr_lock = threading.Lock()
        self.log = open(log_path, "ab") if log_path else subprocess.DEVNULL
        self.proc = subprocess.Popen([exe, "--port", str(self.port)], stdout=self.log, stderr=self.log,
                                     creationflags=NO_WINDOW)
        self.sock = None
        end = time.time() + 15
        while time.time() < end:
            if self.proc.poll() is not None:
                raise RuntimeError("DXHost stopped at start-up (exit code %s)." % self.proc.returncode)
            try:
                self.sock = socket.create_connection(("127.0.0.1", self.port), timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        if self.sock is None:
            self.kill()
            raise RuntimeError("DXHost did not start listening.")
        self.sock.settimeout(None)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        threading.Thread(target=self._reader, daemon=True).start()

    # ---- conversation ------------------------------------------------------------------------------------
    def _reader(self):
        buf = b""
        try:
            while True:
                chunk = self.sock.recv(65536)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    try:
                        m = json.loads(line.decode("utf-8", "replace"))
                    except ValueError:
                        continue
                    with self.wlock:
                        cb = self.waiting.pop(m.get("id"), None)
                    if cb:
                        cb(m)
        except OSError:
            pass
        self.dead = True
        with self.wlock:
            pending, self.waiting = list(self.waiting.values()), {}
        for cb in pending:
            cb({"ok": False, "error": "DXHost ended (a plugin probably crashed it).", "data": {}})

    def alive(self):
        return (not self.dead) and self.proc.poll() is None

    def _write(self, data):
        if self.dead:
            return False
        try:
            with self.wr_lock:
                self.sock.sendall(data)
            return True
        except OSError:
            self.dead = True
            return False

    def send(self, cmd, callback):
        cmd = dict(cmd, id=next(self._ids))
        with self.wlock:
            self.waiting[cmd["id"]] = callback
        if not self._write((json.dumps(cmd) + "\n").encode("utf-8")):
            with self.wlock:
                self.waiting.pop(cmd["id"], None)
            callback({"ok": False, "error": "DXHost is not running.", "data": {}})

    def request(self, cmd, timeout=60):
        box, ev = {}, threading.Event()

        def got(m):
            box.update(m)
            ev.set()

        self.send(cmd, got)
        if not ev.wait(timeout):
            raise RuntimeError("DXHost did not answer within %d s." % timeout)
        if not box.get("ok"):
            raise RuntimeError(box.get("error") or "failed")
        return box.get("data", {})

    def midi(self, slot, messages):
        """messages: one bytes object or a list of them."""
        if isinstance(messages, (bytes, bytearray)):
            messages = [messages]
        line = "M %d %s\n" % (slot, " ".join(bytes(m).hex() for m in messages if m))
        self._write(line.encode("ascii"))

    def kill(self):
        self.dead = True
        try:
            self.proc.kill()
        except Exception:
            pass

    def close(self):
        if not self.dead:
            try:
                self.request({"cmd": "quit"}, timeout=2)
            except Exception:
                pass
        self.dead = True
        try:
            self.sock.close()
        except Exception:
            pass
        try:
            self.proc.wait(2)
        except Exception:
            self.kill()


class _FakeProc:
    def __init__(self, bridge):
        self.b = bridge

    def is_alive(self):
        return self.b.alive()

    @property
    def pid(self):
        return self.b.client.proc.pid


class NativeBridge:
    """One DXHost strip (slot number) or the master chain ("master"), shaped like plugin_bridge.Bridge."""

    def __init__(self, client, which, effects_only=False):
        self.client, self.which, self.effects_only = client, which, effects_only
        self.closed = False
        self.busy = False
        self.proc = _FakeProc(self)

    @property
    def dead(self):
        return self.closed or self.client.dead

    @dead.setter
    def dead(self, value):
        if value:
            self.closed = True

    def alive(self):
        return (not self.closed) and self.client.alive()

    def _wire(self, cmd):
        c = dict(cmd, slot=self.which)
        c.pop("multi", None)
        return c

    def send(self, cmd, callback):
        if self.dead:
            callback({"ok": False, "error": "The plugin host is not running.", "data": {}})
            return
        self.client.send(self._wire(cmd), callback)

    def request(self, cmd, timeout=90):
        if self.dead:
            raise RuntimeError("The plugin host is not running.")
        return self.client.request(self._wire(cmd), timeout)

    def try_finish(self):
        return True

    def kill(self):
        self.client.kill()

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.which != "master" and self.client.alive():
            try:
                self.client.request({"cmd": "unload", "slot": self.which}, timeout=5)
            except Exception:
                pass
