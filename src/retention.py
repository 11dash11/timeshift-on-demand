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
        # With count_daily 0 the prune right after --create removes every daily
        # snapshot older than "now", including the one just made.
        zero = "Timeshift's daily keep count is 0, so each Companion backup is deleted as soon as it's made"
        return "kept for 0 days", f" ({zero})", f" ({zero})"
    return (
        f"kept for a rolling {_plural(n, 'day')} (at least {_plural(n, 'snapshot')})",
        "",
        ", so history only goes back a few days",
    )


TAGS_NOTE = ("A snapshot can carry more than one tag (for example daily and weekly), so the "
             "per-tag counts can add up to more than the total.")


def describe(ret: Retention, tag_counts: Optional[Mapping[str, int]] = None) -> RetentionText:
    # Only the levels that are on, e.g. "Daily/Weekly/Monthly on, keep 2/2/1".
    on = [(label, ret.keep[key]) for key, label in LEVELS if ret.enabled[key]]
    if on:
        levels = "/".join(label for label, _ in on) + " on, keep " + "/".join(
            "?" if keep is None else str(keep) for _, keep in on)
        if any(keep is None for _, keep in on):
            levels += " (? = count not set)"
    else:
        levels = "none on"
    n = ret.keep["daily"]  # used even when the Daily level is off: pruning ignores the flag
    kept, rule_tail, warn_tail = _kept(n)
    rule = f"Companion backups are tagged daily: {kept}{rule_tail}."

    # The overlap explanation (TAGS_NOTE) is the label's tooltip, not text;
    # the total is on the Snapshots line above.
    tags = None
    if tag_counts:
        order = {key: i for i, (key, _label) in enumerate(LEVELS)}
        parts = ", ".join(
            f"{tag} {count}"
            for tag, count in sorted(tag_counts.items(), key=lambda kv: (order.get(kv[0], 99), kv[0])))
        tags = f"Snapshots by tag: {parts}"

    warning = None
    if not ret.enabled["weekly"] and not ret.enabled["monthly"]:
        warning = (
            f"No weekly or monthly restore points are scheduled. Companion backups are {kept}"
            f"{warn_tail}. To keep longer history, enable Weekly and/or Monthly in "
            "Timeshift → Settings → Schedule."
        )
    return RetentionText(levels, rule, tags, warning)
