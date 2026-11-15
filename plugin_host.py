"""plugin_host.py - the PLUGINS & VST rack: hosts VST3 instruments and effects, laid out like FL Studio.

    style / pads / PERC pads -> PartMixer (volumes, drum EQ) -> PluginHost -> per MIDI channel:
        * INTERNAL sound  (FluidSynth / Windows synth / MIDI port: whatever config.json "synth" says), or
        * a PLUGIN SLOT   = one mixer channel:  [bridged instrument] -> [up to 4 insert effects] -> level / pan / mute / solo
        * a MIDI-PORT SLOT: the notes leave through a MIDI port (loopMIDI, hardware, a standalone plugin)
    all channel strips are summed -> MASTER channel: [up to 4 insert effects] -> master volume -> sound card

Bridging: every slot runs in its own helper process (plugin_bridge.py), as in FL Studio. A plugin that crashes or hangs
only takes down its helper: the slot is released to the internal sound and restarted from its last saved sound.

Routing is per MIDI channel. A channel no slot claims plays on the internal sound exactly as before. A channel claimed by
one or more slots goes to those slots only (claim it in two slots to layer plugins).

Needs   pip install pedalboard sounddevice numpy        (bundled into the Windows .exe by the build)
Limits  pedalboard hosts VST3 (and Audio Units on a Mac): no old VST2 .dll plugins, no CLAP.

Files next to the app: plugins.json (scan folders + scan results), rack.json (slots, inserts, master inserts, routing, sounds).
"""
import base64
import json
import math
import os
import subprocess
import sys
import threading
import time

from dx_client import DXClient, NativeBridge, find_dxhost
from plugin_bridge import Bridge

DXHOST_EXE = None                       # set when the C++ host is in use: the scanner then needs no Python plugin library

MAX_INSERTS = 4                       # insert effects per channel strip (and for the master)

PARTS = [("DRUMS", [8, 9]), ("BASS", [10]), ("CHORD 1", [11]), ("CHORD 2", [12]), ("PAD", [13]),
         ("PHRASE", [14, 15]), ("ALL CHANNELS", list(range(16)))]

NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


# ===================================================================================================
#  scanning: find plugin files, ask each one (in a throw-away child process) what it is
# ===================================================================================================
def default_folders(app_dir):
    out = []
    if os.name == "nt":
        for var in ("CommonProgramFiles", "CommonProgramW6432"):
            if os.environ.get(var):
                out.append(os.path.join(os.environ[var], "VST3"))
        if os.environ.get("LOCALAPPDATA"):
            out.append(os.path.join(os.environ["LOCALAPPDATA"], "Programs", "Common", "VST3"))
    elif sys.platform == "darwin":
        out += ["/Library/Audio/Plug-Ins/VST3", os.path.expanduser("~/Library/Audio/Plug-Ins/VST3")]
    else:
        out += [os.path.expanduser("~/.vst3"), "/usr/lib/vst3", "/usr/local/lib/vst3"]
    out.append(os.path.join(app_dir, "Plugins"))
    seen, uniq = set(), []
    for p in out:
        k = os.path.normcase(os.path.abspath(p))
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return uniq


def find_plugin_files(folders, max_depth=4):
    """Every .vst3 under the folders (a .vst3 can be a single file or a bundle folder: either way it counts once)."""
    found = []
    for root in folders:
        if os.path.isfile(root) and root.lower().endswith(".vst3"):
            found.append(root)
            continue
        if not os.path.isdir(root):
            continue
        base_depth = root.rstrip("\\/").count(os.sep)
        for cur, dirs, files in os.walk(root):
            if cur.count(os.sep) - base_depth >= max_depth:
                dirs[:] = []
            for d in list(dirs):
                if d.lower().endswith(".vst3"):
                    found.append(os.path.join(cur, d))
                    dirs.remove(d)                     # do not look inside a plugin bundle
            for f in files:
                if f.lower().endswith(".vst3"):
                    found.append(os.path.join(cur, f))
    seen, uniq = set(), []
    for p in found:
        k = os.path.normcase(os.path.abspath(p))
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return sorted(uniq, key=lambda p: os.path.basename(p).lower())


def probe_main(path):
    """CHILD PROCESS (DX-Arranger.exe --plugin-probe <file>): load the plugin file and print what is inside.
    If the plugin crashes, only this helper dies."""
    from pedalboard import VST3Plugin, load_plugin
    try:
        names = list(VST3Plugin.get_plugin_names_for_file(path))
    except Exception:
        names = []
    out = []
    for n in (names or [None]):
        try:
            p = load_plugin(path, plugin_name=n) if (n and len(names) > 1) else load_plugin(path)
            out.append({"name": n or str(getattr(p, "name", "") or os.path.splitext(os.path.basename(path))[0]),
                        "instrument": bool(getattr(p, "is_instrument", False)), "multi": len(names) > 1})
        except Exception as err:
            out.append({"name": n or os.path.splitext(os.path.basename(path))[0], "error": str(err)[:300],
                        "multi": len(names) > 1})
    print("RESULT " + json.dumps(out), flush=True)


def _probe_cmd(path):
    exe = ([sys.executable] if getattr(sys, "frozen", False)
           else [sys.executable, os.path.abspath(sys.argv[0])])
    return exe + ["--plugin-probe", path]


def _probe_dx(path, timeout):
    import tempfile
    fd, out = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        r = subprocess.run([DXHOST_EXE, "--probe", path, "--out", out], capture_output=True, timeout=timeout,
                           creationflags=NO_WINDOW)
        try:
            with open(out, encoding="utf-8") as f:
                text = f.read()
        except OSError:
            text = ""
        if not text.strip():
            raise RuntimeError("crashed or could not be read (exit code %s)" % r.returncode)
        return json.loads(text)
    except subprocess.TimeoutExpired:
        raise RuntimeError("did not finish loading within %d s" % timeout)
    except (OSError, ValueError) as err:
        raise RuntimeError(str(err))
    finally:
        try:
            os.remove(out)
        except OSError:
            pass


