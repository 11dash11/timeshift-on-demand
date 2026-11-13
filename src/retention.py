"""
retention.py — how long Timeshift keeps Companion's backups, as plain text
for the Dashboard (no GTK, no privilege; reads /etc/timeshift/timeshift.json
through status.py).

What this explains, from Timeshift 24.01.1's source (SnapshotRepo.vala and
Main.vala; not checked against other versions):

* Companion runs `timeshift --create --tags D`. That tags the snapshot
  "daily" whatever the schedule settings are, and Timeshift prunes after every
  --create using only the count_* numbers, never the schedule_* flags. So the
  daily count applies even when the Daily level is switched off.
* count_daily = N keeps every daily snapshot from the last N days (a rolling
  window), and never fewer than N snapshots. Weekly and monthly work the same
  way with weeks and months.
* Only a Weekly or Monthly level creates restore points older than that
  window. With both off, history goes back just the last few days.
* One snapshot can carry several tags, so per-tag counts can add up to more
  than the number of snapshots.

Keep the numbers and rules here in step with docs/usage.md and the README.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

LEVELS = (
    ("daily", "Daily"),
    ("weekly", "Weekly"),
    ("monthly", "Monthly"),
    ("hourly", "Hourly"),
    ("boot", "Boot"),
)


@dataclass
class Retention:
    enabled: dict[str, bool]
    keep: dict[str, Optional[int]]  # count_* per level; None if missing or not a number


@dataclass
class RetentionText:
    levels: str          # which levels are on, and the count each keeps
    daily_rule: str      # how long Companion's (daily-tagged) backups are kept
    tags: Optional[str]  # snapshots per tag, if known
    warning: Optional[str]  # set when no weekly or monthly restore points are scheduled


def from_config(config: Mapping) -> Retention:
    """Read the schedule_* flags and count_* numbers; never raises."""
    enabled: dict[str, bool] = {}
    keep: dict[str, Optional[int]] = {}
    for key, _label in LEVELS:
        enabled[key] = str(config.get(f"schedule_{key}", "false")).strip().lower() == "true"
        try:
            n = int(str(config.get(f"count_{key}")).strip())
        except ValueError:
            n = None
        keep[key] = n if n is not None and n >= 0 else None
    return Retention(enabled, keep)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _kept(n: Optional[int]) -> tuple[str, str, str]:
    """(how long Companion's backups are kept, what to append to the rule line,
    what to append to the warning), built in one place so each sentence reads
    right for a known, unknown or zero count."""
    if n is None:
        return "kept for the number of days set in Timeshift", "", ", so history is short"
    if n == 0:
        zero = "Timeshift's daily keep count is 0, so older backups can be removed at the next backup"
        return "kept for 0 days", f": {zero}", f" ({zero})"
    return (
        f"kept for a rolling {_plural(n, 'day')} (at least {_plural(n, 'snapshot')})",
        "",
        ", so history only goes back a few days",
    )


TAGS_NOTE = ("A snapshot can carry more than one tag (for example daily and weekly), so the "
             "per-tag counts can add up to more than the total.")


def describe(ret: Retention, tag_counts: Optional[Mapping[str, int]] = None,
             total: Optional[int] = None) -> RetentionText:
    levels = ", ".join(
        f"{label} {'on' if ret.enabled[key] else 'off'}"
        + ("" if ret.keep[key] is None else f" (keep {ret.keep[key]})")
        for key, label in LEVELS
    )
    n = ret.keep["daily"]  # used even when the Daily level is off: pruning ignores the flag
    kept, rule_tail, warn_tail = _kept(n)
    rule = f"Companion backups are tagged daily and {kept}{rule_tail}."

    tags = None
    if tag_counts:
        parts = ", ".join(f"{tag} {count}" for tag, count in sorted(tag_counts.items()))
        tags = f"Snapshots by tag: {parts}"
        if total is not None:
            tags += f" (tags overlap, so these can add up to more than the {total} total)"

    warning = None
    if not ret.enabled["weekly"] and not ret.enabled["monthly"]:
        warning = (
            f"No weekly or monthly restore points are scheduled. Companion backups are {kept}"
            f"{warn_tail}. To keep longer history, enable Weekly and/or Monthly in "
            "Timeshift → Settings → Schedule."
        )
    return RetentionText(levels, rule, tags, warning)
