DX ARRANGER – MIDI ONLY (drum engine removed)
=============================================

What changed
------------
* The built-in drum engine package (drum_engine/) is gone.
* The /drums manager page and /api/drums endpoints are gone.
* Default config "synth" is "null" → no internal sound.

What still works
----------------
* Style loading and section playback (Main A/B/C/D, fills, intros, endings)
* drums_only mode still builds MIDI from channels 9+10
* sty_play.build(...) still returns timed MIDI messages
* Panel, tempo, multipads, VST/plugin host paths (if you set synth to vst)

How to connect a NEW drum engine later
--------------------------------------
1. Style MIDI is produced in Engine when a section plays.
2. Messages go to self.out.send() / send_many().
3. Point "synth" at a new backend, or inject a custom output that
   implements send(bytes) / send_many(...) / close().

Example offline (same as we used for testing):
    ppq, events = sty_play.load("file.STY")
    sections, _ = sty_play.find_sections(events, ppq)
    start, end = sections["Main D"]
    to_sec = sty_play.make_clock(events, ppq)
    msgs, duration = sty_play.build(
        events, start, end, to_sec,
        with9=True, only=None, drums=True, kit=None
    )
    # msgs = [(time_sec, midi_bytes), ...]  → feed your new sampler

PowerKit WAVs remain outside this project (PowerKit1_WAVs.zip) for the new engine.
