"""plugin_bridge.py - runs plugins in their OWN helper process ("bridged", like FL Studio / jBridge).

One bridge = one helper process holding
    * an optional INSTRUMENT (a VST3 that turns MIDI into sound), and
    * a chain of INSERT EFFECTS that process that sound (or, in an effects-only bridge, the master mix).

Why: a plugin that crashes or hangs only kills its helper. The app keeps playing, the slot is released back
to the internal sound and restarted. It also lets every plugin render on its own CPU core, with no Python lock between them.

How the app and a helper talk
    audio + MIDI : shared memory, one block at a time. The app writes the block's MIDI (and, for an effects-only bridge, the
                   audio to process), releases the helper's `go` semaphore, and later waits on `done` for the finished audio.
                   Every bridge works on the same block at the same time, so the cost is the slowest plugin, not the sum.
    commands     : a pipe (load / unload an effect / save sound / open window ...). Answers come back asynchronously.
The plugin window opens on the helper's own main thread, so it never touches the app's threads.
"""
import base64
import itertools
import multiprocessing as mproc
import threading
import time
from multiprocessing import shared_memory

import numpy as np

MAX_FRAMES = 4096                       # largest audio block the app may ask for
MAX_EVENTS = 1024                       # most MIDI messages in one block
EV = np.dtype([("t", "<f4"), ("b0", "u1"), ("b1", "u1"), ("b2", "u1"), ("n", "u1")])     # 8 bytes per MIDI message
_HDR, _EV_OFF = 64, 64
_IN_OFF = _EV_OFF + MAX_EVENTS * EV.itemsize
_AUDIO = MAX_FRAMES * 2 * 4
_OUT_OFF = _IN_OFF + _AUDIO
SHM_SIZE = _OUT_OFF + _AUDIO


def _views(buf):
    hdr = np.ndarray((16,), dtype=np.int32, buffer=buf, offset=0)                # [0]=events, [1]=frames
    ev = np.ndarray((MAX_EVENTS,), dtype=EV, buffer=buf, offset=_EV_OFF)
    ain = np.ndarray((MAX_FRAMES, 2), dtype=np.float32, buffer=buf, offset=_IN_OFF)
    aout = np.ndarray((MAX_FRAMES, 2), dtype=np.float32, buffer=buf, offset=_OUT_OFF)
    return hdr, ev, ain, aout


def _b64(raw):
    return base64.b64encode(bytes(raw)).decode("ascii")


# ===================================================================================================
#  CHILD PROCESS
# ===================================================================================================
def _read_state(plugin):
    try:
        return _b64(plugin.raw_state)
    except Exception:
        pass
    try:                                                    # older pedalboard: keep the parameter values instead
        import json
        vals = {k: float(p.raw_value) for k, p in plugin.parameters.items()}
        return _b64(json.dumps({"params": vals}).encode("utf-8"))
    except Exception:
        return ""


def _apply_state(plugin, b64):
    if not b64:
        return
    import json
    try:
        raw = base64.b64decode(b64)
        if raw.startswith(b'{"params"'):
            for k, v in json.loads(raw.decode("utf-8"))["params"].items():
                try:
                    plugin.parameters[k].raw_value = v
                except Exception:
                    pass
        else:
            plugin.raw_state = raw
    except Exception:
        pass


def _load(path, name, multi):
    from pedalboard import load_plugin
    return load_plugin(path, plugin_name=name) if (multi and name) else load_plugin(path)


