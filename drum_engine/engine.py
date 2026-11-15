"""Independent WAV drumkit engine: receives the style's MIDI and plays WAV samples. Nothing else.

* Only MIDI channel 10 note-on messages trigger sound (other channels, program changes, SoundFonts, synths: ignored).
* Only .wav files are ever loaded (see sample_loader).
* Kits load in the background: the old kit keeps playing until the new one is ready.
"""
from __future__ import annotations

import collections
import itertools
import os
import threading
from typing import Optional

import numpy as np

from .drumkit_manager import DrumkitManager
from .note_mapper import all_paths, describe, map_folder
from .sample_loader import SampleBank

HAT_GROUP = (42, 44, 46)          # closed / pedal / open hi-hat cut each other off
FADE = 0.025                      # seconds: how fast a choked or stopped voice fades out


class _Pad:
    """One MIDI note: one sample, velocity layers, or round-robin hits."""
    __slots__ = ("mode", "items", "rr")

    def __init__(self, mode, items):
        self.mode, self.items, self.rr = mode, items, 0

    def pick(self, vel):
        if self.mode == "velocity":
            for top, data in self.items:
                if vel <= top:
                    return data
            return self.items[-1][1]
        if self.mode == "roundrobin":
            data = self.items[self.rr % len(self.items)][1]
            self.rr += 1
            return data
        return self.items[0][1]


class _Voice:
    __slots__ = ("data", "pos", "gain", "note", "fade_left", "fade_len")

    def __init__(self, data, gain, note):
        self.data, self.pos, self.gain, self.note = data, 0, gain, note
        self.fade_left = None
        self.fade_len = 1


