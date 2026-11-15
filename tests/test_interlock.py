"""Pressing FILL again while a fill plays chains one more fill after it, on the bar line (interlocking fills)."""
import os, sys, time, tempfile, shutil
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import make_sty
import sty_play as sp

def run(presses, stop_at):
    tmp = tempfile.mkdtemp()
    try:
        sty = make_sty(os.path.join(tmp, "t.sty"), [("Main A", 1, [(0, 10, 36, 100, 50)]), ("Fill In AA", 1, [(0, 10, 49, 100, 50)])])
        ppq, events = sp.load(sty); secs, _ = sp.find_sections(events, ppq); to_sec = sp.make_clock(events, ppq)
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
        script = sorted(presses + [(stop_at, "q")], key=lambda x: x[0])
        class Keys:
            def pop(self):
                now = time.perf_counter() - t0[0]; out = []
                while script and now >= script[0][0]: out.append(script.pop(0)[1])
                return out
        t0[0] = time.perf_counter() + 0.2
        try: sp.run_live(Out(), get, secs, 0.5, 2.0, 120.0, 120.0, "Main A", [0.0, 0, 0], set(), keys=Keys(), auto_fill=lambda: False)
        except KeyboardInterrupt: pass
        return hits
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

# one press during the Main: fill from the next beat, then back to Main
h = run([(0.1, ("fill",))], 5.0); print(h)
assert [t for t, n in h if n == 49] == [], "a fill from beat 2 has no note at its bar start"      # (its only note is at beat 1)
assert (2.0, 36) in h or any(n == 36 and 1.9 < t < 2.1 for t, n in h), h
# many presses while the fill plays: 3 more fills, each on a bar line (2.0, 4.0, 6.0), then Main at 8.0
h = run([(0.1, ("fill",)), (0.8, ("fill",)), (1.0, ("fill",)), (1.2, ("fill",))], 9.0); print(h)
fills = [t for t, n in h if n == 49]
assert len(fills) == 3 and all(abs(t - e) < 0.08 for t, e in zip(fills, (2.0, 4.0, 6.0))), h
assert any(n == 36 and abs(t - 8.0) < 0.08 for t, n in h), h
# pressing a Main button (auto fill off) during the chain does not break it: the chain finishes, then that Main plays
h = run([(0.1, ("fill",)), (0.8, ("fill",)), (1.0, ("fill",)), (1.3, ("main", "A"))], 6.0); print(h)
f = [t for t, n in h if n == 49]
assert len(f) == 2 and all(abs(t - e) < 0.08 for t, e in zip(f, (2.0, 4.0))), h     # on the bar lines, within timer jitter
print("INTERLOCK OK")