def probe_in_child(path, timeout=90):
    """Returns the list of entries found in the file, or raises RuntimeError saying what went wrong."""
    if DXHOST_EXE:
        return _probe_dx(path, timeout)
    try:
        r = subprocess.run(_probe_cmd(path), capture_output=True, text=True, timeout=timeout,
                           creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise RuntimeError("did not finish loading within %d s" % timeout)
    except OSError as err:
        raise RuntimeError(str(err))
    for line in (r.stdout or "").splitlines():
        if line.startswith("RESULT "):
            try:
                return json.loads(line[7:])
            except ValueError:
                break
    tail = [x for x in ((r.stdout or "") + "\n" + (r.stderr or "")).splitlines() if x.strip()]
    raise RuntimeError("crashed or could not be read (exit code %s) %s" % (r.returncode, tail[-1][:160] if tail else ""))


class Catalog:
    """The list of plugin files the scan found, remembered in plugins.json so a restart does not re-probe everything."""

    def __init__(self, app_dir, cfg, probe=None):
        self.app_dir, self.cfg = app_dir, cfg
        self.path = os.path.join(app_dir, "plugins.json")
        self.probe = probe or probe_in_child
        self.lock = threading.Lock()
        self.folders, self.found = [], {}
        self.scanning, self.done, self.total = False, 0, 0
        self._load()

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            self.folders = [x for x in d.get("folders", []) if isinstance(x, str)]
            self.found = d.get("found", {}) if isinstance(d.get("found"), dict) else {}
        except (OSError, ValueError):
            pass

    def save(self):
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"folders": self.folders, "found": self.found}, f, indent=1)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def all_folders(self):
        extra = [x for x in (self.cfg.get("plugin_folders") or []) if isinstance(x, str)]
        out, seen = [], set()
        for p in default_folders(self.app_dir) + extra + self.folders:
            k = os.path.normcase(os.path.abspath(p))
            if k not in seen:
                seen.add(k)
                out.append(p)
        return out

    def add_folder(self, path):
        path = os.path.normpath(str(path).strip().strip('"'))
        if not (os.path.isdir(path) or (os.path.isfile(path) and path.lower().endswith(".vst3"))):
            raise ValueError("That folder / .vst3 file does not exist: " + path)
        if path not in self.folders:
            self.folders.append(path)
            self.save()

    def remove_folder(self, path):
        if path in self.folders:
            self.folders.remove(path)
            self.save()

    def entries(self):
        """Flat list: one row per plugin (a file can hold several)."""
        rows = []
        with self.lock:
            files = find_plugin_files(self.all_folders())
            for p in files:
                rec = self.found.get(p)
                base = os.path.splitext(os.path.basename(p))[0]
                if not rec:
                    rows.append({"path": p, "file": os.path.basename(p), "name": base, "instrument": None,
                                 "error": "", "multi": False})
                    continue
                for e in rec.get("entries", []):
                    rows.append({"path": p, "file": os.path.basename(p), "name": e.get("name") or base,
                                 "instrument": e.get("instrument") if not e.get("error") else None,
                                 "error": e.get("error", ""), "multi": bool(e.get("multi"))})
        return rows

    def lookup(self, path, name=None):
        for e in self.entries():
            if e["path"] == path and (name is None or e["name"] == name):
                return e
        return None

    def scan(self, force=False, on_progress=None):
        if self.scanning:
            return False
        self.scanning, self.done, self.total = True, 0, 0
        threading.Thread(target=self._scan, args=(force, on_progress), daemon=True).start()
        return True

    def _scan(self, force, on_progress):
        try:
            files = find_plugin_files(self.all_folders())
            todo = []
            for p in files:
                try:
                    mt = os.path.getmtime(p)
                except OSError:
                    continue
                rec = self.found.get(p)
                if force or not rec or rec.get("mtime") != mt:
                    todo.append((p, mt))
            self.total = len(todo)
            from concurrent.futures import ThreadPoolExecutor

            def work(item):
                p, mt = item
                try:
                    ents = self.probe(p)
                except RuntimeError as err:
                    ents = [{"name": os.path.splitext(os.path.basename(p))[0], "error": str(err)}]
                with self.lock:
                    self.found[p] = {"mtime": mt, "entries": ents}
                self.done += 1
                if on_progress:
                    on_progress()

            if todo:
                with ThreadPoolExecutor(max_workers=3) as pool:
                    list(pool.map(work, todo))
            with self.lock:                              # forget plugins that no longer exist
                for p in [p for p in self.found if not os.path.exists(p)]:
                    del self.found[p]
            self.save()
        finally:
            self.scanning = False
            if on_progress:
                on_progress()