class DrumEngine:
    """Same send / send_many / set_gain / close surface as the other DX outputs (used as the style's synth)."""

    def __init__(self, app_dir: str, cfg: Optional[dict] = None, log=print, autoload: bool = True):
        cfg = cfg or {}
        self.log = log or (lambda *a, **k: None)
        self.app_dir = app_dir
        self.rate = int(cfg.get("sampler_rate") or cfg.get("vst_rate") or 44100)
        self.block = int(cfg.get("sampler_block") or 128)      # 128 frames = 2.9 ms: a hit is never more than that late
        self.device = cfg.get("sampler_device") or cfg.get("vst_device")
        self.polyphony = int(cfg.get("sampler_polyphony", 64))
        self.master = float(cfg.get("sampler_master", 1.0))
        self.channel = (int(cfg.get("drum_channel", 10)) - 1) & 0x0F

        self.manager = DrumkitManager(app_dir)
        self.bank = SampleBank(self.rate)
        self.note_map = {}
        self.table = [None] * 128
        self.voices = []
        self.lock = threading.RLock()
        self.stats = collections.Counter()
        self.error = ""
        self.kit_errors = []
        self.ready_kit = None            # kit whose samples are in the table right now
        self.loading = None              # {"kit", "done", "total"} while a kit is being read
        self._token = itertools.count(1)
        self._current = 0
        self._threads = []
        self.stream = None
        self._started = False
        self.gain = 1.0
        self.chan_gain = 1.0             # CC 7 of the drum channel (the VOL knob)
        self.dry = False

        if autoload and self.manager.kits:
            kid = self.manager.config.get("default_kit")
            if kid not in self.manager.kits:
                kid = next(iter(self.manager.kits))
            self.manager.selected = kid
            self.load_selected()

    # ---- kit loading ---------------------------------------------------
    def load_selected(self, wait: bool = False, audition: bool = False) -> dict:
        kit = self.manager.selected
        folder = self.manager.current_folder()
        if not folder:
            self.error = "No drumkit selected"
            return {"ok": False, "error": self.error}
        token = next(self._token)
        self._current = token
        self.loading = {"kit": kit, "done": 0, "total": 0}
        t = threading.Thread(target=self._load_worker, args=(kit, folder, token, audition), daemon=True)
        self._threads.append(t)
        t.start()
        if wait:
            t.join()
        return {"ok": True, "loading": kit}

    def _load_worker(self, kit, folder, token, audition):
        try:
            note_map = map_folder(folder)
            paths = all_paths(note_map)
            self.loading = {"kit": kit, "done": 0, "total": len(paths)}
            for i, p in enumerate(paths, 1):
                if token != self._current:
                    return                                  # a newer choice replaced this one
                self.bank.get(p)
                self.loading = {"kit": kit, "done": i, "total": len(paths)}
            if token != self._current:
                return
            table, ok, fail = [None] * 128, 0, 0
            for note, e in note_map.items():
                items = [(top, self.bank.get(p)) for top, p in e["items"]]
                items = [(top, d) for top, d in items if d is not None]
                if items:
                    table[note] = _Pad(e["mode"], items)
            ok = sum(1 for p in paths if self.bank.get(p) is not None)
            fail = len(paths) - ok
            errs = [f"{os.path.basename(p)}: {self.bank.errors.get(os.path.abspath(p), '?')}"
                    for p in paths if self.bank.get(p) is None]
            with self.lock:
                self.note_map, self.table = note_map, table     # voices still sounding keep their old samples
                self.ready_kit, self.error, self.kit_errors = kit, "", errs
            self.bank.prune(paths)
            msg = f"Kit '{kit}': {ok} samples ready" + (f" ({fail} failed)" if fail else "")
            self.log(msg)
            self.stats["kits_loaded"] += 1
            if audition:
                self.audition()
        except Exception as e:
            if token == self._current:
                self.error = f"drumkit load failed: {e}"
                self.log("!", self.error)
        finally:
            if token == self._current:
                self.loading = None

    def select_kit(self, kit_id: str, manual: bool = True, audition: bool = False) -> dict:
        if kit_id not in self.manager.kits:
            return {"ok": False, "error": f"Unknown kit: {kit_id}"}
        if manual:
            self.manager.select_manual(kit_id)
        else:
            self.manager.selected, self.manager.manual = kit_id, False
        return self.load_selected(audition=audition)

    def on_style_program(self, program: Optional[int]) -> dict:
        """Called by the app when a style is loaded."""
        prev = self.manager.selected
        kit = self.manager.on_style_loaded(program)
        if kit and (kit != prev or kit != self.ready_kit) and not self.loading:
            return self.load_selected()
        return {"ok": True}

    def use_default(self, audition: bool = False) -> dict:
        kit = self.manager.select_default()
        return self.load_selected(audition=audition) if kit else {"ok": False, "error": "No default kit"}

    def reload_kits(self) -> dict:
        self.manager.reload()
        if self.manager.selected not in self.manager.kits:
            self.manager.selected = next(iter(self.manager.kits), None)
            self.manager.manual = False
        return self.load_selected() if self.manager.selected else {"ok": True, "message": "No kits found"}

    def add_folder(self, path: str, audition: bool = True) -> dict:
        kid = self.manager.add_folder(path)
        if kid is None:
            return {"ok": False, "error": "That folder has no .wav files"}
        r = self.select_kit(kid, manual=True, audition=audition)
        r["kit"] = kid
        return r

    def forget(self, kit_id: str) -> dict:
        if not self.manager.forget(kit_id):
            return {"ok": False, "error": "Only added folders can be removed (kits in DRUMS are folders there)"}
        return self.reload_kits()

    def audition(self):
        """Kick, snare, hat: lets you hear a kit you just loaded."""
        for i, (note, vel) in enumerate(((36, 110), (38, 105), (42, 95), (46, 90))):
            threading.Timer(0.22 * i, self.note_on, args=(note, vel)).start()

    # ---- MIDI ----------------------------------------------------------
    def note_on(self, note: int, velocity: int = 100):
        if not (0 <= note <= 127):
            return
        self.stats["note_on"] += 1
        pad = self.table[note]
        if pad is None:
            self.stats["unmapped"] += 1
            return
        vel = max(1, min(127, velocity))
        data = pad.pick(vel)
        gain = (vel / 127.0) ** 1.2 * self.master
        with self.lock:
            if note in HAT_GROUP:
                n = int(FADE * self.rate)
                for v in self.voices:
                    if v.note in HAT_GROUP and v.fade_left is None:
                        v.fade_left, v.fade_len = n, n
            if len(self.voices) >= self.polyphony:
                self.voices.pop(0)
                self.stats["stolen"] += 1
            self.voices.append(_Voice(data, gain, note))
            self.stats["triggered"] += 1

    def stop_all(self):
        n = int(FADE * self.rate)
        with self.lock:
            for v in self.voices:
                if v.fade_left is None:
                    v.fade_left, v.fade_len = n, n

    def send(self, m: bytes, timestamp=None):
        """Only channel-10 notes and the drum channel's volume / sound-off are used."""
        if not m or len(m) < 2 or (m[0] & 0x0F) != self.channel:
            return
        cmd = m[0] & 0xF0
        if cmd == 0x90 and len(m) >= 3 and m[2] > 0:
            self.note_on(m[1] & 0x7F, m[2] & 0x7F)
        elif cmd == 0xB0 and len(m) >= 3:
            if m[1] == 7:
                self.chan_gain = min(1.5, (m[2] & 0x7F) / 100.0)
            elif m[1] == 120:
                self.stop_all()
        # note-off, program change, other controllers: ignored (drum hits are one-shots)

    def send_many(self, messages):
        for item in messages:
            if isinstance(item, (bytes, bytearray)):
                self.send(bytes(item))
            elif isinstance(item, (list, tuple)) and item:
                self.send(bytes(item[-1]) if isinstance(item[-1], (bytes, bytearray)) else bytes(item))

    def set_gain(self, pct):
        try:
            v = float(pct)
            self.gain = max(0.0, min(2.0, v / 100.0 if v > 2 else v))
        except (TypeError, ValueError):
            pass
        if self.gain == 0.0:
            self.stop_all()

    # ---- audio ---------------------------------------------------------
    def render(self, frames: int) -> np.ndarray:
        out = np.zeros((frames, 2), dtype=np.float32)
        with self.lock:
            alive = []
            for v in self.voices:
                remaining = len(v.data) - v.pos
                if remaining <= 0:
                    continue
                n = min(frames, remaining)
                if v.fade_left is not None:
                    k = min(n, v.fade_left)
                    g0 = v.fade_left / v.fade_len
                    g1 = (v.fade_left - k) / v.fade_len
                    ramp = np.linspace(g0, g1, k, endpoint=False, dtype=np.float32)[:, None]
                    out[:k] += v.data[v.pos:v.pos + k] * (v.gain * ramp)
                    v.pos += k
                    v.fade_left -= k
                    if v.fade_left > 0 and v.pos < len(v.data):
                        alive.append(v)
                    continue
                out[:n] += v.data[v.pos:v.pos + n] * v.gain
                v.pos += n
                if v.pos < len(v.data):
                    alive.append(v)
            self.voices = alive
        g = self.gain * self.chan_gain
        if g != 1.0:
            out *= g
        np.clip(out, -1.0, 1.0, out=out)
        return out

    def start(self) -> bool:
        if self._started and self.stream is not None:
            return True
        try:
            import sounddevice as sd
            dev = None
            if self.device:
                for i, d in enumerate(sd.query_devices()):
                    if self.device.lower() in d["name"].lower() and d["max_output_channels"] > 0:
                        dev = i
                        break

            def callback(outdata, frames, time_info, status):
                try:
                    outdata[:] = self.render(frames)
                except Exception:
                    outdata[:] = 0

            self.stream = sd.OutputStream(samplerate=self.rate, blocksize=self.block, device=dev, channels=2,
                                          dtype="float32", callback=callback, latency="low")
            self.stream.start()
            self._started = True
            return True
        except Exception as e:
            self.error = f"No audio output: {e}"
            self.log("!", self.error)
            return False

    def make_output(self, main=True):
        if main and not self.start():
            raise RuntimeError(self.error or "no audio output")
        return self

    def close(self):
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None
        self._started = False

    def shutdown(self):
        self.close()

    # ---- API / UI state ------------------------------------------------
    def brief(self) -> dict:
        m = self.manager
        return {"selected": m.selected, "ready": self.ready_kit, "manual": m.manual, "loading": self.loading,
                "mapped": sum(1 for x in self.table if x is not None), "failed": len(self.kit_errors), "error": self.error,
                "folder": m.current_folder(), "audio_running": self._started}

    def browser(self) -> dict:
        m = self.manager
        kits = []
        for k in m.list_kits():
            k["active"] = (k["id"] == self.ready_kit)
            kits.append(k)
        return {"root": m.samples_root(), "root_label": m.folder_label(m.samples_root()), "kits": kits,
                "labels": [k["name"].upper() for k in kits], **self.brief()}

    def state(self) -> dict:
        st = self.browser()
        st.update({"style_program": self.manager.style_program, "style_default_kit": self.manager.style_default_kit,
                   "stats": dict(self.stats), "kit_errors": list(self.kit_errors),
                   "notes": describe(self.note_map) if self.note_map else []})
        return st

    def command(self, body: dict) -> dict:
        op = body.get("op")
        audition = bool(body.get("audition"))
        if op in (None, "state"):
            return self.state()
        if op == "reload":
            return self.reload_kits()
        if op == "select":
            return self.select_kit(body.get("id") or body.get("kit") or "", manual=True, audition=audition)
        if op == "use_default":
            return self.use_default(audition=audition)
        if op == "forget":
            return self.forget(str(body.get("id", "")))
        if op == "preview":
            self.note_on(int(body.get("note", 36)), int(body.get("vel", 100)))
            return {"ok": True}
        return {"error": f"Unknown op: {op}"}
