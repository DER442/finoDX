"""Multipad layout: P1 = 54 | P2 = 84 | P3, P4 = 54, 70, 82. A loaded file fills the pads; pads play the style's WAV kit."""
import os, sys, struct, tempfile, shutil, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import numpy as np
from helpers import make_wav, tone
import multipad as mp
from drum_engine import DrumEngine

def vlq(n):
    out = [n & 0x7F]
    while n > 0x7F:
        n >>= 7; out.append((n & 0x7F) | 0x80)
    return bytes(reversed(out))

def smf(path, notes, ppq=480):
    """notes: [(tick, note)] on channel 10, 100 ticks long."""
    ev = sorted([(t, 0x99, n, 100) for t, n in notes] + [(t + 100, 0x99, n, 0) for t, n in notes])
    tr, last = b"", 0
    for t, st, n, v in ev:
        tr += vlq(t - last) + bytes([st, n, v]); last = t
    tr += vlq(1920 - last) + b"\xff\x2f\x00"
    open(path, "wb").write(b"MThd" + struct.pack(">IHHH", 6, 0, 1, ppq) + b"MTrk" + struct.pack(">I", len(tr)) + tr)

tmp = tempfile.mkdtemp()
try:
    kit = os.path.join(tmp, "DRUMS", "Kit"); os.makedirs(kit)
    for fn in ("36_kick.wav", "tambourine.wav", "maracas.wav", "shaker.wav", "belltree.wav"):
        make_wav(os.path.join(kit, fn), tone(3000))
    e = DrumEngine(tmp, {}, log=lambda *a: None)
    for _ in range(500):
        if not e.loading: break
        time.sleep(0.01)
    assert sorted(i for i, p in enumerate(e.table) if p) == [36, 54, 70, 82, 84], "kit maps tambourine/maracas/shaker/bell tree"
    folder = os.path.join(tmp, "Multipad"); os.makedirs(folder)
    # one phrase with all the instruments plus a kick that is NOT on any pad
    smf(os.path.join(folder, "perc.mid"), [(0, 54), (480, 54), (0, 84), (960, 70), (240, 82), (0, 36)])
    layout = mp.parse_layout(mp.DEFAULT_LAYOUT)
    pads = mp.PadEngine(lambda: e, lambda: None, lambda: 120.0, folder, os.path.join(tmp, "pads.json"), repeat=False, layout=layout)
    pads.load("perc.mid", 1)
    info = pads.info(); print([(i["name"], i["loaded"]) for i in info])
    assert all(i["loaded"] for i in info), "all four buttons loaded (they light blue)"
    notes = lambda n: sorted({m[1] for _, m in pads.pads[n]["events"] if m[0] & 0xF0 == 0x90 and m[2] > 0})
    assert notes(1) == [54] and notes(2) == [84] and notes(3) == [54, 70, 82] and notes(4) == [54, 70, 82], [notes(n) for n in (1, 2, 3, 4)]
    assert all(m[0] & 0x0F == 9 for n in (1, 2, 3, 4) for _, m in pads.pads[n]["events"]), "everything on drum channel 10"
    # pad 2 sounds the bell tree of the loaded kit, and only that
    e.stats.clear(); pads.trigger(2); time.sleep(0.25)
    assert e.stats["triggered"] == 1 and np.max(np.abs(e.render(256))) > 0, dict(e.stats)
    pads.stop_all()
    # pad 2 is a ONE-SHOT: it plays once and stops by itself; the other pads loop
    loopy = mp.PadEngine(lambda: e, lambda: None, lambda: 240.0, folder, os.path.join(tmp, "pl.json"), repeat=False,   # even with repeat off
                         layout=layout, oneshot=mp.DEFAULT_ONESHOT)
    loopy.load("perc.mid", 1)
    e.stats.clear(); loopy.trigger(2); time.sleep(0.4); assert loopy.live[2], "still sounding"
    time.sleep(2.2); assert e.stats["triggered"] == 1 and not loopy.live[2], ("one-shot must play once and stop", dict(e.stats), loopy.live)
    e.stats.clear(); loopy.trigger(1); time.sleep(2.6); assert e.stats["triggered"] >= 4 and loopy.live[1], "pad 1 loops"
    e.stats.clear(); loopy.trigger(3); time.sleep(2.6); assert e.stats["triggered"] >= 8 and loopy.live[3], "pad 3 loops"
    e.stats.clear(); loopy.trigger(4); time.sleep(2.6); assert loopy.live[4], "pad 4 loops"
    loopy.stop_all()
    # a file with none of the layout notes is refused with a clear message
    smf(os.path.join(folder, "kick.mid"), [(0, 36), (480, 38)])
    try: pads.load("kick.mid", 1); raise SystemExit("accepted a file with no layout notes")
    except ValueError as err: print("refused:", err)
    # no layout = old behaviour
    pads2 = mp.PadEngine(lambda: e, lambda: None, lambda: 120.0, folder, os.path.join(tmp, "p2.json"), repeat=False, layout=None)
    pads2.load("kick.mid", 2); assert pads2.info()[1]["loaded"] and not pads2.info()[0]["loaded"]
    # remembered across a restart
    pads3 = mp.PadEngine(lambda: e, lambda: None, lambda: 120.0, folder, os.path.join(tmp, "pads.json"), repeat=False, layout=layout)
    assert all(i["loaded"] for i in pads3.info())
    print("MULTIPAD LAYOUT OK")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