# ===================================================================================================
#  slots (= mixer channel strips)
# ===================================================================================================
class Slot:
    def __init__(self, idx):
        self.idx = idx
        self.kind = "plugin"                    # "plugin" | "midi"
        self.path, self.pname, self.port = "", "", ""
        self.channels = set()                   # 0-based MIDI channels this slot receives
        self.level, self.pan, self.mute, self.solo = 1.0, 0.0, False, False
        self.out_ch = 0                         # 0 = keep the channel; 1-16 = send everything on that channel
        self.programs = False                   # pass program change / bank select (off: a style cannot switch your preset)
        self.vel_vol = False                    # scale note velocity by the part volume (for plugins that ignore CC 7)
        self.state_b64 = ""                     # the instrument's own saved sound
        self.fx = []                            # inserts: {"path","name","multi","bypass","state"}
        self.bridge, self.dev = None, None
        self.sink = None                        # DXHost engine: callable(bytes) that sends a MIDI message to this strip
        self.loading, self.error = False, ""
        self.pending, self.qlock = [], threading.Lock()
        self.chvol = [100] * 16
        self.peak = 0.0
        self.late = 0                           # blocks in a row the helper was too slow for
        self.restarts = []                      # times of automatic restarts after a crash

    @property
    def active(self):
        if self.kind == "midi":
            return self.dev is not None
        return self.bridge is not None and not self.bridge.dead

    @property
    def title(self):
        if self.kind == "midi":
            return ("MIDI > " + self.port) if self.port else ""
        return self.pname or ""

    def push(self, m, now):
        st = m[0]
        kind = st & 0xF0
        if not self.programs and (kind == 0xC0 or (kind == 0xB0 and len(m) > 1 and m[1] in (0, 32))):
            return
        ch = st & 0x0F
        if kind == 0xB0 and len(m) > 2 and m[1] == 7:
            self.chvol[ch] = m[2]
        elif kind == 0x90 and len(m) > 2 and m[2] > 0 and self.vel_vol:
            m = bytes([st, m[1], _clamp(int(round(m[2] * self.chvol[ch] / 100.0)), 1, 127)])
        if self.out_ch:
            m = bytes([kind | (self.out_ch - 1)]) + bytes(m[1:])
        if self.kind == "midi":
            try:
                self.dev.send(m)
            except Exception as err:
                self.error = str(err)
            return
        if self.sink is not None:
            self.sink(bytes(m))
            return
        with self.qlock:
            if len(self.pending) < 4000:                 # a stuck helper must not eat memory
                self.pending.append((bytes(m), now))

    def silence(self, channels=None, hard=True):
        """All notes off (and all sound off) on the given channels (default all)."""
        now = time.perf_counter()
        for ch in (range(16) if channels is None else channels):
            c = (self.out_ch - 1) if self.out_ch else ch
            for cc in ((123, 120) if hard else (123,)):
                m = bytes([0xB0 | c, cc, 0])
                if self.kind == "midi":
                    try:
                        self.dev.send(m)
                    except Exception:
                        pass
                elif self.sink is not None:
                    self.sink(m)
                else:
                    with self.qlock:
                        self.pending.append((m, now))

    def info(self, with_peak=False):
        chans = sorted(self.channels)
        return {"i": self.idx, "kind": self.kind, "name": self.title, "path": self.path, "port": self.port,
                "loaded": self.active and not self.loading, "loading": self.loading, "error": self.error,
                "channels": chans,
                "parts": [n for n, cs in PARTS[:-1] if set(cs) <= self.channels] +
                         (["ALL"] if len(chans) == 16 else []),
                "level": int(round(self.level * 100)), "pan": int(round(self.pan * 100)), "mute": self.mute,
                "solo": self.solo, "out_ch": self.out_ch, "programs": self.programs, "vel_vol": self.vel_vol,
                "has_state": bool(self.state_b64), "peak": int(min(self.peak, 1.5) * 100) if with_peak else 0,
                "fx": [{"name": f["name"], "bypass": f["bypass"]} for f in self.fx]}


