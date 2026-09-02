"""
config.py — per-user settings for Timeshift On Demand.

XDG-compliant: reads/writes ~/.config/timeshift-on-demand/config.json
(respecting $XDG_CONFIG_HOME if set, same pattern backup_runner.py uses
for $XDG_DATA_HOME). Currently the only setting is which backup drive
(by filesystem UUID, not mount point — mount points move, UUIDs don't)
the Backup tab should wait for before running a backup.

No first-run wizard, per PROJECT.md's "Drive config" decision: an absent
config file just means "not configured yet" — BackupRunner handles a
None uuid by reporting that clearly instead of guessing or blocking
startup. The Settings tab (app.py) is the only way to set or change it.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


def _config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "timeshift-on-demand"


CONFIG_PATH = _config_dir() / "config.json"


@dataclass
class Settings:
    backup_drive_uuid: Optional[str] = None


def load_settings() -> Settings:
    """Never raises — a missing or corrupt config file just means defaults."""
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return Settings()
    return Settings(backup_drive_uuid=data.get("backup_drive_uuid") or None)


def save_settings(settings: Settings) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(asdict(settings), indent=2) + "\n", encoding="utf-8"
    )
