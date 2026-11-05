"""
scheduling.py — read-only explanation of Timeshift's legacy cron file.

Only reads world-readable paths; no privilege needed. It explains
/etc/cron.d/timeshift-hourly and changes nothing.

That file is Timeshift's OWN behavior, driven by the schedule_daily/
weekly/monthly/hourly/boot flags in /etc/timeshift/timeshift.json. If any
flag is true, Timeshift recreates the file every time it runs at all,
including when Companion's backup helper invokes it. Confirmed on both
the Dell and a Samsung RF511 (2026-08-30): unticking every level in
Timeshift's Settings -> Schedule made Timeshift remove the file itself.

Companion used to offer a privileged "Fix Scheduling" action that renamed
the file. It was removed in 0.1.0~dev20: it could only act when every
level was off, and any later change there is Timeshift's to make. The
right place to change this is Timeshift's own Schedule tab.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

CRON_FILE_LEGACY = Path("/etc/cron.d/timeshift-hourly")
TIMESHIFT_CONFIG = Path("/etc/timeshift/timeshift.json")

SCHEDULE_KEYS = (
    "schedule_hourly",
    "schedule_daily",
    "schedule_weekly",
    "schedule_monthly",
    "schedule_boot",
)


@dataclass
class CronCheckResult:
    legacy_cron_present: bool
    schedule_enabled: Optional[bool]  # None if timeshift.json unreadable/missing
    detail: str


def _timeshift_schedule_enabled() -> Optional[bool]:
    """
    True if Timeshift's own config has any schedule level turned on.
    None if the config can't be read at all (e.g. Timeshift never
    configured yet): the caller can't tell either way and should say so.
    """
    try:
        data = json.loads(TIMESHIFT_CONFIG.read_text(encoding="utf-8"))
    except (FileNotFoundError, PermissionError, json.JSONDecodeError):
        return None
    return any(str(data.get(key, "false")).lower() == "true" for key in SCHEDULE_KEYS)


def check_cron_integrity() -> CronCheckResult:
    schedule_enabled = _timeshift_schedule_enabled()
    present = CRON_FILE_LEGACY.exists()

    if not present:
        detail = "No /etc/cron.d/timeshift-hourly file found — nothing to explain."
    elif schedule_enabled:
        detail = (
            "Timeshift's own Schedule settings have at least one level "
            "enabled (Daily/Weekly/Monthly/Hourly/Boot) — that's why "
            "/etc/cron.d/timeshift-hourly exists. This is expected Timeshift "
            "behavior, not a bug. If you want on-demand-only, disable all "
            "schedule levels in Timeshift itself (Settings → Schedule)."
        )
    elif schedule_enabled is None:
        detail = (
            "/etc/cron.d/timeshift-hourly exists, but Timeshift's settings "
            "couldn't be read, so Companion can't tell why. Check that "
            "Timeshift's first-run setup has been completed."
        )
    else:
        detail = (
            "/etc/cron.d/timeshift-hourly exists even though Timeshift's "
            "Schedule levels are all off. Timeshift manages this file itself: "
            "ticking a level and unticking it again in Timeshift's Settings → "
            "Schedule should make Timeshift remove it."
        )
    return CronCheckResult(present, schedule_enabled, detail)
