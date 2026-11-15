"""Discover drumkit folders (each kit = one folder of .wav files) and resolve style program -> kit."""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

from .note_mapper import is_wav

DEFAULT_CONFIG = {
    "samples_dir": "DRUMS",
    "default_kit": None,
    "keep_manual_on_style_load": True,      # a kit you chose by hand stays when a new style is loaded
    "kit_folders": [],                      # extra kit folders allocated from anywhere on the PC
    "program_map": {str(p): "PowerKit1" for p in (0, 8, 16, 24, 25, 32, 40, 48, 56)},
}


def count_wavs(folder: str) -> int:
    try:
        return sum(1 for f in os.listdir(folder) if is_wav(f) and os.path.isfile(os.path.join(folder, f)))
    except OSError:
        return 0


class DrumkitManager:
    def __init__(self, app_dir: str, config_path: Optional[str] = None):
        self.app_dir = app_dir
        self.config_path = config_path or os.path.join(app_dir, "drumkit_config.json")
        self.config = self._load_config()
        self.kits: Dict[str, dict] = {}
        self.selected: Optional[str] = None
        self.manual = False
        self.style_program: Optional[int] = None
        self.style_default_kit: Optional[str] = None
        self.reload()

    def _load_config(self) -> dict:
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        try:
            with open(self.config_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                cfg.update(data)
                if isinstance(data.get("program_map"), dict):
                    cfg["program_map"] = {str(k): v for k, v in data["program_map"].items()}
                if not isinstance(cfg.get("kit_folders"), list):
                    cfg["kit_folders"] = []
        except (OSError, ValueError):
            pass
        return cfg

    def save_config(self):
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2)
        except OSError:
            pass

    def samples_root(self) -> str:
        root = self.config.get("samples_dir") or "DRUMS"
        return root if os.path.isabs(root) else os.path.join(self.app_dir, root)

    def folder_label(self, folder: str) -> str:
        """Short path for the LCD: relative to the app when inside it, else the full path."""
        try:
            rel = os.path.relpath(folder, self.app_dir)
            if not rel.startswith(".."):
                return rel
        except ValueError:
            pass
        return folder

    def reload(self) -> List[dict]:
        self.kits.clear()

        def add(name, folder, extra):
            n, k = name, 2
            while n in self.kits:
                n, k = f"{name} ({k})", k + 1
            self.kits[n] = {"id": n, "name": n, "folder": folder, "files": count_wavs(folder),
                            "extra": extra, "label": self.folder_label(folder)}

        root = self.samples_root()
        if os.path.isdir(root):
            for name in sorted(os.listdir(root), key=str.lower):
                folder = os.path.join(root, name)
                if os.path.isdir(folder) and count_wavs(folder):
                    add(name, folder, False)
        have = {os.path.normcase(os.path.abspath(k["folder"])) for k in self.kits.values()}
        for path in self.config.get("kit_folders", []):
            ap = os.path.abspath(path)
            if os.path.normcase(ap) in have or not os.path.isdir(ap) or not count_wavs(ap):
                continue
            add(os.path.basename(ap.rstrip("\\/")) or ap, ap, True)
        return self.list_kits()

    def add_folder(self, path: str) -> Optional[str]:
        """Allocate any folder of .wav files as a kit. Returns its kit id, or None if it has no .wav files."""
        ap = os.path.abspath(path)
        if not os.path.isdir(ap) or not count_wavs(ap):
            return None
        folders = self.config.setdefault("kit_folders", [])
        if not any(os.path.normcase(os.path.abspath(f)) == os.path.normcase(ap) for f in folders):
            folders.append(ap)
            self.save_config()
        self.reload()
        for kid, k in self.kits.items():
            if os.path.normcase(os.path.abspath(k["folder"])) == os.path.normcase(ap):
                return kid
        return None

    def forget(self, kit_id: str) -> bool:
        k = self.kits.get(kit_id)
        if not k or not k["extra"]:
            return False
        ap = os.path.normcase(os.path.abspath(k["folder"]))
        self.config["kit_folders"] = [f for f in self.config.get("kit_folders", [])
                                      if os.path.normcase(os.path.abspath(f)) != ap]
        self.save_config()
        self.reload()
        return True

    def list_kits(self) -> List[dict]:
        return [dict(k) for k in self.kits.values()]

    def resolve_program(self, program: Optional[int]) -> Optional[str]:
        default = self.config.get("default_kit")
        fallback = default if default in self.kits else next(iter(self.kits), None)
        if program is None:
            return fallback
        key = str(int(program) & 0x7F)
        name = self.config.get("program_map", {}).get(key)
        if name and name in self.kits:
            return name
        return fallback

    def on_style_loaded(self, program: Optional[int]) -> Optional[str]:
        """Style default kit, unless a kit was chosen by hand (and keep_manual_on_style_load is on)."""
        self.style_program = program
        self.style_default_kit = self.resolve_program(program)
        if self.manual and self.config.get("keep_manual_on_style_load", True) and self.selected in self.kits:
            return self.selected
        self.manual = False
        if self.style_default_kit:
            self.selected = self.style_default_kit
        return self.selected

    def select_manual(self, kit_id: str) -> bool:
        if kit_id not in self.kits:
            return False
        self.selected, self.manual = kit_id, True
        return True

    def select_default(self) -> Optional[str]:
        self.manual = False
        kit = self.style_default_kit or self.resolve_program(self.style_program)
        if kit in self.kits:
            self.selected = kit
        return self.selected

    def current_folder(self) -> Optional[str]:
        return self.kits[self.selected]["folder"] if self.selected in self.kits else None
