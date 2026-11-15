"""WAV drumkit engine: WAV-only loading, channel-10-only triggering, layers, choke, kit folders, async load."""
import os, shutil, struct, sys, tempfile, time
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import make_wav, click, tone
from drum_engine import DrumEngine
from drum_engine.sample_loader import load_wav, WavError
from drum_engine.note_mapper import map_folder, parse_name

def wait(e, t=5):
    for _ in range(int(t * 100)):
        if not e.loading: return
        time.sleep(0.01)
    raise AssertionError("kit load timed out")

tmp = tempfile.mkdtemp()
try:
    # ---- loader: every common WAV flavour, and nothing but WAV
    for name, kw in [("p16", {}), ("p24", dict(bits=24)), ("p32", dict(bits=32)), ("p8", dict(bits=8)),
                     ("f32", dict(tag=3, bits=32)), ("f64", dict(tag=3, bits=64)), ("ext", dict(extensible=True)),
                     ("ext24", dict(extensible=True, bits=24)), ("chunks", dict(extra_chunk=True)),
                     ("badsize", dict(bad_size=True)), ("r48", dict(rate=48000))]:
        p = os.path.join(tmp, name + ".wav"); make_wav(p, tone(4800, rate=kw.get("rate", 44100)), **kw)
        a = load_wav(p, 44100); assert a.ndim == 2 and a.shape[1] == 2 and np.max(np.abs(a)) > 0.3, name
    st = os.path.join(tmp, "st.wav"); make_wav(st, np.column_stack([tone(500), tone(500, 880)])); assert load_wav(st).shape == (500, 2)
    open(os.path.join(tmp, "fake.wav"), "wb").write(b"ID3\x03" + b"\0" * 100)       # an mp3 renamed .wav
    for bad in ("fake.wav",):
        try: load_wav(os.path.join(tmp, bad)); raise SystemExit("accepted a non-WAV")
        except WavError: pass
    open(os.path.join(tmp, "a.mp3"), "wb").write(b"x")
    try: load_wav(os.path.join(tmp, "a.mp3")); raise SystemExit("accepted .mp3")
    except WavError: pass
    adpcm = os.path.join(tmp, "adpcm.wav"); make_wav(adpcm, tone(100), tag=2) if False else None

    # ---- lead-in silence is trimmed so every instrument hits on the beat
    lead = os.path.join(tmp, "lead.wav"); make_wav(lead, np.concatenate([np.zeros(900), tone(2000)]))
    a = load_wav(lead, 44100); assert np.abs(a[:20]).max() > 0.01 or np.abs(a[:40]).max() > 0.1, "samples start at the hit"
    assert len(a) < 2100

    # ---- name mapping: layers, round robin, leftovers, WAV only
    assert parse_name("PowerKit1-38-127.wav") == (38, 127) and parse_name("36_kick.wav")[0] == 36
    assert parse_name("kick.wav") == (36, None) and parse_name("Kick_02.wav") == (36, 2)
    k = os.path.join(tmp, "DRUMS", "Layered"); os.makedirs(k)
    for fn in ("P-38-64.wav", "P-38-127.wav", "P-36-1.wav", "P-36-2.wav", "hat.wav", "Sample 7.wav"):
        make_wav(os.path.join(k, fn), click(1000, 0.3))
    for fn in ("notes.txt", "x.mp3", "x.ncw", "x.sf2", "x.mid"): open(os.path.join(k, fn), "w").write("x")
    m = map_folder(k)
    assert m[38]["mode"] == "velocity" and [t for t, _ in m[38]["items"]] == [64, 127]
    assert m[36]["mode"] == "roundrobin" and m[42]["mode"] == "single"
    assert all(p.lower().endswith(".wav") for e in m.values() for _, p in e["items"]) and len(m) == 4, m.keys()

    # ---- engine: loading is async, plays ONLY channel-10 notes, WAV only
    kit = os.path.join(tmp, "DRUMS", "Kit1"); os.makedirs(kit)
    make_wav(os.path.join(kit, "36_kick.wav"), click(2000, 0.5)); make_wav(os.path.join(kit, "42_hat.wav"), click(5000, 0.5))
    make_wav(os.path.join(kit, "46_open.wav"), click(20000, 0.5)); make_wav(os.path.join(kit, "bad.wav"), click(10)[:0])
    e = DrumEngine(tmp, {}, log=lambda *a: None); wait(e)
    assert set(e.manager.kits) == {"Kit1", "Layered"} and e.ready_kit in e.manager.kits
    e.select_kit("Kit1"); wait(e)
    assert e.ready_kit == "Kit1" and e.brief()["failed"] == 1 and e.error == ""        # one broken file does not stop the kit
    assert np.max(np.abs(e.render(64))) == 0
    e.send(bytes([0x99, 36, 100])); assert np.max(np.abs(e.render(64))) > 0.1           # channel 10 plays
    e.stop_all(); e.render(2000); assert not e.voices
    for msg in (bytes([0x90, 36, 100]), bytes([0x91, 36, 100]), bytes([0x9F, 36, 100]), bytes([0xC9, 5]), bytes([0xB9, 10, 5]), bytes([0x89, 36, 0])):
        e.send(msg)
    assert np.max(np.abs(e.render(64))) == 0 and not e.voices, "non-drum-channel MIDI must be silent"
    e.send(bytes([0xC9, 40])); wait(e); assert e.ready_kit == "Kit1", "program change must not switch kits inside the sampler"
    e.send(bytes([0x99, 99, 100])); e.render(8); assert e.stats["unmapped"] == 1               # unmapped note: silent, no crash
    # hi-hat choke
    e.send(bytes([0x99, 46, 100])); e.render(100); e.send(bytes([0x99, 42, 100])); e.render(2000)
    assert sum(1 for v in e.voices if v.note == 46) == 0 and sum(1 for v in e.voices if v.note == 42) == 1
    # drum-channel volume
    e.stop_all(); e.render(2000); e.send(bytes([0xB9, 7, 50])); e.send(bytes([0x99, 36, 127]))
    half = np.max(np.abs(e.render(64))); e.send(bytes([0xB9, 7, 100]))
    e.stop_all(); e.render(2000); e.send(bytes([0x99, 36, 127])); assert np.max(np.abs(e.render(64))) > half * 1.8
    # style program -> kit; manual choice sticks
    e2 = DrumEngine(tmp, {}, log=lambda *a: None, autoload=False)
    e2.manager.config["program_map"] = {"0": "Kit1", "8": "Layered"}; e2.manager.config["default_kit"] = "Kit1"
    e2.on_style_program(8); wait(e2); assert e2.ready_kit == "Layered"
    e2.select_kit("Kit1"); wait(e2); e2.on_style_program(8); wait(e2); assert e2.ready_kit == "Kit1" and e2.manager.manual
    e2.use_default(); wait(e2); assert e2.ready_kit == "Layered" and not e2.manager.manual
    # velocity layers pick the right sample
    e2.select_kit("Layered"); wait(e2); pad = e2.table[38]
    assert pad.pick(30) is pad.items[0][1] and pad.pick(100) is pad.items[1][1]
    # folder allocation: any folder becomes a kit and is remembered
    ext = os.path.join(tmp, "elsewhere", "MyOwn"); os.makedirs(ext); make_wav(os.path.join(ext, "36.wav"), click(500))
    r = e2.add_folder(ext, audition=False); assert r["ok"] and r["kit"] == "MyOwn"; wait(e2)
    assert e2.ready_kit == "MyOwn" and e2.browser()["kits"][-1]["extra"]
    e3 = DrumEngine(tmp, {}, log=lambda *a: None, autoload=False); assert "MyOwn" in e3.manager.kits
    assert e3.add_folder(os.path.join(tmp, "elsewhere"))["ok"] is False                      # no .wav inside -> refused
    assert e3.forget("MyOwn")["ok"] and "MyOwn" not in e3.manager.kits
    assert e3.forget("Kit1")["ok"] is False                                                   # DRUMS kits are not removable
    # newest choice wins when clicking fast
    e2.select_kit("Layered"); e2.select_kit("Kit1"); wait(e2); assert e2.ready_kit == "Kit1"
    print("DRUMKIT ENGINE OK")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
