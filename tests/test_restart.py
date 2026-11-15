"""RESET: queued - on the next beat the playing section starts again from bar 1, beat 1; the beat grid stays exact."""
import os, sys, time, tempfile, shutil
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import make_sty
import sty_play as sp

tmp = tempfile.mkdtemp()
try:
    # Main A = 2 bars: kick (36) on bar 1 beat 1, snare (38) on bar 1 beat 3, hat (42) on bar 2 beat 1
    sty = make_sty(os.path.join(tmp, "t.sty"), [("Main A", 2, [(0, 10, 36, 100, 50), (960, 10, 38, 100, 50), (1920, 10, 42, 100, 50)])])
    ppq, events = sp.load(sty); secs, (s0, s1) = sp.find_sections(events, ppq); to_sec = sp.make_clock(events, ppq)
    cache = {}
    def get(sec):
        if sec not in cache: cache[sec] = sp.build(events, *secs[sec], to_sec, False, None, True, None)
        return cache[sec]
    hits, t0 = [], [0.0]
    class Out:
        dry = False
        def send(self, m): self.send_many([m])
        def send_many(self, ms):
            for m in ms:
                if m[0] == 0x99 and m[2] > 0: hits.append((round(time.perf_counter() - t0[0], 2), m[1]))
    script = [(0.75, ("restart",)), (4.9, "q")]          # 0.75 s = in the middle of beat 2: the reset waits for the next beat (1.0 s)
    class Keys:
        def pop(self):
            now = time.perf_counter() - t0[0]; out = []
            while script and now >= script[0][0]: out.append(script.pop(0)[1])
            return out
    q = 60.0 / 120; bar = q * 4
    sync = {}
    t0[0] = time.perf_counter() + 0.2          # run_live starts its clock 0.2 s from now
    try: sp.run_live(Out(), get, secs, q, bar, 120.0, 120.0, "Main A", [0.0, 0, 0], set(), keys=Keys(), sync=sync)
    except KeyboardInterrupt: pass
    print(hits)
    kicks = [t for t, n in hits if n == 36]
    assert abs(kicks[0]) < 0.08, hits                               # first kick on the first beat
    assert abs(kicks[1] - 1.0) < 0.07, ("reset is queued to the NEXT beat (1.0 s), not at the press (0.75 s)", hits)
    snares = [t for t, n in hits if n == 38]
    assert abs((snares[0] - kicks[1]) - 1.0) < 0.08, ("beat 3 is 1.0 s after the restarted beat 1", hits)
    hats = [t for t, n in hits if n == 42]
    assert any(abs(h - (kicks[1] + 2.0)) < 0.05 for h in hats), ("bar 2 follows 2 s after the restart", hits)
    c = sync["clock"]; assert abs(c.s[1]) < 5, "clock re-based"
    print("RESTART OK")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
