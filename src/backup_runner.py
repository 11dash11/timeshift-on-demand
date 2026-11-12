"""
backup_runner.py — threaded runner for the privileged backup call.

Replaces timeshift-ondemand-backup.sh's drive-wait + backup-invocation
logic, and replaces timeshift-monitor.sh's spawned-terminal live view with
an in-process log stream the GTK app can render directly (a queue the GUI
polls via GLib.idle_add, rather than tailing a logfile in a separate
window).

The privileged call now goes through `pkexec` to Companion's own bundled
helper script (packaging/helpers/timeshift-on-demand-backup-helper),
resolved via the io.github.11dash11.timeshiftondemand.backup polkit action — see
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
from typing import Callable, Iterable, Optional

import last_run
from progress_filter import PROGRESS, classify


def _data_dir() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local/share"
    return base / "timeshift-on-demand"


LOGFILE = _data_dir() / "backup.log"
BACKUP_HELPER = "/usr/lib/timeshift-on-demand/timeshift-on-demand-backup-helper"
# Past LOG_MAX_BYTES, keep only the last LOG_KEEP_BYTES (checked at the
# start of each run). Same limits as timeshift-on-demand-prompt.
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_KEEP_BYTES = 5 * 1024 * 1024
DRIVE_WAIT_ATTEMPTS = 36
DRIVE_WAIT_INTERVAL_S = 5

# pkexec's own documented exit codes (`man pkexec`, RETURN VALUE) — not
# specific to this helper, but worth naming instead of leaving as bare
# numbers, since a real failure (exit-code passthrough from the helper
# itself) must not be confused with "user said no" or "auth broken".
PKEXEC_EXIT_DISMISSED = 126  # user dismissed the authentication dialog
PKEXEC_EXIT_NOT_AUTHORIZED = 127  # auth failed/denied, or no matching policy action

# The helper ends every failure with "BACKUP-FAILED: <plain-English reason>"
# (packaging/helpers/timeshift-on-demand-backup-helper).
FAILURE_MARKER = "BACKUP-FAILED: "


def _trim_log() -> None:
    try:
        if LOGFILE.stat().st_size <= LOG_MAX_BYTES:
            return
        with open(LOGFILE, "rb") as f:
            f.seek(-LOG_KEEP_BYTES, os.SEEK_END)
            tail = f.read()
        tmp = LOGFILE.with_suffix(".log.tmp")
        tmp.write_bytes(tail)
        tmp.replace(LOGFILE)
    except OSError:
        pass  # a log we can't trim is not a reason to skip the backup


@dataclass
class BackupResult:
    started_at: datetime
    finished_at: datetime
    exit_code: int
    drive_mounted: bool
    reason: Optional[str] = None  # plain-English failure reason, when one is known

    @property
    def cancelled(self) -> bool:
        return self.exit_code == PKEXEC_EXIT_DISMISSED

    @property
    def success(self) -> bool:
        return self.drive_mounted and self.exit_code == 0


class BackupRunner:
    """
    Runs the backup in a background thread. Call `start()`, then poll
    `poll_log_lines()` from the GTK main loop (e.g. via GLib.timeout_add)
    to drain new log lines without blocking the UI. `is_running` and
    `result` reflect current state.

    The drive waited for is whichever backup device Timeshift itself is
    configured with (status.get_timeshift_backup_device_uuid()), read at
    the start of each run, so there is nothing to configure here. If
    Timeshift has none yet, `start()` still runs (so "Backup Now" always
    does *something* observable) but says so instead of waiting on a drive
    or invoking pkexec for nothing.
    """

    def __init__(self):
        self._log_queue: "queue.Queue[str]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self.is_running = False
        self.result: Optional[BackupResult] = None
        self._failure_reason: Optional[str] = None

    def _log(self, msg: str, persist: bool = True) -> str:
        """Queue a line for the Backup tab and (unless persist=False)
        append it to backup.log. Returns the timestamped line."""
        line = f"{datetime.now().strftime('%H:%M:%S')}  {msg}"
        self._log_queue.put(line)
        if persist:
            self._write_file(line)
        return line

    @staticmethod
    def _write_file(line: str) -> None:
        LOGFILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOGFILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _stream_output(self, lines: Iterable[str]) -> None:
        """Log the helper's output. Progress updates ("NN% complete")
        go to the Backup tab live but are NOT each written to backup.log:
        only the newest one is kept, written when a normal line follows
        it or the stream ends, so the log still shows how far a run got
        without thousands of near-identical lines. Normal lines (errors
        included) are always written, in order."""
        pending: Optional[str] = None
        try:
            for raw in lines:
                text = raw.strip()
                if not text:
                    continue
                if text.startswith(FAILURE_MARKER):
                    self._failure_reason = text[len(FAILURE_MARKER):].strip() or None
                if classify(text).kind == PROGRESS:
                    pending = self._log(text, persist=False)
                    continue
                if pending is not None:
                    self._write_file(pending)
                    pending = None
                self._log(text)
        finally:
            if pending is not None:
                self._write_file(pending)

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
        _trim_log()
        self._thread = threading.Thread(
            target=self._run, args=(on_done,), daemon=True
        )
        self._thread.start()

    def _wait_for_drive(self, uuid: str) -> Optional[str]:
        from status import find_backup_mount  # local import: sibling module

        for attempt in range(1, DRIVE_WAIT_ATTEMPTS + 1):
            mount = find_backup_mount(uuid)
            if mount:
                self._log(f"Backup drive mounted at {mount}.")
                return mount
            self._log(f"Waiting for drive to mount (attempt {attempt}/{DRIVE_WAIT_ATTEMPTS})...")
            time.sleep(DRIVE_WAIT_INTERVAL_S)
        return None

    def _run(self, on_done: Optional[Callable[[BackupResult], None]]) -> None:
        # Whatever happens inside _run_backup(), _finish() must run —
        # otherwise is_running stays True and Backup Now is silently
        # ignored until the app is restarted.
        started = datetime.now()
        result = BackupResult(started, started, exit_code=-1, drive_mounted=False)
        try:
            result = self._run_backup(started)
        except Exception as exc:  # noqa: BLE001 — must not escape the worker thread
            self._log(f"Backup runner error: {exc!r}")
            last_run.record(last_run.FAILED, f"Backup runner error: {exc!r}", source="backup-tab")
            # drive_mounted=True so the GUI reports "failed", not "did not
            # run" — we can't know how far the attempt got.
            result = BackupResult(
                started, datetime.now(), exit_code=-1, drive_mounted=True,
                reason=f"Backup runner error: {exc!r}",
            )
        finally:
            self._finish(result, on_done)

    def _run_backup(self, started: datetime) -> BackupResult:
        self._log("Backup started.")

        # Fail fast, before waiting on the drive or spending a pkexec
        # prompt: a pure config read (no privilege needed). Timeshift's own
        # backup_device_uuid is the drive the backup goes to, so it is also
        # the one to wait for. Imported here: sibling module.
        from status import get_timeshift_backup_device_uuid

        uuid = get_timeshift_backup_device_uuid()
        if uuid is None:
            reason = (
                "Timeshift itself has no backup device configured yet — open "
                "Timeshift and complete its setup (pick a backup drive) before "
                "running an on-demand backup from here."
            )
            self._log(reason)
            return BackupResult(started, datetime.now(), exit_code=0, drive_mounted=False, reason=reason)

        mount = self._wait_for_drive(uuid)
        if not mount:
            minutes = DRIVE_WAIT_ATTEMPTS * DRIVE_WAIT_INTERVAL_S // 60
            reason = f"The backup drive wasn't mounted after waiting {minutes} minutes."
            self._log("Backup drive not mounted after wait — aborting (non-fatal).")
            return BackupResult(started, datetime.now(), exit_code=0, drive_mounted=False, reason=reason)

        self._log(f"Running {BACKUP_HELPER} via pkexec ...")
        # Streamed line by line (text mode also splits on timeshift's \r
        # progress updates), so the Backup tab's log is live rather than
        # appearing all at once when the helper exits.
        #
        # Deliberately no timeout. Once authenticated, pkexec's child runs
        # as root, so this unprivileged process can't kill it anyway —
        # the old 1-hour subprocess.run timeout raised PermissionError from
        # kill() (escaping the TimeoutExpired handler and wedging
        # is_running), while closing the pipe out from under a still-running
        # backup. A first full rsync backup to USB can legitimately take
        # longer than an hour.
        exit_code = -1
        self._failure_reason = None
        launch_error: Optional[str] = None
        try:
            with subprocess.Popen(
                ["pkexec", BACKUP_HELPER],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            ) as proc:
                self._stream_output(proc.stdout)
            exit_code = proc.returncode
        except OSError as exc:
            launch_error = f"Could not launch pkexec: {exc}"
            self._log(launch_error)

        reason: Optional[str] = None
        if exit_code == 0:
            self._log("Backup completed successfully.")
        elif exit_code == PKEXEC_EXIT_DISMISSED:
            reason = "Authentication dialog was dismissed — backup not run."
            self._log(reason)
        elif exit_code == PKEXEC_EXIT_NOT_AUTHORIZED:
            reason = (
                "Authentication failed or was denied (pkexec exit 127) — "
                "check that the package's polkit policy is installed correctly."
            )
            self._log(reason)
        else:
            self._log(f"Backup finished with exit code {exit_code}.")
            reason = (
                self._failure_reason
                or launch_error
                or f"Timeshift failed (exit code {exit_code}). See the log for details."
            )

        # A dismissed password dialog means the helper never ran, so (like a
        # drive that never mounted) it doesn't replace an earlier failure.
        if exit_code == 0:
            last_run.record(last_run.SUCCESS, source="backup-tab")
        elif exit_code != PKEXEC_EXIT_DISMISSED:
            last_run.record(last_run.FAILED, reason, source="backup-tab")

        return BackupResult(
            started, datetime.now(), exit_code=exit_code, drive_mounted=True, reason=reason,
        )

    def _finish(self, result: BackupResult, on_done: Optional[Callable[[BackupResult], None]]) -> None:
        self.result = result
        self.is_running = False
        if on_done:
            on_done(result)
