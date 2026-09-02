"""
scheduling.py — cron-integrity check + fix.

Read side (check_cron_integrity) is unchanged in spirit from
check-timeshift-cron.sh — it only reads world-readable paths, no
privilege needed. Fix side goes through a single `pkexec` call to
Companion's own bundled cron-fix helper
(packaging/helpers/timeshift-on-demand-cronfix-helper), resolved via the
io.github.11dash11.timeshiftondemand.cronfix polkit action — see packaging/README.md
for the invocation contract — instead of four separate `sudo` commands.

Deliberately does NOT re-enable timeshift-backup.timer. That unit was
intentionally removed (see
projects/script-consolidation/docs/decisions-log.md, item 12: a
sanity-check script ran for real instead of as a dry run, and the
deletion was reviewed and kept because on-demand is the actual desired
mechanism) — resurrecting it would regress a deliberate decision, not
fix anything. See PROJECT.md, "Maintenance tab — Fix Scheduling".

REDESIGNED 2026-08-30 after real-world testing on both the Dell and a
Samsung RF511 exposed a flawed premise: /etc/cron.d/timeshift-hourly
reappearing is not a mystery or an attack — it's Timeshift's OWN
internal behavior, driven entirely by its own schedule_daily/weekly/
monthly/hourly/boot config flags in /etc/timeshift/timeshift.json (also
world-readable, no privilege needed to check). If ANY of those flags is
true, Timeshift recreates that file every time it runs at all —
including when Companion's own backup/list helpers invoke it — so
disabling the file while scheduling is still enabled is a losing,
pointless fight, not a fix. Confirmed directly: both machines had
schedule_daily/weekly/monthly = true (Timeshift's own setup-wizard
default), and on the Dell, unticking all three in Timeshift's own
Settings -> Schedule tab made Timeshift remove the cron file itself
immediately — no "fix" action needed or possible from outside Timeshift.
check_cron_integrity() now reads those flags first and reports honestly
when the file's presence is expected Timeshift behavior; fix_scheduling()
refuses to spend a pkexec prompt on an action that would just get undone.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

CRON_FILE_DISABLED = Path("/etc/cron.d/timeshift-hourly.disabled")
CRON_FILE_LEGACY = Path("/etc/cron.d/timeshift-hourly")
HASH_FILE = Path("/var/lib/timeshift-cron.hash")
TIMESHIFT_CONFIG = Path("/etc/timeshift/timeshift.json")

CRONFIX_HELPER = "/usr/lib/timeshift-on-demand/timeshift-on-demand-cronfix-helper"

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
    hash_changed: Optional[bool]  # None if disabled-file / hash-file missing
    schedule_enabled: Optional[bool]  # None if timeshift.json unreadable/missing
    detail: str


def _sha256_of(path: Path) -> Optional[str]:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except (FileNotFoundError, PermissionError):
        return None


def _timeshift_schedule_enabled() -> Optional[bool]:
    """
    True if Timeshift's own config has any schedule level turned on.
    /etc/timeshift/timeshift.json is world-readable (644) — confirmed
    directly, no privilege needed. None if the config can't be read at
    all (e.g. Timeshift never configured yet), in which case the caller
    can't tell either way and should say so rather than guessing.
    """
    try:
        data = json.loads(TIMESHIFT_CONFIG.read_text(encoding="utf-8"))
    except (FileNotFoundError, PermissionError, json.JSONDecodeError):
        return None
    return any(str(data.get(key, "false")).lower() == "true" for key in SCHEDULE_KEYS)


def check_cron_integrity() -> CronCheckResult:
    """
    Read-only check — no privilege needed to read /etc/cron.d/*.disabled,
    the hash file, or Timeshift's own config (all world-readable).
    """
    schedule_enabled = _timeshift_schedule_enabled()

    if CRON_FILE_LEGACY.exists():
        if schedule_enabled:
            return CronCheckResult(
                legacy_cron_present=True,
                hash_changed=None,
                schedule_enabled=True,
                detail=(
                    "Timeshift's own Schedule settings have at least one "
                    "level enabled (Daily/Weekly/Monthly/Hourly/Boot) — "
                    "that's why /etc/cron.d/timeshift-hourly exists. This "
                    "is expected Timeshift behavior, not a bug: running "
                    "the fix would just get undone the next time Timeshift "
                    "runs. If you want on-demand-only, disable all "
                    "schedule levels in Timeshift itself (Settings → "
                    "Schedule) instead."
                ),
            )
        return CronCheckResult(
            legacy_cron_present=True,
            hash_changed=None,
            schedule_enabled=schedule_enabled,
            detail=(
                "Legacy /etc/cron.d/timeshift-hourly has reappeared, and "
                "Timeshift's own schedule settings are all off — this is "
                "the genuine anomaly the fix is for. Run the scheduling "
                "fix."
            ),
        )

    if not CRON_FILE_DISABLED.exists():
        return CronCheckResult(
            legacy_cron_present=False,
            hash_changed=None,
            schedule_enabled=schedule_enabled,
            detail="No legacy cron file found (disabled or otherwise) — nothing to check.",
        )

    current_hash = _sha256_of(CRON_FILE_DISABLED)
    stored_hash = None
    try:
        stored_hash = HASH_FILE.read_text().strip().split()[0]
    except (FileNotFoundError, PermissionError, IndexError):
        pass

    if current_hash is None:
        return CronCheckResult(
            legacy_cron_present=False,
            hash_changed=None,
            schedule_enabled=schedule_enabled,
            detail="Could not read the disabled cron file to hash it.",
        )

    changed = current_hash != stored_hash
    detail = (
        "Disabled cron file has changed since last recorded hash — worth a look."
        if changed
        else "Disabled cron file hash matches last known-good state."
    )
    return CronCheckResult(
        legacy_cron_present=False,
        hash_changed=changed,
        schedule_enabled=schedule_enabled,
        detail=detail,
    )


def fix_scheduling() -> tuple[bool, str]:
    """
    Runs the bundled cron-fix helper via pkexec. The helper itself is
    argument-free and does exactly one thing: if the legacy cron file has
    reappeared, rename it back to disabled and restart cron. No systemd
    timer is touched, ever — see module docstring.

    Refuses to run at all (no pkexec call, no auth prompt spent) if
    Timeshift's own schedule settings currently have anything enabled —
    the rename would just get undone the next time Timeshift runs.
    """
    if _timeshift_schedule_enabled():
        return False, (
            "Not running the fix — Timeshift's own schedule settings have "
            "at least one level enabled, so the cron file would just "
            "reappear. Disable scheduling in Timeshift's own Settings → "
            "Schedule tab instead."
        )

    try:
        result = subprocess.run(
            ["pkexec", CRONFIX_HELPER],
            capture_output=True, text=True, check=False,
        )
    except FileNotFoundError as exc:
        return False, f"Could not launch pkexec: {exc}"

    detail = (result.stdout + result.stderr).strip() or f"exit code {result.returncode}"
    return result.returncode == 0, detail