def bridge_main(conn, go, done, shm_name, rate, block):
    """Entry point of the helper process."""
    shm = shared_memory.SharedMemory(name=shm_name)
    hdr, ev, ain, aout = _views(shm.buf)
    S = {"inst": None, "fx": [], "first": True, "fxfirst": []}      # fx: list of [plugin, bypass]
    lock = threading.Lock()
    stop = threading.Event()

    def render():
        frames = int(hdr[1])
        n_ev = int(hdr[0])
        dur = frames / float(rate)
        inst = S["inst"]
        if inst is not None:
            msgs = []
            for i in range(n_ev):
                e = ev[i]
                msgs.append((bytes((int(e["b0"]), int(e["b1"]), int(e["b2"])))[:int(e["n"])], float(e["t"])))
            audio = inst(msgs, duration=dur, sample_rate=rate, num_channels=2, buffer_size=block,
                         reset=S["first"])
            S["first"] = False
        else:                                               # effects-only bridge: process what the app sent
            audio = np.ascontiguousarray(ain[:frames].T)
        for i, (p, bypass) in enumerate(S["fx"]):
            if bypass:
                continue
            audio = p(audio, rate, buffer_size=block, reset=S["fxfirst"][i])
            S["fxfirst"][i] = False
        n = min(frames, audio.shape[1])
        aout[:n] = audio[:, :n].T
        if n < frames:
            aout[n:frames] = 0.0

    def render_loop():
        while not stop.is_set():
            if not go.acquire(timeout=0.5):
                continue
            if stop.is_set():
                break
            try:
                with lock:
                    render()
            except Exception:
                aout[:int(hdr[1])] = 0.0
            finally:
                done.release()

    threading.Thread(target=render_loop, daemon=True).start()

    def reply(i, ok=True, error="", **data):
        try:
            conn.send({"id": i, "ok": ok, "error": error, "data": data})
        except Exception:
            pass

    while True:                                              # the MAIN thread: commands and plugin windows
        try:
            m = conn.recv()
        except (EOFError, OSError):
            break
        c, i = m.get("cmd"), m.get("id")
        try:
            if c == "quit":
                break
            elif c == "load":
                p = _load(m["path"], m.get("name"), m.get("multi"))
                if not getattr(p, "is_instrument", True):
                    raise RuntimeError("That plugin is an effect, not an instrument.")
                _apply_state(p, m.get("state"))
                with lock:
                    S["inst"], S["first"] = p, True
                reply(i, name=str(getattr(p, "name", "") or ""))
            elif c == "fx_set":
                idx, plug = int(m["index"]), None
                if m.get("path"):
                    plug = _load(m["path"], m.get("name"), m.get("multi"))
                    if getattr(plug, "is_instrument", False):
                        raise RuntimeError("That plugin is an instrument, not an effect.")
                    _apply_state(plug, m.get("state"))
                with lock:
                    while len(S["fx"]) <= idx:
                        S["fx"].append([None, False])
                        S["fxfirst"].append(True)
                    if plug is None:
                        del S["fx"][idx]
                        del S["fxfirst"][idx]
                    else:
                        S["fx"][idx] = [plug, bool(m.get("bypass"))]
                        S["fxfirst"][idx] = True
                reply(i)
            elif c == "fx_bypass":
                with lock:
                    S["fx"][int(m["index"])][1] = bool(m.get("on"))
                reply(i)
            elif c == "state":
                reply(i, inst=_read_state(S["inst"]) if S["inst"] is not None else "",
                      fx=[_read_state(p) for p, _ in S["fx"]])
            elif c == "editor":
                t = m.get("target", "inst")
                plug = S["inst"] if t == "inst" else S["fx"][int(t)][0]
                if plug is None or not hasattr(plug, "show_editor"):
                    raise RuntimeError("This plugin has no window that can be shown.")
                plug.show_editor()                          # blocks this thread until the window is closed
                reply(i, inst=_read_state(S["inst"]) if S["inst"] is not None else "",
                      fx=[_read_state(p) for p, _ in S["fx"]])
            else:
                reply(i, False, "unknown command")
        except Exception as err:
            reply(i, False, str(err) or type(err).__name__)
    stop.set()
    go.release()
    try:
        shm.close()
    except Exception:
        pass


