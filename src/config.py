"""
config.py — per-user settings for Timeshift On Demand.

XDG-compliant: reads/writes ~/.config/timeshift-on-demand/config.json
(respecting $XDG_CONFIG_HOME if set, same pattern backup_runner.py uses
for $XDG_DATA_HOME). The only settings are whether the login and resume
prompts are on. timeshift-on-demand-prompt reads this same file; it
treats only an explicit JSON false as "off", as here.

There is no backup-drive setting: backups go to the device Timeshift
itself is configured with (status.get_timeshift_backup_device_uuid()).
Older versions stored a "backup_drive_uuid" here; it is ignored, and
disappears from the file the next time a setting is saved.

No first-run wizard, by design: an absent config file just means the
defaults (both prompts on).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path


def _config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "timeshift-on-demand"


CONFIG_PATH = _config_dir() / "config.json"


@dataclass
class Settings:
    prompt_on_login: bool = True
    prompt_on_resume: bool = True


def load_settings() -> Settings:
    """Never raises — a missing or corrupt config file just means defaults."""
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return Settings()
    if not isinstance(data, dict):
        return Settings()
    return Settings(
        # A prompt silently going quiet is the worse mistake here, so
        # anything other than an explicit false leaves it on.
        prompt_on_login=data.get("prompt_on_login") is not False,
        prompt_on_resume=data.get("prompt_on_resume") is not False,
    )


def save_settings(settings: Settings) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(asdict(settings), indent=2) + "\n", encoding="utf-8"
    )


def update_settings(**changes) -> Settings:
    """Change only the named settings, keeping the rest of the file."""
    settings = replace(load_settings(), **changes)
    save_settings(settings)
    return settings
