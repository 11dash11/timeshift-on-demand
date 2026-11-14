"""
last_run.py — remembers how the most recent backup ended, so the Dashboard
can answer "was my last backup OK?" (no GTK, no privilege).

Both ways of running a backup record into the same small file: the Backup
tab and tray (BackupRunner, source "backup-tab" or "tray") and the
login/resume prompt script (source "prompt"), which calls this module's
command line:

    python3 last_run.py record --source prompt --outcome failed --reason="..."

Only attempts that actually ran the backup helper are recorded. A run that
never got that far (drive not mounted, no drive configured, user said No to
the prompt, password dialog dismissed) leaves the previous record alone, so
a FAILED line keeps warning until a backup really succeeds: the log says why, and the
Dashboard's question is about the last backup, not the last click.

The file is replaced atomically, and reading it never raises: a missing,
corrupt or unexpected file is just "no run recorded".
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

SUCCESS, FAILED = "success", "failed"
OUTCOMES = (SUCCESS, FAILED)
SOURCE_LABELS = {"backup-tab": "Backup tab", "tray": "tray icon", "prompt": "login/resume prompt"}
MAX_REASON = 500


def _path() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local/share"
    return base / "timeshift-on-demand" / "last-run.json"


@dataclass
class LastRun:
    finished_at: datetime
    outcome: str
    reason: Optional[str] = None
    source: Optional[str] = None


def record(outcome: str, reason: Optional[str] = None, source: Optional[str] = None,
           now: Optional[datetime] = None) -> None:
    """Never raises: a state file we can't write must not break a backup."""
    if outcome not in OUTCOMES:
        return
    reason = " ".join((reason or "").split())[:MAX_REASON] or None
    data = {
        "finished_at": (now or datetime.now()).astimezone().isoformat(timespec="seconds"),
        "outcome": outcome,
        "reason": reason,
        "source": source,
    }
    path = _path()
    tmp = path.with_suffix(".json.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def load() -> Optional[LastRun]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("outcome") not in OUTCOMES:
            return None
        reason, source = data.get("reason"), data.get("source")
        return LastRun(
            # Converted here, not in describe(), so a date that can't be
            # converted (e.g. year 0001) reads as "no run recorded" too.
            finished_at=datetime.fromisoformat(data["finished_at"]).astimezone(),
            outcome=data["outcome"],
            reason=reason if isinstance(reason, str) else None,
            source=source if isinstance(source, str) else None,
        )
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        return None


def describe(last: Optional[LastRun]) -> str:
    """One plain-English line for the Dashboard."""
    if last is None:
        return "Last backup: no run recorded yet"
    # The year only when it isn't this one, so an old record can't pass for
    # a fresh one.
    fmt = "%a %d %b %H:%M" if last.finished_at.year == datetime.now().year else "%a %d %b %Y %H:%M"
    when = last.finished_at.strftime(fmt)
    # Where it was started from, straight after "Last backup"; an unknown
    # or missing source (older records) just leaves it out.
    via = SOURCE_LABELS.get(last.source or "")
    head = f"Last backup ({via})" if via else "Last backup"
    if last.outcome == SUCCESS:
        return f"{head}: succeeded — {when}"
    text = f"{head}: FAILED — {when}"
    return f"{text}\n{last.reason}" if last.reason else text


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    sub = parser.add_subparsers(dest="cmd", required=True)
    rec = sub.add_parser("record")
    rec.add_argument("--outcome", required=True, choices=OUTCOMES)
    rec.add_argument("--reason")
    rec.add_argument("--source")
    args = parser.parse_args(argv)
    record(args.outcome, args.reason, args.source)
    return 0


if __name__ == "__main__":
    sys.exit(main())