# ===================================================================================================
#  APP SIDE
# ===================================================================================================
class Bridge:
    """The app's handle on one helper process."""
    _ids = itertools.count(1)

    def __init__(self, rate, block, effects_only=False):
        ctx = mproc.get_context("spawn")
        self.rate, self.block, self.effects_only = rate, block, effects_only
        self.shm = shared_memory.SharedMemory(create=True, size=SHM_SIZE)
        self.hdr, self.ev, self.ain, self.aout = _views(self.shm.buf)
        self.go, self.done = ctx.Semaphore(0), ctx.Semaphore(0)
        self.conn, child_end = ctx.Pipe()
        self.proc = ctx.Process(target=bridge_main, args=(child_end, self.go, self.done, self.shm.name, rate, block),
                                daemon=True)
        self.proc.start()
        child_end.close()                                    # so we see the pipe close if the helper dies
        self.dead, self.busy, self.closed = False, False, False
        self.waiting, self.wlock = {}, threading.Lock()
        threading.Thread(target=self._reader, daemon=True).start()

    # ---- commands ----------------------------------------------------------------------------------------
    def _reader(self):
        while True:
            try:
                m = self.conn.recv()
            except (EOFError, OSError):
                break
            with self.wlock:
                w = self.waiting.pop(m.get("id"), None)
            if w:
                w(m)
        self.dead = True
        with self.wlock:
            pending, self.waiting = list(self.waiting.values()), {}
        for w in pending:
            w({"ok": False, "error": "The plugin process ended (the plugin probably crashed).", "data": {}})

    def send(self, cmd, callback):
        """Asynchronous: callback(result_dict) runs on the reader thread when the helper answers."""
        if self.dead:
            callback({"ok": False, "error": "The plugin process is not running.", "data": {}})
            return
        cmd = dict(cmd, id=next(self._ids))
        with self.wlock:
            self.waiting[cmd["id"]] = callback
        try:
            self.conn.send(cmd)
        except (OSError, ValueError):
            self.dead = True
            with self.wlock:
                self.waiting.pop(cmd["id"], None)
            callback({"ok": False, "error": "The plugin process is not running.", "data": {}})

    def request(self, cmd, timeout=90):
        """Synchronous. Returns the answer's data or raises RuntimeError with the reason."""
        box, ev = {}, threading.Event()

        def got(m):
            box.update(m)
            ev.set()

        self.send(cmd, got)
        if not ev.wait(timeout):
            raise RuntimeError("The plugin did not answer within %d s." % timeout)
        if not box.get("ok"):
            raise RuntimeError(box.get("error") or "failed")
        return box.get("data", {})

    # ---- audio -------------------------------------------------------------------------------------------
    def submit(self, msgs, frames, audio_in=None):
        """Start rendering one block. msgs = [(bytes, offset_seconds)]."""
        msgs = msgs[:MAX_EVENTS]
        for i, (m, t) in enumerate(msgs):
            e = self.ev[i]
            e["t"], e["n"] = t, min(len(m), 3)
            e["b0"], e["b1"], e["b2"] = m[0], (m[1] if len(m) > 1 else 0), (m[2] if len(m) > 2 else 0)
        self.hdr[0], self.hdr[1] = len(msgs), frames
        if audio_in is not None:
            self.ain[:frames] = audio_in[:frames]
        self.busy = True
        self.go.release()

    def collect(self, timeout):
        """The finished block as a (frames, 2) array, or None if the helper is not done in time."""
        if not self.busy:
            return None
        if not self.done.acquire(timeout=max(timeout, 0.0)):
            return None
        self.busy = False
        return self.aout[:int(self.hdr[1])]

    def try_finish(self):
        """A block that ran late has finished by now? (its audio is dropped)"""
        if self.busy and self.done.acquire(timeout=0):
            self.busy = False
        return not self.busy

    def alive(self):
        return (not self.dead) and self.proc.is_alive()

    def kill(self):
        self.dead = True
        try:
            self.proc.terminate()
        except Exception:
            pass

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.conn.send({"cmd": "quit", "id": 0})
        except Exception:
            pass
        self.proc.join(1.5)
        if self.proc.is_alive():
            self.proc.terminate()
            self.proc.join(1.0)
        self.dead = True
        try:
            self.conn.close()
        except Exception:
            pass
        try:
            self.hdr = self.ev = self.ain = self.aout = None
            self.shm.close()
            self.shm.unlink()
        except Exception:
            pass
