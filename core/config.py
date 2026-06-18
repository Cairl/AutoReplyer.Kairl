"""
Configuration manager: auto-generation, field repair, atomic writes, hot-reload.
"""

import os
import json
import copy
import tempfile
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "config.json"

DEFAULT_CONFIG = {
    "reply": {
        "content": "收到",
        "delay_min": 1.0,
        "delay_max": 3.0,
        "scan_interval": 2.0
    },
    "groups": []
}


class Config:
    """Manages config.json with auto-generation, field repair, and atomic writes."""

    def __init__(self, path: Path = CONFIG_PATH):
        self.path = path
        self._last_mtime = 0.0
        self.data = {}
        self._load_or_generate()

    def _load_or_generate(self):
        if not self.path.exists():
            self.data = copy.deepcopy(DEFAULT_CONFIG)
            self._save()
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self.data = json.load(f)
        except (json.JSONDecodeError, IOError):
            self.data = copy.deepcopy(DEFAULT_CONFIG)
            self._save()
            return
        self._repair(self.data, DEFAULT_CONFIG)
        self._save()

    def _repair(self, target: dict, defaults: dict):
        """Fill missing fields from defaults without overwriting existing values."""
        changed = False
        for key, val in defaults.items():
            if key not in target:
                target[key] = copy.deepcopy(val)
                changed = True
            elif isinstance(val, dict) and isinstance(target[key], dict):
                self._repair(target[key], val)
        if changed:
            self._save()

    def _save(self):
        """Atomic write via temp file + rename."""
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=str(self.path.parent), suffix=".tmp"
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, str(self.path))
            self._last_mtime = self.path.stat().st_mtime
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def save(self):
        self._save()

    def hot_reload(self):
        """Reload if file changed externally."""
        if not self.path.exists():
            return
        mtime = self.path.stat().st_mtime
        if mtime > self._last_mtime:
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
                self._last_mtime = mtime
            except (json.JSONDecodeError, IOError):
                pass

    # ── Convenience accessors ──

    def get_reply(self, key: str, default=None):
        return self.data.get("reply", {}).get(key, default)

    def set_reply(self, key: str, value):
        self.data.setdefault("reply", {})[key] = value
        self._save()

    def get_groups(self) -> list:
        return self.data.get("groups", [])

    def add_group(self, name: str):
        groups = self.data.setdefault("groups", [])
        groups.append({
            "name": name,
            "enabled": True,
            "message_region": None,
            "reply_region": None
        })
        self._save()

    def remove_group(self, index: int):
        groups = self.get_groups()
        if 0 <= index < len(groups):
            groups.pop(index)
            self._save()

    def set_group_region(self, index: int, region_type: str, region: dict):
        """Set message_region or reply_region for a group."""
        groups = self.get_groups()
        if 0 <= index < len(groups):
            groups[index][region_type] = region
            self._save()

    def toggle_group(self, index: int):
        groups = self.get_groups()
        if 0 <= index < len(groups):
            groups[index]["enabled"] = not groups[index]["enabled"]
            self._save()
