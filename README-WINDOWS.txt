DX ARRANGER FOR WINDOWS (no Python needed)
===========================================
START:   double-click Start-DX-Arranger.bat   (or DX-Arranger.exe). The panel opens in its own window;
         close the window to quit. To use a browser tab instead, set "window": "browser" in config.json.
PHONE:   double-click Start-for-phone.bat -> it prints an address and a PIN for your phone/tablet.
         Allow Windows Firewall for PRIVATE networks when asked.

FIRST TIME
 * Windows may say "Windows protected your PC" (the program is not code-signed):
   click More info > Run anyway.
 * FluidSynth and a SoundFont are needed for the good sound. Open config.json (created on first
   run) and set "fluid_exe" and "soundfont" to your own files. Without them the Windows
   built-in synth is used.
 * Put .STY files in the Styles\P01..P16 folders, or import them from the panel.

Everything else works as described in README.txt.

USING A VST DRUM PLUGIN (e.g. MT Power Drum Kit) INSTEAD OF FLUIDSYNTH
 1. Install loopMIDI (free) and create one port (default name "loopMIDI Port").
 2. Open the plugin's standalone program, or a VST host (SAVIHost, Reaper...), and set its MIDI input to that port.
 3. In config.json set:  "synth": "midiout"   and   "midi_out_name": "loopMIDI"
    (the start-up window lists every MIDI output; use any part of the name you want).
 4. Start DX Arranger. EFX, reverb/chorus and master volume are then the plugin's job; MUTE still silences it.

PLAYING THE VST DIRECTLY (no FluidSynth, no loopMIDI)
 1. Copy MT-PowerDrumKit.vst3 next to DX-Arranger.exe (or put its full path in "vst_path").
 2. Run MT Power Drum Kit's standalone ONCE and point it at your sound content folder (it remembers this).
 3. config.json:  "synth": "vst"
 4. Start the app. The window prints "VST plugin ready", or a warning if the plugin makes no sound.
 If it crackles raise "vst_block" to 1024; if the beat feels late try 256.

PLUGINS & VST (host VST3 instruments and effects inside DX Arranger, laid out like FL Studio)
 * Two engines, chosen automatically ("plugin_engine" in config.json: "auto" | "dxhost" | "python"):
     C++ engine  DXHost.exe (next to the app, built from the dxhost folder with JUCE). All plugin audio, the mixer, the
                 inserts and the master run in C++: lower CPU, tighter timing, automatic PLUGIN DELAY COMPENSATION
                 (layered plugins stay in time), a PARAMETER PANEL for every plugin, and no Python plugin library needed.
     Python engine  used when DXHost.exe is missing. Each slot runs in its own helper process (pedalboard).
   The manager page shows which engine is running. Saved sounds belong to the engine that saved them.
 * Each slot is a MIXER CHANNEL:  instrument -> up to 4 INSERT effects -> level / pan / mute / solo / meter.
   All channels go to the MASTER channel (up to 4 inserts, then the master volume).
 * Crashes: with the Python engine only the crashing slot stops and restarts. With DXHost the whole plugin host restarts
   (about a second of silence) and every slot, insert and master insert comes back with its saved sound; a plugin that
   crashes DXHost three times in two minutes is left off. The arranger and the internal sound keep playing either way.
   Scanning a plugin and loading its info always happens in a throw-away process.
 * On the panel: MENU > PLUGINS & VST.  SLOT > LOAD PLUGIN / OPEN PLUGIN WINDOW / ROUTE PARTS / LEVEL / MUTE / SAVE SOUND.
   The last entry opens the full manager (http://127.0.0.1:8765/plugins): scan folders, per-channel routing, layering,
   pan / solo / meters, inserts per channel and on the master, plugin windows, Params panel, MIDI-port slots.
 * Installing a plugin: run its own installer. VST3 files normally land in C:\Program Files\Common Files\VST3, which is
   scanned automatically. Anywhere else: Add folder in the manager. A Plugins\ folder next to the .exe is scanned too.
 * Channels no slot takes keep playing on the normal sound. For plugins only, set "synth": "plugins" in config.json.
 * Bass / chords / pad only reach a plugin when "play ALL parts" is on (menu or manager). Default: drums only.
 * Instruments go in a slot, effects go in inserts. Only VST3 (no VST2 .dll, no CLAP).
 * Style program changes are NOT sent to plugins (a style cannot change your preset); the Programs button allows it.
   If a plugin ignores the VOL knobs, switch on "Volume via velocity" for that slot.
 * Plugin sounds are saved when you close a plugin window, press Save sound, and when the app closes.
 * Saved next to the app: rack.json (slots, inserts, master inserts, routing, sounds), plugins.json (scan), dxhost.log.
 * Audio driver (DXHost): "dx_audio_type" in config.json, e.g. "Windows Audio (Low Latency Mode)" or "DirectSound";
   "vst_device" picks the sound card by name, "vst_block" the buffer (smaller = less delay, more risk of crackle).
