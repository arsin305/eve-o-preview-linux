"""Configuration load/save with one-time migration from the old app name."""
import json, shutil
from pathlib import Path as _Path

_OLD_CONFIG = _Path.home() / ".config" / "eve-o-preview-linux" / "config.json"

class Config:
    def __init__(self):
        self.config_dir = _Path.home() / ".config" / "podsight"
        self.config_file = self.config_dir / "config.json"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        # One-time migration from the pre-rename config location. The old
        # file is copied, not moved, so downgrading remains possible.
        if not self.config_file.exists() and _OLD_CONFIG.exists():
            try:
                shutil.copy2(_OLD_CONFIG, self.config_file)
                print(f"[podsight] Migrated settings from {_OLD_CONFIG}")
            except Exception as e:
                print(f"[podsight] Config migration failed: {e}")
        self.default_config = {
            "thumbnail_width": 320,
            "thumbnail_height": 200,
            "opacity": 0.95,
            "always_on_top": True,
            "hide_active_client": False,
            "zoom_on_hover": True,
            "zoom_factor": 1.25,
            "show_overlay": True,
            "refresh_fps": 10,  # FPS instead of period
            "active_border_color": "#00FF00",  # Neon green default
            "thumbnail_positions": {}
        }
        self.settings = self.load()

    def load(self):
        try:
            if self.config_file.exists():
                with open(self.config_file, "r") as f:
                    data = json.load(f)
                merged = self.default_config.copy()
                merged.update(data)
                return merged
        except Exception as e:
            print("Config load error:", e)
        return self.default_config.copy()

    def save(self):
        try:
            with open(self.config_file, "w") as f:
                json.dump(self.settings, f, indent=2)
        except Exception as e:
            print("Config save error:", e)
