"""Map WAV filenames in a kit folder to MIDI notes (no renames). Only .wav files are considered.

Recognised names:  36_kick.wav, 038-snare.wav, kick.wav, PowerKit1-38-127.wav, Snare_38_64.wav ...
Several files for one note become VELOCITY LAYERS (the number after the note looks like a velocity: the
largest is 64 or more, e.g. -38-127, -38-64) or ROUND-ROBIN hits (e.g. kick_01, kick_02).
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Tuple

GM_NAME_TO_NOTE = {
    "kick": 36, "bassdrum": 36, "bd": 36, "kick1": 36, "kick2": 35,
    "snare": 38, "sd": 38, "snare1": 38, "snare2": 40, "rim": 37, "sidestick": 37,
    "clap": 39, "handclap": 39,
    "tom": 45, "lowtom": 45, "midtom": 47, "hightom": 50, "floor": 41, "floortom": 41,
    "hat": 42, "hihat": 42, "closedhat": 42, "chh": 42, "closed": 42,
    "openhat": 46, "ohh": 46, "open": 46, "pedalhat": 44,
    "crash": 49, "crash1": 49, "crash2": 57,
    "ride": 51, "ride1": 51, "ride2": 59, "ridebell": 53,
    "china": 52, "splash": 55, "tamb": 54, "tambourine": 54,
    "cowbell": 56, "cabasa": 69, "maracas": 70, "clave": 75, "claves": 75,
    "woodblock": 76, "conga": 63, "bongo": 60, "timbale": 65,
    "shaker": 82, "belltree": 84, "bell": 84, "chimes": 84, "triangle": 81, "guiro": 73,
}

GM_NOTE_TO_NAME = {
    35: "Kick 2", 36: "Kick 1", 37: "Side Stick", 38: "Snare 1", 39: "Hand Clap",
    40: "Snare 2", 41: "Low Floor Tom", 42: "Closed Hi-Hat", 43: "High Floor Tom",
    44: "Pedal Hi-Hat", 45: "Low Tom", 46: "Open Hi-Hat", 47: "Low-Mid Tom",
    48: "High-Mid Tom", 49: "Crash Cymbal 1", 50: "High Tom", 51: "Ride Cymbal 1",
    52: "Chinese Cymbal", 53: "Ride Bell", 54: "Tambourine", 55: "Splash Cymbal",
    56: "Cowbell", 57: "Crash Cymbal 2", 59: "Ride Cymbal 2", 60: "High Bongo",
    61: "Low Bongo", 62: "Mute High Conga", 63: "Open High Conga", 64: "Low Conga",
    69: "Cabasa", 70: "Maracas", 75: "Claves", 76: "High Wood Block",
    84: "Bell Tree", 80: "Mute Triangle", 81: "Open Triangle", 82: "Shaker",
}


def is_wav(name: str) -> bool:
    return name.lower().endswith(".wav")


def parse_name(filename: str) -> Tuple[Optional[int], Optional[int]]:
    """-> (midi note or None, layer number or None) from a file name."""
    base = os.path.splitext(os.path.basename(filename))[0].lower()
    base = base.replace(" ", "_")
    tokens = [t for t in re.split(r"[_\-\.]+", base) if t]
    nums = [(i, int(t)) for i, t in enumerate(tokens) if t.isdigit()]
    note = idx = None
    if tokens and tokens[0].isdigit() and 0 <= int(tokens[0]) <= 127:      # 36_kick
        note, idx = int(tokens[0]), 0
    else:
        for i, n in nums:                                                  # PowerKit1-38-127
            if 27 <= n <= 87:
                note, idx = n, i
                break
        if note is None:
            for i, t in enumerate(tokens):                                 # kick / closed_hat
                if t in GM_NAME_TO_NOTE:
                    note, idx = GM_NAME_TO_NOTE[t], i
                    break
                if i + 1 < len(tokens) and t + tokens[i + 1] in GM_NAME_TO_NOTE:
                    note, idx = GM_NAME_TO_NOTE[t + tokens[i + 1]], i + 1
                    break
    if note is None:
        return None, None
    layer = next((n for i, n in nums if i > idx and 1 <= n <= 127), None)
    return note, layer


def guess_note(filename: str) -> Optional[int]:
    return parse_name(filename)[0]


def map_folder(folder: str) -> Dict[int, dict]:
    """Return {note: {"mode": single|velocity|roundrobin, "items": [(max_velocity, path), ...]}}.
    Files with no recognisable note fill the free notes upward from 35 (sorted order)."""
    folder = os.path.abspath(folder)
    if not os.path.isdir(folder):
        raise FileNotFoundError(folder)
    wavs = sorted((f for f in os.listdir(folder) if is_wav(f) and os.path.isfile(os.path.join(folder, f))),
                  key=str.lower)
    if not wavs:
        raise ValueError(f"No .wav files in {folder}")
    found: Dict[int, List[Tuple[Optional[int], str]]] = {}
    unmatched: List[str] = []
    for name in wavs:
        path = os.path.join(folder, name)
        note, layer = parse_name(name)
        if note is None:
            unmatched.append(path)
        else:
            found.setdefault(note, []).append((layer, path))
    out: Dict[int, dict] = {}
    for note, items in found.items():
        if len(items) == 1:
            out[note] = {"mode": "single", "items": [(127, items[0][1])]}
            continue
        ids = [l for l, _ in items]
        if None not in ids and len(set(ids)) == len(ids) and max(ids) >= 64:
            items = sorted(items, key=lambda x: x[0])
            out[note] = {"mode": "velocity", "items": [(l, p) for l, p in items]}
        else:
            items = sorted(items, key=lambda x: (x[0] is None, x[0] or 0, x[1].lower()))
            out[note] = {"mode": "roundrobin", "items": [(127, p) for _, p in items]}
    n = 35
    for path in unmatched:
        while n in out and n <= 127:
            n += 1
        if n > 127:
            break
        out[n] = {"mode": "single", "items": [(127, path)]}
        n += 1
    return out


def all_paths(note_map: Dict[int, dict]) -> List[str]:
    seen, res = set(), []
    for note in sorted(note_map):
        for _, p in note_map[note]["items"]:
            if p not in seen:
                seen.add(p)
                res.append(p)
    return res


def describe(note_map: Dict[int, dict]) -> List[dict]:
    rows = []
    for note in sorted(note_map):
        e = note_map[note]
        rows.append({
            "note": note,
            "label": GM_NOTE_TO_NAME.get(note, f"Note {note}"),
            "mode": e["mode"],
            "layers": len(e["items"]),
            "file": os.path.basename(e["items"][0][1]),
        })
    return rows
