"""
backup_runner.py — threaded runner for the privileged backup call.

Replaces timeshift-ondemand-backup.sh's drive-wait + backup-invocation
logic, and replaces timeshift-monitor.sh's spawned-terminal live view with
an in-process log stream the GTK app can render directly (a queue the GUI
polls via GLib.idle_add, rather than tailing a logfile in a separate
window).

The privileged call now goes through `pkexec` to Companion's own bundled
helper script (packaging/helpers/timeshift-on-demand-backup-helper),
resolved via the org.timeshiftondemand.app.backup polkit action — see
packaging/README.md for the exact invocation contract. This replaces the
first-pass `sudo /usr/local/sbin/timeshift-backup-root.sh` call, which
assumed a personal-machine path that isn't installed by the package. The
exit-134 tolerance logic itself now lives inside that helper, not here;
this module only orchestrates the drive-wait and the pkexec call around
it, same division of responsibility as before.
"""

from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional


def _data_dir() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local/share"
    return base / "timeshift-on-demand"


LOGFILE = _data_dir() / "backup.log"
BACKUP_HELPER = "/usr/lib/timeshift-on-demand/timeshift-on-demand-backup-helper"
DRIVE_WAIT_ATTEMPTS = 36
DRIVE_WAIT_INTERVAL_S = 5

# pkexec's own documented exit codes (`man pkexec`, RETURN VALUE) — not
# specific to this helper, but worth naming instead of leaving as bare
# numbers, since a real failure (exit-code passthrough from the helper
# itself) must not be confused with "user said no" or "auth broken".
PKEXEC_EXIT_DISMISSED = 126  # user dismissed the authentication dialog
PKEXEC_EXIT_NOT_AUTHORIZED = 127  # auth failed/denied, or no matching policy action


@dataclass
class BackupResult:
    started_at: datetime
    finished_at: datetime
    exit_code: int
    drive_mounted: bool

    @property
    def success(self) -> bool:
        return self.drive_mounted and self.exit_code == 0


class BackupRunner:
    """
    Runs the backup in a background thread. Call `start()`, then poll
    `poll_log_lines()` from the GTK main loop (e.g. via GLib.timeout_add)
    to drain new log lines without blocking the UI. `is_running` and
    `result` reflect current state.

    `drive_uuid` may be None if the user hasn't configured a backup drive
    yet in the Settings tab (see config.py) — `start()` still runs (so
    "Backup Now" always does *something* observable) but reports
    immediately rather than waiting on a drive that was never chosen, or
    invoking pkexec for nothing.
    """

    def __init__(self, drive_uuid: Optional[str]):
        self.drive_uuid = drive_uuid
        self._log_queue: "queue.Queue[str]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self.is_running = False
        self.result: Optional[BackupResult] = None

    def _log(self, msg: str) -> None:
        line = f"{datetime.now().strftime('%H:%M:%S')}  {msg}"
        self._log_queue.put(line)
        LOGFILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOGFILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def poll_log_lines(self) -> list[str]:
        """Non-blocking drain of any new log lines. Safe to call from the GUI loop."""
        lines = []
        while True:
            try:
                lines.append(self._log_queue.get_nowait())
            except queue.Empty:
                break
        return lines

    def start(self, on_done: Optional[Callable[[BackupResult], None]] = None) -> None:
        if self.is_running:
            self._log("Backup already in progress — ignoring duplicate trigger.")
            return
        self.is_running = True
        self.result = None
        self._thread = threading.Thread(
            target=self._run, args=(on_done,), daemon=True
        )
        self._thread.start()

    def _wait_for_drive(self) -> Optional[str]:
        from status import find_backup_mount  # local import: sibling module

        for attempt in range(1, DRIVE_WAIT_ATTEMPTS + 1):
            mount = find_backup_mount(self.drive_uuid)
            if mount:
                self._log(f"Backup drive mounted at {mount}.")
                return mount
            self._log(f"Waiting for drive to mount (attempt {attempt}/{DRIVE_WAIT_ATTEMPTS})...")
            time.sleep(DRIVE_WAIT_INTERVAL_S)
        return None

    def _run(self, on_done: Optional[Callable[[BackupResult], None]]) -> None:
        started = datetime.now()
        self._log("Backup started.")

        if not self.drive_uuid:
            self._log("No backup drive configured — open Settings to choose one.")
            result = BackupResult(started, datetime.now(), exit_code=0, drive_mounted=False)
            self._finish(result, on_done)
            return

        # Fail fast, before waiting on the drive or spending a pkexec
        # prompt: confirmed via real testing (Samsung RF511, 2026-08-30)
        # that Companion's own drive setting and Timeshift's OWN
        # backup_device_uuid are entirely independent — Companion can
        # find its configured drive mounted just fine while Timeshift
        # itself is still unconfigured (or points elsewhere), and the
        # backup fails with Timeshift's own "Device not found: UUID=..."
        # with nothing upstream explaining why. This is a pure config
        # read (no privilege needed), so check it before doing anything
        # that costs the user time or an authentication prompt.
        from status import get_timeshift_backup_device_uuid  # local import: sibling module

        ts_uuid = get_timeshift_backup_device_uuid()
        if ts_uuid is None:
            self._log(
                "Timeshift itself has no backup device configured yet — open "
                "Timeshift and complete its setup (pick a backup drive) before "
                "running an on-demand backup from here."
            )
            result = BackupResult(started, datetime.now(), exit_code=0, drive_mounted=False)
            self._finish(result, on_done)
            return
        if ts_uuid != self.drive_uuid:
            self._log(
                f"Timeshift is configured to back up to a different device "
                f"(UUID={ts_uuid}) than the one selected here in Companion's "
                f"Settings (UUID={self.drive_uuid}) — open Timeshift and "
                f"repoint its backup device to match, or update Companion's "
                f"Settings to match Timeshift's, before running an on-demand "
                f"backup."
            )
            result = BackupResult(started, datetime.now(), exit_code=0, drive_mounted=False)
            self._finish(result, on_done)
            return

        mount = self._wait_for_drive()
        if not mount:
            self._log("Backup drive not mounted after wait — aborting (non-fatal).")
            result = BackupResult(started, datetime.now(), exit_code=0, drive_mounted=False)
            self._finish(result, on_done)
            return

        self._log(f"Running {BACKUP_HELPER} via pkexec ...")
        try:
            proc = subprocess.run(
                ["pkexec", BACKUP_HELPER],
                capture_output=True,
                text=True,
                timeout=3600,
                check=False,
            )
            for line in (proc.stdout + proc.stderr).splitlines():
                if line.strip():
                    self._log(line.strip())
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            self._log("Backup timed out after 1 hour — treating as failure.")
            exit_code = -1
        except FileNotFoundError as exc:
            self._log(f"Could not launch pkexec: {exc}")
            exit_code = -1

        if exit_code == 0:
            self._log("Backup completed successfully.")
        elif exit_code == PKEXEC_EXIT_DISMISSED:
            self._log("Authentication dialog was dismissed — backup not run.")
        elif exit_code == PKEXEC_EXIT_NOT_AUTHORIZED:
            self._log(
                "Authentication failed or was denied (pkexec exit 127) — "
                "check that the package's polkit policy is installed correctly."
            )
        else:
            self._log(f"Backup finished with exit code {exit_code}.")

        result = BackupResult(started, datetime.now(), exit_code=exit_code, drive_mounted=True)
        self._finish(result, on_done)

    def _finish(self, result: BackupResult, on_done: Optional[Callable[[BackupResult], None]]) -> None:
        self.result = result
        self.is_running = False
        if on_done:
            on_done(result)