# ===================================================================================================
#  the host / router / mixer
# ===================================================================================================
class PluginHost:
    dry = False

    def __init__(self, internal, cfg, app_dir, on_change=None):
        self.internal, self.cfg, self.app_dir = internal, cfg, app_dir
        self.on_change = on_change or (lambda: None)
        self.rate = int(cfg.get("vst_rate", 44100))
        self.block = int(cfg.get("vst_block", 512))
        self.device = cfg.get("vst_device")
        self.base_gain = float(cfg.get("vst_gain", 1.0))
        self.master = 1.0
        n = _clamp(int(cfg.get("plugin_slots", 8) or 8), 1, 16)
        self.slots = [Slot(i) for i in range(n)]
        self.mfx = []                                   # master inserts
        self.mbridge, self.mpeak = None, 0.0
        self.map = [()] * 16
        self.lock = threading.RLock()
        self.catalog = Catalog(app_dir, cfg)
        self.rack_path = os.path.join(app_dir, "rack.json")
        self.stream, self.stream_error = None, ""
        self.editors = set()                            # (slot index or "master", target) windows currently open
        self.closed = False
        self._prev = time.perf_counter()
        self._np = None
        self._wd = None
        # which engine plays the plugins: the C++ DXHost if it is there, else the Python helper processes
        global DXHOST_EXE
        pref = str(cfg.get("plugin_engine", "auto")).lower()
        self.dx_exe = find_dxhost(app_dir, cfg) if pref in ("auto", "dxhost") else None
        DXHOST_EXE = self.dx_exe
        self.native = self.dx_exe is not None
        self.engine_name = "dxhost" if self.native else "python"
        self.engine_note = ("DXHost.exe was not found: using the Python engine." if (pref == "dxhost" and not self.native) else "")
        self.client, self.client_lock = None, threading.RLock()
        self.latency_ms, self.audio_info = 0.0, {}
        self.dx_restarts = []

    # ---- the output interface the engine already uses ----------------------------------------------
    def _route(self, m, now):
        """Hands m to the slots that own its channel. Returns True if the internal sound should get it."""
        if not m:
            return False
        st = m[0]
        if st >= 0xF0:
            return True
        targets = self.map[st & 0x0F]
        if not targets:
            return True
        for s in targets:
            s.push(m, now)
        return False

    def send(self, m):
        if self._route(m, time.perf_counter()) and self.internal is not None:
            self.internal.send(m)

    def send_many(self, msgs):
        now, rest = time.perf_counter(), []
        for m in msgs:
            if self._route(m, now):
                rest.append(m)
        if rest and self.internal is not None:
            many = getattr(self.internal, "send_many", None)
            if many:
                many(rest)
            else:
                for m in rest:
                    self.internal.send(m)

    def set_gain(self, pct):
        self.master = _clamp(pct, 0, 100) / 100.0
        if self.native and self.client is not None and self.client.alive():
            self.client.send({"cmd": "master", "level": self.master}, lambda m: None)
        if self.internal is not None:
            self.internal.set_gain(pct)
        if pct <= 0:                                  # MUTE: also stop whatever is ringing in the plugins
            for s in self.slots:
                if s.active:
                    s.silence()

    def set_reverb(self, params):
        if hasattr(self.internal, "set_reverb"):
            self.internal.set_reverb(params)

    def efx(self, on):
        if hasattr(self.internal, "efx"):
            self.internal.efx(on)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self._capture_states()
        self.save_rack()
        try:
            if self.stream:
                self.stream.stop()
                self.stream.close()
        except Exception:
            pass
        self.stream = None
        for s in self.slots:
            if s.dev is not None:
                try:
                    s.silence()
                    s.dev.close()
                except Exception:
                    pass
            if s.bridge is not None:
                s.bridge.close()
        if self.mbridge is not None:
            self.mbridge.close()
        if self.client is not None:
            self.client.close()
        if self.internal is not None:
            self.internal.close()

    # ---- routing table ---------------------------------------------------------------------------------
    def _rebuild_map(self):
        old = self.map
        new = [[] for _ in range(16)]
        for s in self.slots:
            if s.active and not s.loading:
                for ch in s.channels:
                    new[ch].append(s)
        new = [tuple(x) for x in new]
        self.map = new
        if self.internal is not None:                 # a channel the slots just took over must stop on the internal sound
            for ch in range(16):
                if new[ch] and not old[ch]:
                    for cc in (123, 120):
                        self.internal.send(bytes([0xB0 | ch, cc, 0]))

    def set_channels(self, idx, channels):
        with self.lock:
            s = self.slots[idx]
            new = {int(c) for c in channels if 0 <= int(c) <= 15}
            removed = s.channels - new
            s.channels = new
            if removed and s.active:
                s.silence(sorted(removed))
            self._rebuild_map()
        self._changed()

    def toggle_part(self, idx, part, on=None):
        chans = dict(PARTS).get(part)
        if chans is None:
            raise ValueError("unknown part " + str(part))
        s = self.slots[idx]
        has = set(chans) <= s.channels
        on = (not has) if on is None else bool(on)
        self.set_channels(idx, (s.channels | set(chans)) if on else (s.channels - set(chans)))

    # ---- audio ---------------------------------------------------------------------------------------
    def _find_device(self, sd, name):
        if not name:
            return None
        for i, d in enumerate(sd.query_devices()):
            if d["max_output_channels"] > 0 and str(name).lower() in d["name"].lower():
                return i
        return None

    def _ensure_stream(self):
        if self.stream:
            return
        try:
            import numpy as np
            import sounddevice as sd
        except ImportError as err:
            self.stream_error = "Plugin hosting needs the 'pedalboard', 'sounddevice' and 'numpy' packages (%s)." % err
            raise RuntimeError(self.stream_error)
        self._np = np
        try:
            self._prev = time.perf_counter()
            self.stream = sd.OutputStream(samplerate=self.rate, blocksize=self.block, channels=2, dtype="float32",
                                          device=self._find_device(sd, self.device), callback=self._callback,
                                          latency="low")
            self.stream.start()
            self.stream_error = ""
        except Exception as err:
            self.stream = None
            self.stream_error = "Could not open the audio output: %s" % err
            raise RuntimeError(self.stream_error)
        self._start_watchdog()

    def _ensure_engine(self):
        if self.native:
            return self._native_client()
        return self._ensure_stream()

    def _start_watchdog(self):
        if self._wd is None:
            self._wd = threading.Thread(target=self._watchdog, daemon=True)
            self._wd.start()

    def _native_client(self):
        """The running DXHost (started, or restarted after a crash, with the audio device open)."""
        with self.client_lock:
            if self.client is not None and self.client.alive():
                return self.client
            restart = self.client is not None
            if restart:
                self.client.kill()
            try:
                client = DXClient(self.dx_exe, os.path.join(self.app_dir, "dxhost.log"))
                info = client.request({"cmd": "audio", "type": self.cfg.get("dx_audio_type", ""), "device": self.device or "",
                                       "rate": self.rate, "block": self.block}, timeout=30)
            except Exception as err:
                try:
                    client.kill()
                except Exception:
                    pass
                self.stream_error = "Plugin host: %s" % err
                raise RuntimeError(self.stream_error)
            self.client, self.audio_info, self.stream_error = client, info, ""
            client.send({"cmd": "master", "level": self.master}, lambda m: None)
            self.mbridge = None
            if restart and self.mfx:                                   # the master inserts come back with their sounds
                self.mbridge = NativeBridge(client, "master", True)
                for i, f in enumerate(self.mfx):
                    try:
                        client.request({"cmd": "fx_set", "slot": "master", "index": i, "path": f["path"], "name": f["name"],
                                        "state": f.get("state", ""), "bypass": f["bypass"]}, timeout=60)
                    except Exception as err:
                        self.stream_error = "Master insert '%s': %s" % (f["name"], err)
            self._start_watchdog()
            return client

    def _new_bridge(self, which, effects_only=False):
        if self.native:
            return NativeBridge(self._native_client(), which, effects_only)
        return Bridge(self.rate, self.block, effects_only=effects_only)

    def _push_mix(self, s):
        """DXHost engine: level / pan / mute / solo live in DXHost."""
        if self.native and s.bridge is not None and not s.bridge.dead:
            s.bridge.client.send({"cmd": "mix", "slot": s.idx, "level": s.level * self.base_gain, "pan": s.pan,
                                  "mute": s.mute, "solo": s.solo}, lambda m: None)

    def _poll_meters(self):
        c = self.client
        if c is None or not c.alive():
            return
        try:
            d = c.request({"cmd": "meters"}, timeout=1.5)
        except Exception:
            return
        peaks = d.get("slots", [])
        for s in self.slots:
            if s.idx < len(peaks):
                s.peak = max(float(peaks[s.idx]) * s.level, s.peak * 0.8)
        self.mpeak = max(float(d.get("master", 0.0)), self.mpeak * 0.8)
        self.latency_ms = float(d.get("latency_ms", 0.0))

    def _submit(self, s, prev, dur, frames):
        b = s.bridge
        if not b.try_finish():                           # last block still running: skip, keep the MIDI for the next one
            s.late += 1
            return False
        with s.qlock:
            batch, s.pending = s.pending, []
        top = dur * 0.999
        b.submit([(m, min(max(t - prev, 0.0), top)) for m, t in batch], frames)
        return True

    def _callback(self, outdata, frames, tinfo, status):
        np = self._np
        now = time.perf_counter()
        prev, self._prev = self._prev, now
        dur = frames / float(self.rate)
        outdata.fill(0)
        try:
            live = [s for s in self.slots if s.kind == "plugin" and s.bridge is not None and not s.loading
                    and not s.bridge.dead]
            sent = [s for s in live if self._submit(s, prev, dur, frames)]       # all helpers start together...
            deadline = now + dur * 0.85
            soloing = any(s.solo for s in live)
            for s in sent:                                                      # ...and are collected together
                a = s.bridge.collect(deadline - time.perf_counter())
                if a is None:
                    s.late += 1
                    continue
                s.late = 0
                g = 0.0 if (s.mute or (soloing and not s.solo)) else s.level * self.base_gain
                ang = (_clamp(s.pan, -1.0, 1.0) + 1.0) * math.pi / 4.0
                gl, gr = g * math.cos(ang) * 1.4142, g * math.sin(ang) * 1.4142
                pk = float(np.abs(a).max()) * g if g else 0.0
                s.peak = max(pk, s.peak * 0.995)
                outdata[:len(a), 0] += a[:, 0] * gl
                outdata[:len(a), 1] += a[:, 1] * gr
            mb = self.mbridge
            if mb is not None and not mb.dead and any(not f["bypass"] for f in self.mfx):    # the master inserts
                if mb.try_finish():
                    mb.submit([], frames, outdata)
                    a = mb.collect(deadline - time.perf_counter())
                    if a is not None:
                        outdata[:len(a)] = a
            if self.master != 1.0:
                outdata *= self.master
            self.mpeak = max(float(np.abs(outdata).max()), self.mpeak * 0.995)
            np.clip(outdata, -1.0, 1.0, out=outdata)
        except Exception as err:                       # never let the audio thread die
            outdata.fill(0)
            self.stream_error = "audio: %s" % err

    def _watchdog(self):
        """Notices a helper that died or hung: frees its channels (the internal sound takes over) and restarts it."""
        while not self.closed:
            time.sleep(0.5)
            if self.native:
                self._poll_meters()
                c = self.client
                if c is not None and not c.alive() and not self.closed:        # the C++ host itself is gone
                    now = time.time()
                    self.dx_restarts = [t for t in self.dx_restarts if now - t < 120]
                    if len(self.dx_restarts) < 3:
                        self.dx_restarts.append(now)
                        self.stream_error = "The plugin host stopped (a plugin probably crashed it): restarting it..."
                        threading.Thread(target=self._restart_native, daemon=True).start()
                    elif "keeps" not in self.stream_error:
                        self.stream_error = "The plugin host keeps crashing and was stopped. Plugins are off."
                        self._changed()
            for s in self.slots:
                try:
                    b = s.bridge
                    if b is None or s.loading:
                        continue
                    hung = s.late > 250 and b.alive()
                    if hung:
                        b.kill()
                    if b.dead or not b.proc.is_alive():
                        self._on_crash(s, "froze" if hung else "crashed")
                except Exception:
                    pass
            mb = self.mbridge
            if mb is not None and not self.native and not self.closed and (mb.dead or not mb.proc.is_alive()):
                self._master_crash()

    def _restart_native(self):
        try:
            self._native_client()
        except Exception:
            pass
        self._changed()

    def _on_crash(self, s, what):
        with self.lock:
            b, s.bridge = s.bridge, None
            s.late = 0
            self._rebuild_map()
        if b is not None:
            b.dead = True
            threading.Thread(target=b.close, daemon=True).start()      # frees its shared memory
        with s.qlock:
            s.pending = []                                              # the note that crashed it must not be replayed
        now = time.time()
        s.restarts = [t for t in s.restarts if now - t < 120]
        if len(s.restarts) < 2 and s.path:
            s.restarts.append(now)
            s.error = "%s %s: restarting it..." % (s.pname or "Plugin", what)
            self._changed()
            threading.Thread(target=self._load, args=(s.idx, s.path, s.pname or None, s.state_b64, None, False),
                             daemon=True).start()
        else:
            s.error = "%s %s again and was stopped. The internal sound plays its channels." % (s.pname or "Plugin", what)
            self._changed()

    def _master_crash(self):
        mb, self.mbridge = self.mbridge, None
        if mb is not None:
            mb.dead = True
            threading.Thread(target=mb.close, daemon=True).start()
        self.stream_error = "A master effect crashed and was switched off."
        self._changed()

    # ---- loading -------------------------------------------------------------------------------------
    def _changed(self):
        self.save_rack()
        try:
            self.on_change()
        except Exception:
            pass

    def _entry(self, path, name, want_instrument):
        """Finds (probing in a helper process if needed) the catalog entry and checks instrument/effect."""
        if not os.path.exists(path):
            raise RuntimeError("File not found: " + path)
        ent = self.catalog.lookup(path, name)
        if ent is None or ent["instrument"] is None:
            found = probe_in_child(path)
            with self.catalog.lock:
                self.catalog.found[path] = {"mtime": os.path.getmtime(path), "entries": found}
            self.catalog.save()
            ent = self.catalog.lookup(path, name) or self.catalog.lookup(path)
        if ent is None or ent["error"]:
            raise RuntimeError("That plugin could not be loaded%s" % ((": " + ent["error"]) if ent else "."))
        if want_instrument and not ent["instrument"]:
            raise RuntimeError("%s is an effect, not an instrument: it makes no sound from MIDI. "
                               "Add it as an INSERT instead." % ent["name"])
        if not want_instrument and ent["instrument"]:
            raise RuntimeError("%s is an instrument, not an effect. Load it into a slot instead." % ent["name"])
        return ent

    def load_async(self, idx, path, name=None, state_b64=None, then=None):
        s = self.slots[idx]
        if s.loading:
            raise ValueError("Slot %d is still loading." % (idx + 1))
        s.loading, s.error = True, ""
        self.on_change()
        threading.Thread(target=self._load, args=(idx, path, name, state_b64, then, True), daemon=True).start()

    def _load(self, idx, path, name, state_b64, then, fresh):
        s = self.slots[idx]
        s.loading = True
        bridge = None
        try:
            ent = self._entry(path, name, True)
            self._ensure_engine()
            if fresh:
                s.fx = []                                              # a different plugin starts without old inserts
            bridge = self._new_bridge(idx)
            st = state_b64 if state_b64 is not None else (s.state_b64 if s.path == path and s.pname == ent["name"] else "")
            bridge.request({"cmd": "load", "path": path, "name": ent["name"], "multi": ent.get("multi"), "state": st,
                            "clear_fx": bool(fresh)})
            for i, f in enumerate(list(s.fx)):                         # inserts come back too
                try:
                    bridge.request({"cmd": "fx_set", "index": i, "path": f["path"], "name": f["name"],
                                    "multi": f.get("multi"), "bypass": f["bypass"], "state": f.get("state", "")})
                except RuntimeError as err:
                    s.error = "Insert '%s': %s" % (f["name"], err)
            with self.lock:
                self._unload(s, replacing=True)
                s.kind, s.path, s.pname, s.port = "plugin", path, ent["name"], ""
                s.bridge, s.state_b64, s.late = bridge, st or "", 0
                if self.native:
                    s.sink = lambda m, c=bridge.client, i=idx: c.midi(i, m)
                    self._push_mix(s)
                bridge = None
                if not s.channels:                                     # a new slot starts on DRUMS only
                    s.channels = set(dict(PARTS)["DRUMS"])
                s.loading = False
                self._rebuild_map()
            if not s.error.startswith("Insert"):
                s.error = ""
        except Exception as err:
            s.error = str(err) or type(err).__name__
            if bridge is not None:
                if isinstance(bridge, NativeBridge):
                    bridge.closed = True                  # the strip may still hold the previous plugin: leave it alone
                else:
                    bridge.close()
        finally:
            s.loading = False
            self._changed()
        if then:
            then(s)

    def _unload(self, s, replacing=False):
        if s.active:
            s.silence()
        s.sink = None
        if s.dev is not None:
            try:
                s.dev.close()
            except Exception:
                pass
        b, s.bridge, s.dev = s.bridge, None, None
        if b is not None:
            if replacing and isinstance(b, NativeBridge):
                b.closed = True                           # the strip is about to hold the new plugin: do not unload it
            else:
                threading.Thread(target=b.close, daemon=True).start()
        with s.qlock:
            s.pending = []

    def clear(self, idx):
        with self.lock:
            s = self.slots[idx]
            self._unload(s)
            s.path = s.pname = s.port = s.state_b64 = s.error = ""
            s.channels, s.fx = set(), []
            self._rebuild_map()
        self._changed()

    def set_midi_port(self, idx, port):
        """Make a slot send to a MIDI output port instead of hosting a plugin."""
        import sty_play as sp
        devs = sp.midi_out_devices()
        num = next((i for i, n in enumerate(devs) if port.lower() in n.lower()), None)
        if num is None:
            raise ValueError("No MIDI output named '%s'. Available: %s" % (port, ", ".join(devs) or "(none)"))
        s = self.slots[idx]
        with self.lock:
            self._unload(s)
            try:
                s.dev = sp.MidiOut(False, num)
            except (SystemExit, Exception) as err:
                raise ValueError(str(err).splitlines()[0] if str(err) else "Could not open that MIDI port.")
            s.kind, s.port, s.path, s.pname, s.error, s.fx = "midi", devs[num], "", "", "", []
            if not s.channels:
                s.channels = set(dict(PARTS)["DRUMS"])
            self._rebuild_map()
        self._changed()

    # ---- inserts (effects) -----------------------------------------------------------------------------
    def _fxlist(self, which):
        return self.mfx if which == "master" else self.slots[int(which)].fx

    def fx_add(self, which, path, name=None, state=None, index=None, then=None):
        """Insert an effect plugin into a slot's chain, or the master chain (which = slot number or "master")."""
        lst = self._fxlist(which)
        if index is None:
            if len(lst) >= MAX_INSERTS:
                raise ValueError("A channel has room for %d inserts." % MAX_INSERTS)
            index = len(lst)
        if which != "master":
            s = self.slots[int(which)]
            if s.kind != "plugin" or s.bridge is None or s.loading:
                raise ValueError("Load an instrument into slot %d first." % (int(which) + 1))
        threading.Thread(target=self._fx_add, args=(which, path, name, state, index, then), daemon=True).start()

    def _fx_add(self, which, path, name, state, index, then):
        owner = self.mfx if which == "master" else None
        target = self.slots[int(which)] if which != "master" else None
        try:
            ent = self._entry(path, name, False)
            self._ensure_engine()
            if which == "master":
                if self.mbridge is None or self.mbridge.dead:
                    self.mbridge = self._new_bridge("master", True)
                bridge = self.mbridge
            else:
                bridge = target.bridge
                if bridge is None:
                    raise RuntimeError("The slot's plugin is not running.")
            bridge.request({"cmd": "fx_set", "index": index, "path": path, "name": ent["name"],
                            "multi": ent.get("multi"), "state": state or ""})
            rec = {"path": path, "name": ent["name"], "multi": ent.get("multi"), "bypass": False, "state": state or ""}
            lst = self.mfx if which == "master" else target.fx
            if index < len(lst):
                lst[index] = rec
            else:
                lst.append(rec)
            if target is not None:
                target.error = "" if not target.error.startswith("Insert") else target.error
        except Exception as err:
            msg = "Insert: " + (str(err) or type(err).__name__)
            if which == "master":
                self.stream_error = msg
            else:
                target.error = msg
        self._changed()
        if then:
            then()

    def fx_remove(self, which, index):
        lst = self._fxlist(which)
        index = int(index)
        if not 0 <= index < len(lst):
            raise ValueError("no such insert")
        bridge = self.mbridge if which == "master" else self.slots[int(which)].bridge
        if bridge is not None and not bridge.dead:
            bridge.request({"cmd": "fx_set", "index": index, "path": ""})
        del lst[index]
        self._changed()

    def fx_bypass(self, which, index, on):
        lst = self._fxlist(which)
        index = int(index)
        if not 0 <= index < len(lst):
            raise ValueError("no such insert")
        bridge = self.mbridge if which == "master" else self.slots[int(which)].bridge
        if bridge is not None and not bridge.dead:
            bridge.request({"cmd": "fx_bypass", "index": index, "on": bool(on)})
        lst[index]["bypass"] = bool(on)
        self._changed()

    # ---- the plugins' own saved sounds ----------------------------------------------------------------
    def _store_states(self, which, data):
        if which == "master":
            for f, st in zip(self.mfx, data.get("fx", [])):
                if st:
                    f["state"] = st
        else:
            s = self.slots[int(which)]
            if data.get("inst"):
                s.state_b64 = data["inst"]
            for f, st in zip(s.fx, data.get("fx", [])):
                if st:
                    f["state"] = st

    def save_state(self, idx):
        s = self.slots[idx]
        if s.bridge is None:
            raise ValueError("Slot %d has no plugin." % (idx + 1))
        self._store_states(idx, s.bridge.request({"cmd": "state"}, timeout=10))
        self._changed()

    def _capture_states(self):
        pairs = [(s.idx, s.bridge) for s in self.slots if s.bridge is not None and not s.bridge.dead]
        if self.mbridge is not None and not self.mbridge.dead:
            pairs.append(("master", self.mbridge))
        for which, b in pairs:
            try:
                self._store_states(which, b.request({"cmd": "state"}, timeout=3))
            except Exception:
                pass

    # ---- the plugin's own window (opens in the helper process) ------------------------------------------
    def open_editor(self, which, target="inst"):
        if which == "master":
            bridge = self.mbridge
        else:
            s = self.slots[int(which)]
            bridge = s.bridge
        if bridge is None or bridge.dead:
            raise ValueError("Nothing is loaded there.")
        key = (which, str(target))
        if key in self.editors:
            raise ValueError("That plugin window is already open.")
        self.editors.add(key)
        self.on_change()

        def done(res):
            self.editors.discard(key)
            if res.get("ok"):
                self._store_states(which, res.get("data", {}))             # what you set in the window is remembered
            elif which != "master" and bridge is self.slots[int(which)].bridge:
                self.slots[int(which)].error = "Window: " + str(res.get("error"))
            self._changed()

        bridge.send({"cmd": "editor", "target": target}, done)

    # ---- the rack file -------------------------------------------------------------------------------
    def save_rack(self):
        data = {"version": 3, "engine": self.engine_name, "slots": [], "master_fx": list(self.mfx)}
        for s in self.slots:
            if not (s.path or s.port or s.channels):
                continue
            data["slots"].append({"i": s.idx, "kind": s.kind, "path": s.path, "name": s.pname, "port": s.port,
                                  "channels": sorted(s.channels), "level": s.level, "pan": s.pan, "mute": s.mute,
                                  "solo": s.solo, "out_ch": s.out_ch, "programs": s.programs, "vel_vol": s.vel_vol,
                                  "state": s.state_b64, "fx": s.fx})
        try:
            tmp = self.rack_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1)
            os.replace(tmp, self.rack_path)
        except OSError:
            pass

    def restore_rack(self):
        """Reload the rack from rack.json (one background thread, one plugin at a time)."""
        try:
            with open(self.rack_path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        if data.get("engine", "python") != self.engine_name:          # saved sounds only fit the engine that saved them
            for d in data.get("slots", []):
                if isinstance(d, dict):
                    d["state"] = ""
                    for f in d.get("fx", []):
                        if isinstance(f, dict):
                            f["state"] = ""
            for f in data.get("master_fx", []):
                if isinstance(f, dict):
                    f["state"] = ""
        saved = [d for d in data.get("slots", []) if isinstance(d, dict) and 0 <= int(d.get("i", -1)) < len(self.slots)]
        for d in saved:
            s = self.slots[int(d["i"])]
            s.kind = d.get("kind", "plugin")
            s.path, s.pname, s.port = d.get("path", ""), d.get("name", ""), d.get("port", "")
            s.channels = {int(c) for c in d.get("channels", []) if 0 <= int(c) <= 15}
            s.level, s.mute = _clamp(float(d.get("level", 1.0)), 0.0, 1.5), bool(d.get("mute", False))
            s.pan, s.solo = _clamp(float(d.get("pan", 0.0)), -1.0, 1.0), bool(d.get("solo", False))
            s.out_ch, s.programs, s.vel_vol = int(d.get("out_ch", 0)), bool(d.get("programs")), bool(d.get("vel_vol"))
            s.state_b64 = d.get("state", "")
            s.fx = [f for f in d.get("fx", []) if isinstance(f, dict) and f.get("path")][:MAX_INSERTS]
        mfx = [f for f in data.get("master_fx", []) if isinstance(f, dict) and f.get("path")][:MAX_INSERTS]

        def run():
            for d in saved:
                s = self.slots[int(d["i"])]
                try:
                    if s.kind == "midi" and s.port:
                        self.set_midi_port(s.idx, s.port)
                    elif s.path:
                        self._load(s.idx, s.path, s.pname or None, s.state_b64, None, False)
                except Exception as err:
                    s.error = str(err)
                    s.loading = False
            if mfx:
                self.mfx = []
                for i, f in enumerate(mfx):
                    self._fx_add("master", f["path"], f.get("name"), f.get("state"), len(self.mfx), None)
                for i, f in enumerate(mfx):                                # keep bypass switches
                    if i < len(self.mfx) and f.get("bypass"):
                        try:
                            self.fx_bypass("master", i, True)
                        except Exception:
                            pass
            self._changed()

        for d in saved:                                                    # show them as "loading" straight away
            if self.slots[int(d["i"])].path:
                self.slots[int(d["i"])].loading = True
        threading.Thread(target=run, daemon=True).start()
        self.on_change()

    # ---- what the panel shows ------------------------------------------------------------------------
    def summary(self):
        return {"slots": [s.info() for s in self.slots], "scanning": self.catalog.scanning,
                "scan_done": self.catalog.done, "scan_total": self.catalog.total,
                "editors": [[w, t] for w, t in sorted(self.editors, key=str)],
                "audio_error": self.stream_error, "engine": self.engine_name, "engine_note": self.engine_note,
                "latency_ms": round(self.latency_ms, 1),
                "master": {"fx": [{"name": f["name"], "bypass": f["bypass"]} for f in self.mfx], "peak": 0}}

    def details(self):
        d = self.summary()                              # (the polled state carries no meters: they change every poll)
        d["slots"] = [s.info(with_peak=True) for s in self.slots]
        d["master"]["peak"] = int(min(self.mpeak, 1.5) * 100)
        d.update({"catalog": self.catalog.entries(), "folders": self.catalog.all_folders(),
                  "user_folders": list(self.catalog.folders), "parts": [n for n, _ in PARTS],
                  "rate": self.rate, "block": self.block, "max_inserts": MAX_INSERTS})
        try:
            import sty_play as sp
            d["midi_ports"] = sp.midi_out_devices()
        except Exception:
            d["midi_ports"] = []
        return d

    # ---- one entry point for the panel / the manager page --------------------------------------------
    def command(self, b):
        op = b.get("op")

        def slot():
            i = int(b.get("slot", -1))
            if not 0 <= i < len(self.slots):
                raise ValueError("bad slot")
            return i

        def which():
            return "master" if b.get("slot") == "master" else slot()

        if op == "scan":
            self.catalog.scan(force=bool(b.get("force")), on_progress=self.on_change)
        elif op == "addfolder":
            self.catalog.add_folder(b.get("path", ""))
            self.catalog.scan(on_progress=self.on_change)
        elif op == "removefolder":
            self.catalog.remove_folder(b.get("path", ""))
        elif op == "load":
            self.load_async(slot(), str(b.get("path", "")), b.get("name") or None)
        elif op == "clear":
            self.clear(slot())
        elif op == "editor":
            self.open_editor(which(), b.get("target", "inst"))
        elif op == "savestate":
            self.save_state(slot())
        elif op == "channels":
            self.set_channels(slot(), b.get("channels") or [])
        elif op == "part":
            self.toggle_part(slot(), str(b.get("part", "")), b.get("on"))
        elif op == "midiport":
            self.set_midi_port(slot(), str(b.get("port", "")))
        elif op == "fx_add":
            self.fx_add(which(), str(b.get("path", "")), b.get("name") or None)
        elif op == "fx_remove":
            self.fx_remove(which(), b.get("index"))
        elif op == "fx_bypass":
            self.fx_bypass(which(), b.get("index"), b.get("on"))
        elif op == "set":
            s = self.slots[slot()]
            if "level" in b:
                s.level = _clamp(float(b["level"]) / 100.0, 0.0, 1.5)
            if "pan" in b:
                s.pan = _clamp(float(b["pan"]) / 100.0, -1.0, 1.0)
            if "mute" in b:
                s.mute = bool(b["mute"])
                if s.mute and s.active:
                    s.silence()
            if "solo" in b:
                s.solo = bool(b["solo"])
            if "out_ch" in b:
                s.out_ch = _clamp(int(b["out_ch"]), 0, 16)
            if "programs" in b:
                s.programs = bool(b["programs"])
            if "vel_vol" in b:
                s.vel_vol = bool(b["vel_vol"])
            self._push_mix(s)
            self._changed()
        elif op in ("params", "setparam"):
            if not self.native:
                raise ValueError("The parameter panel needs the C++ engine (DXHost.exe).")
            w = which()
            bridge = self.mbridge if w == "master" else self.slots[w].bridge
            if bridge is None or bridge.dead:
                raise ValueError("Nothing is loaded there.")
            cmd = {"cmd": op, "target": str(b.get("target", "inst"))}
            if op == "setparam":
                cmd.update(index=int(b.get("index", -1)), value=float(b.get("value", 0.0)))
            data = bridge.request(cmd, timeout=10)
            return dict(data, ok=True)
        elif op == "panic":
            for s in self.slots:
                if s.active:
                    s.silence()
            if self.internal is not None:
                for ch in range(16):
                    self.internal.send(bytes([0xB0 | ch, 123, 0]))
        else:
            raise ValueError("unknown plugin command")
        return {"ok": True}
