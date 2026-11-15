"""Multi Pad started alone, then the beat starts: the pad is cut and queued back in sync on the beat's bar line.
A RESET (the beat grid moves) re-queues it the same way."""
import os, sys, time, tempfile, shutil
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import multipad as mp, sty_play as sp

tmp = tempfile.mkdtemp()
try:
    hits, t0 = [], [0.0]
    class Out:
        dry = False
        def send(self, m): self.send_many([m])
        def send_many(self, ms):
            for m in ms:
                if (m[0] & 0xF0) == 0x90 and m[2] > 0: hits.append(round(time.perf_counter() - t0[0], 2))
    out = Out()
    sync = {}
    pads = mp.PadEngine(lambda: out, lambda: (sync["clock"], sync["quarter"]) if sync.get("clock") else None,
                        lambda: 120.0, tmp, os.path.join(tmp, "pads.json"), repeat=True,
                        get_grid=lambda: (sync.get("bar"), sync.get("epoch", 0)))
    # a 4-beat pad (2 s at 120 BPM) with one note on its first beat
    pads.pads[1] = {"name": "T", "kind": "midi", "beats": 4, "events": [(0.0, bytes([0x91, 60, 100]))], "setup": [], "file": "t", "index": 0}
    t0[0] = time.perf_counter()
    pads.trigger(1)                                   # plays alone: notes at ~0.02, 2.02, 4.02 ...
    time.sleep(1.3)
    assert pads.live[1] and any(abs(h - 0.02) < 0.05 for h in hits), hits
    # the beat starts at 1.5 s: its bar 1 beat 1 is at 1.5 s; bars are 2 s long
    t_style = time.perf_counter() + 0.2
    sync.update(clock=sp.TempoClock(120.0, 120.0, t_style), quarter=0.5, bar=2.0, epoch=0)
    time.sleep(2.6)
    ts = t_style - t0[0]
    print("style bar 1 at", round(ts, 2), "hits", hits)
    assert not any(abs(h - 2.02) < 0.09 for h in hits), "the free-running loop must have been killed"
    assert any(abs(h - ts) < 0.09 for h in hits), ("pad queued back onto the style's bar line", hits)
    assert any(abs(h - (ts + 2.0)) < 0.09 for h in hits), ("and keeps looping on the bar grid", hits)
    # RESET: the grid is re-anchored "now" and the epoch changes: the pad restarts with the new bar 1
    t_reset = time.perf_counter()
    sync["clock"].s = (t_reset, 0.0, sync["clock"].s[2]); sync["epoch"] = 1
    time.sleep(2.4)
    tr = t_reset - t0[0]
    print("reset at", round(tr, 2), "hits", hits)
    assert any(abs(h - tr) < 0.09 for h in hits), ("pad restarts on the reset bar 1", hits)
    assert any(abs(h - (tr + 2.0)) < 0.09 for h in hits), hits
    # stop: silence
    pads.stop_all(); n = len(hits); time.sleep(0.6); assert len(hits) == n and not pads.live[1]
    print("PAD RESYNC OK")
finally:
    pads.stop_all()
    shutil.rmtree(tmp, ignore_errors=True)
