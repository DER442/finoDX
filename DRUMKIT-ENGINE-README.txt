DX-ARRANGER - WAV DRUMKIT ENGINE
================================

STYLE (.STY) -> MIDI on channel 10 -> drum engine -> your .WAV samples -> sound card

What it plays
  * ONLY .wav files. mp3, ncw, sf2, mid and every other file are ignored (a file that is not really a WAV is
    refused). PCM 8/16/24/32-bit, float, "extensible" WAVs, any sample rate and mono/stereo are read.
  * ONLY channel-10 note-on messages make a sound. Other channels, program changes and controllers never
    trigger a sample, and there is no fall-back to the Windows synth: if the audio device cannot open, the
    panel shows "Drum engine: ..." and stays silent.
  * Drum channel volume (VOL-1) and the master volume work. Closed/pedal/open hi-hats cut each other off.

Kits and folders
  * One kit = one folder of .wav files:   DRUMS\<KitName>\*.wav
  * Any other folder can be a kit too:    MENU > STYLES > DRUMKITS > + ADD KIT FOLDER
    (remembered in drumkit_config.json "kit_folders"; right-click an added kit to remove it from the list)
  * File names pick the note:   36_kick.wav  038-snare.wav  kick.wav  hihat.wav  PowerKit1-38-127.wav
    Several files for one note: -38-64, -38-127 = velocity layers; kick_01, kick_02 = round robin.
    Files with no recognisable note fill the free notes upward from 35.

On the panel (LCD)  - works like the P buttons for beats
  MENU > STYLES > DRUMKITS shows the kits. The status line shows the highlighted kit's folder and file count,
  a dot marks the kit that is playing. ENTER loads it (the old kit keeps playing until the new one is ready;
  progress shows on the status line). When the beat is stopped you hear a short kick / snare / hat.
  "AUTO (STYLE KIT)" lets each style choose its kit again (drumkit_config.json "program_map").
  A kit you chose by hand stays when you load a new style ("keep_manual_on_style_load": false = old behaviour).

config.json
  "synth": "drums"   (default)        "window": "auto" | "webview" | "app" | "browser"

Needs: pip install numpy sounddevice   (already inside the Windows build)
API:   GET /api/drumfolder   POST /api/drumkit {op: state|select|use_default|allocate|forget|reload|preview}
Tests: python tests/test_drumkit_engine.py   (tests/test_drum_output.py and test_app_integration.py are from an older engine)

Multipad
  Pad buttons play ONLY these drum notes (config.json "multipad_layout", null = no filter):
    P-1 = F#3 (54) | P-2 = C6 (84) | P-3 and P-4 = F#3 (54), A#4 (70), A#5 (82)
  A loaded MIDI/.pad file fills every pad whose notes it contains (a file with markers = one phrase per pad).
  The pads play through the drum engine, so they use the same WAV kit as the style (tambourine = 54,
  maracas = 70, shaker = 82, bell tree = 84: name the wav files e.g. tambourine.wav, belltree.wav or 54.wav).
  Lamps: light blue = a file is loaded on that pad, orange = playing.
  Pad 2 (bell tree) does NOT loop: it plays once and its lamp goes back to blue by itself. The other pads loop
  until stopped. Change it in config.json: "multipad_oneshot": [2]  (e.g. [] = every pad loops).
