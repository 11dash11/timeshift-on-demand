"""
app.py — GTK3 main window: Dashboard / Backup / Settings tabs.

Consolidates what used to be separate surfaces (zenity dialog + spawned
terminal monitor, timeshift-status.sh output) into one window. No time-window restriction — the
Backup tab's trigger button is always enabled.

There is no drive setting of its own: backups go to whichever device
Timeshift is configured with, which the Dashboard shows. If Timeshift has
none yet, the Backup tab still works (the button is always clickable) but
says so instead of guessing one. Snapshot management stays
Timeshift's own job: the Dashboard shows a read-only glance plus an
"Open Timeshift" button, no delete/restore capability here.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Callable, Optional

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

import config  # noqa: E402
import last_run  # noqa: E402
import retention  # noqa: E402
import status  # noqa: E402
from backup_runner import BackupRunner  # noqa: E402
from log_display import LogDisplay  # noqa: E402
from progress_filter import classify  # noqa: E402

LOG_POLL_INTERVAL_MS = 500
STATUS_REFRESH_INTERVAL_MS = 30_000


class TimeshiftCompanionWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application):
        super().__init__(application=app, title="Timeshift On Demand")
        self.set_default_size(720, 480)

        self.runner = BackupRunner()
        # Set by main.py when there is a tray: called with True when a
        # backup starts and False when it ends (always on the main thread).
        self.on_running_changed: Optional[Callable[[bool], None]] = None

        notebook = Gtk.Notebook()
        self.add(notebook)

        self.dashboard = self._build_dashboard_tab()
        notebook.append_page(self.dashboard, Gtk.Label(label="Dashboard"))

        self.backup_tab = self._build_backup_tab()
        notebook.append_page(self.backup_tab, Gtk.Label(label="Backup"))

        self.settings_tab = self._build_settings_tab()
        notebook.append_page(self.settings_tab, Gtk.Label(label="Settings"))

        self.refresh_dashboard()
        GLib.timeout_add(STATUS_REFRESH_INTERVAL_MS, self._on_status_refresh_tick)
        GLib.timeout_add(LOG_POLL_INTERVAL_MS, self._on_log_poll_tick)

        # Closing the window hides it (tray keeps the app alive) rather
        # than quitting — matches the "launcher icon + tray" launch model.
        self.connect("delete-event", self._on_delete_event)

        # GTK3 widgets are not visible by default when constructed — only
        # show()/show_all() makes them so. Window.present() (called by
        # main.py after building this) maps the toplevel itself, which is
        # why the title bar/chrome would render even without this call —
        # but every child built above (notebook, tabs, labels, buttons)
        # would stay invisible without it. Confirmed missing and reproduced
        # via an actual screenshot during testing (2026-08-29): window
        # chrome rendered, content area was entirely blank. Call this last,
        # after the full widget tree above is built.
        self.show_all()

    # ------------------------------------------------------------------
    # Dashboard tab
    # ------------------------------------------------------------------
    def _build_dashboard_tab(self) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(12)

        # First, because it answers the question the app exists for: did
        # the last backup work? (See last_run.py.)
        self.last_run_label = Gtk.Label(label="", xalign=0)
        self.last_run_label.set_line_wrap(True)
        self.snapshot_label = Gtk.Label(label="Loading snapshot history...", xalign=0)
        # How long Timeshift keeps Companion's backups (see retention.py).
        self.retention_label = Gtk.Label(label="", xalign=0)
        self.retention_label.set_line_wrap(True)
        # Per-tag counts: short text, the full explanation in the tooltip.
        self.tags_label = Gtk.Label(label="", xalign=0)
        self.tags_label.set_line_wrap(True)
        self.tags_label.set_tooltip_text(retention.TAGS_NOTE)
        # A permanent limit, not an error: bold, but no error colour or icon,
        # so someone who chose daily-only history doesn't learn to ignore it.
        self.retention_warning_label = Gtk.Label(label="", xalign=0)
        self.retention_warning_label.set_line_wrap(True)
        # Shown only when there is something to say; show_all() must not
        # override that.
        for w in (self.tags_label, self.retention_warning_label):
            w.set_no_show_all(True)
            w.set_visible(False)
        self.disk_label = Gtk.Label(label="Loading disk usage...", xalign=0)

        advisory = Gtk.Label(
            label=(
                "This is a read-only glance. Companion cannot browse, restore, "
                "or delete snapshots — use the “Open Timeshift” button above; "
                "it's the only way to work with your snapshot data.\n"
                "How long backups are kept is Timeshift's own retention "
                "setting (Timeshift → Settings → Schedule → Keep); the "
                "Retention lines above show what it's set to."
            ),
            xalign=0,
        )
        advisory.set_line_wrap(True)
        advisory.get_style_context().add_class("dim-label")

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        refresh_btn = Gtk.Button(label="Refresh")
        refresh_btn.connect("clicked", lambda *_: self.refresh_dashboard())
        open_ts_btn = Gtk.Button(label="Open Timeshift")
        open_ts_btn.connect("clicked", self._on_open_timeshift_clicked)
        btn_row.pack_start(refresh_btn, False, False, 0)
        btn_row.pack_start(open_ts_btn, False, False, 0)

        for w in (self.last_run_label, self.snapshot_label, self.retention_label,
                  self.tags_label, self.retention_warning_label,
                  self.disk_label, btn_row, advisory):
            box.pack_start(w, False, False, 0)

        return box

    def refresh_dashboard(self) -> None:
        """
        Snapshot info plus drive status. Both are unprivileged local reads
        (see status.get_snapshots()), so this runs on window open, Refresh,
        after a backup, and from the 30s auto-refresh timer.
        """
        self._refresh_last_run()
        self._refresh_snapshot_info()
        self._refresh_drive_status()

    def _refresh_last_run(self) -> None:
        last = last_run.load()
        text = GLib.markup_escape_text(last_run.describe(last))
        if last is not None and last.outcome == last_run.FAILED:
            text = f"<b>{text}</b>"
        self.last_run_label.set_markup(text)

    def _refresh_snapshot_info(self) -> None:
        snaps = status.get_snapshots()
        if snaps.error:
            self.snapshot_label.set_text(f"Snapshots: unavailable — {snaps.error}")
        else:
            text = f"Snapshots: {len(snaps.names)} total, latest {snaps.latest or 'none'}"
            if snaps.latest_tags:
                text += f" ({snaps.latest_tags})"
            if snaps.incomplete:
                text += f" — plus {snaps.incomplete} incomplete (in progress, or a run that didn't finish)"
            self.snapshot_label.set_text(text)
        self._refresh_retention(snaps)

    def _refresh_retention(self, snaps) -> None:
        """How long Timeshift keeps backups. The levels and counts come from
        Timeshift's config; the per-tag counts only when the drive is readable."""
        ret = status.get_retention()
        if ret is None:
            self.retention_label.set_text("Retention: unavailable (Timeshift isn't configured yet)")
            self.tags_label.set_visible(False)
            self.retention_warning_label.set_visible(False)
            return
        text = retention.describe(
            ret,
            None if snaps.error else snaps.tag_counts,
            None if snaps.error else len(snaps.names),
        )
        self.retention_label.set_text(f"Retention: {text.daily_rule}\nSchedule levels: {text.levels}.")
        self.tags_label.set_text(text.tags or "")
        self.tags_label.set_visible(bool(text.tags))
        self.retention_warning_label.set_markup(
            "<b>" + GLib.markup_escape_text(text.warning) + "</b>" if text.warning else ""
        )
        self.retention_warning_label.set_visible(bool(text.warning))

    def _refresh_drive_status(self) -> None:
        drive_uuid = status.get_timeshift_backup_device_uuid()
        if not drive_uuid:
            self.disk_label.set_text(
                "Backup drive: Timeshift has no backup device set yet — open Timeshift and finish its setup"
            )
            return

        mount = status.find_backup_mount(drive_uuid)
        if not mount:
            self.disk_label.set_text(f"Backup drive (Timeshift's, {drive_uuid}): not currently mounted")
            return

        usage = status.get_backup_drive_usage(mount)
        if usage.error:
            self.disk_label.set_text(f"Backup drive ({mount}): usage error — {usage.error}")
        else:
            self.disk_label.set_text(
                f"Backup drive ({mount}): {usage.used_gb} GB used / "
                f"{usage.total_gb} GB total ({usage.percent_used}%)"
            )

    def _on_status_refresh_tick(self) -> bool:
        self.refresh_dashboard()
        return True  # keep the timeout running

    def _on_open_timeshift_clicked(self, *_args) -> None:
        """
        Launches the real Timeshift GUI via its own installed launcher,
        which handles its own pkexec/polkit elevation
        (in.teejeetech.pkexec.timeshift-gtk) — Companion needs no
        privilege of its own for this, it just spawns the process.
        """
        try:
            subprocess.Popen(["timeshift-launcher"])
        except FileNotFoundError:
            self.snapshot_label.set_text(
                self.snapshot_label.get_text()
                + "\n(Could not launch Timeshift — is the timeshift package installed?)"
            )

    # ------------------------------------------------------------------
    # Backup tab
    # ------------------------------------------------------------------
    def _build_backup_tab(self) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(12)

        self.trigger_btn = Gtk.Button(label="Backup Now")
        self.trigger_btn.connect("clicked", self._on_trigger_clicked)
        box.pack_start(self.trigger_btn, False, False, 0)

        self.status_line = Gtk.Label(label="Idle.", xalign=0)
        box.pack_start(self.status_line, False, False, 0)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.log_view = Gtk.TextView()
        self.log_view.set_editable(False)
        self.log_view.set_monospace(True)
        self.log_buffer = self.log_view.get_buffer()
        scroller.add(self.log_view)
        self.log_display = LogDisplay(self.log_buffer, scroller)
        box.pack_start(scroller, True, True, 0)

        return box

    def _on_trigger_clicked(self, *_args) -> None:
        if self.runner.is_running:
            return
        self.trigger_btn.set_sensitive(False)
        self.status_line.set_text("Backup running...")
        self.runner.start(on_done=self._on_backup_done_from_thread)
        self._set_running(True)

    def _set_running(self, running: bool) -> None:
        if self.on_running_changed:
            try:
                self.on_running_changed(running)
            except Exception as exc:  # noqa: BLE001 — a tray problem must never block the window
                print(f"Timeshift Companion: tray update failed: {exc!r}", file=sys.stderr)

    def _on_backup_done_from_thread(self, result) -> None:
        # Called from the worker thread — hop back to the GTK main loop
        # before touching any widgets.
        GLib.idle_add(self._on_backup_done_main_thread, result)

    def _on_backup_done_main_thread(self, result) -> bool:
        # First, so every way a run can end (finished, failed, never started,
        # dialog dismissed, runner error: BackupRunner._finish is in a
        # `finally`) puts the tray back to idle.
        self._set_running(False)
        self.trigger_btn.set_sensitive(True)
        if not result.drive_mounted:
            # No drive set in Timeshift, or the drive never mounted; the
            # runner says which, and the log above has the details.
            self.status_line.set_text(
                f"Backup did not run: {result.reason}" if result.reason
                else "Backup did not run — see the log above for why."
            )
        elif result.success:
            self.status_line.set_text("Backup completed successfully.")
        elif result.cancelled:
            self.status_line.set_text(f"Backup cancelled: {result.reason}")
        else:
            # The helper's own sentence when it gave one; the bare code
            # only for failures it never got to explain (e.g. pkexec's).
            self.status_line.set_text(
                f"Backup failed: {result.reason}" if result.reason
                else f"Backup failed (exit code {result.exit_code})."
            )

        self.refresh_dashboard()
        self._notify_backup_done(result)
        return False  # one-shot idle call

    def _notify_backup_done(self, result) -> None:
        """Desktop notification when a backup started from here ends, so a
        run started from the tray (window hidden) doesn't end unseen.
        Success and cancelled are skipped while the window is in front (the
        status line already shows them); a failure always notifies, and is
        urgent so the desktop keeps it until it is dismissed. A run that
        never started (no device, drive not mounted) gets a plain notice
        when the window isn't in front, so the tray going back to idle
        isn't silent."""
        failed = result.drive_mounted and not result.success and not result.cancelled
        if self.is_active() and not failed:
            return
        if not result.drive_mounted:
            title, body, icon, urgent = "Backup did not run", result.reason or "See the Backup tab's log.", "dialog-warning", False
        elif result.success:
            title, body, icon, urgent = "Backup completed", "Timeshift On Demand finished the backup.", "drive-harddisk", False
        elif result.cancelled:
            title, body, icon, urgent = "Backup cancelled", result.reason or "", "dialog-warning", False
        else:
            title, body, icon, urgent = "Backup FAILED", result.reason or "See the Backup tab's log.", "dialog-error", True
        note = Gio.Notification.new(f"Timeshift On Demand — {title}")
        note.set_body(body)
        note.set_icon(Gio.ThemedIcon.new(icon))
        if urgent:
            note.set_priority(Gio.NotificationPriority.URGENT)
        # One id, so a newer result replaces the older notification.
        self.get_application().send_notification("backup-result", note)

    def _on_log_poll_tick(self) -> bool:
        # Progress lines replace each other in place; everything else is
        # appended. Scrolling follows new output only while the user is at
        # the bottom (see log_display.py).
        self.log_display.apply([classify(line) for line in self.runner.poll_log_lines()])
        return True

    # ------------------------------------------------------------------
    # Settings tab
    # ------------------------------------------------------------------
    def _build_settings_tab(self) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(12)

        # No drive picker: backups go to the device Timeshift itself is set
        # up with (shown on the Dashboard), so there is only one place to
        # choose it.
        note = Gtk.Label(
            label=(
                "Backups go to the drive chosen in Timeshift (Settings → Location). "
                "The Dashboard shows its status; use “Open Timeshift” there to change it."
            ),
            xalign=0,
        )
        note.set_line_wrap(True)
        note.get_style_context().add_class("dim-label")
        box.pack_start(note, False, False, 0)

        box.pack_start(Gtk.Separator(), False, False, 6)
        box.pack_start(
            Gtk.Label(label="Ask whether to back up (when the backup drive is connected):", xalign=0),
            False, False, 0,
        )
        settings = config.load_settings()
        for label, key, active in (
            ("At login", "prompt_on_login", settings.prompt_on_login),
            ("After resuming from suspend", "prompt_on_resume", settings.prompt_on_resume),
        ):
            check = Gtk.CheckButton(label=label)
            check.set_active(active)
            # Connected after set_active, so building the tab saves nothing.
            check.connect("toggled", self._on_prompt_toggled, key)
            box.pack_start(check, False, False, 0)

        self.prompt_status_label = Gtk.Label(label="", xalign=0)
        self.prompt_status_label.set_line_wrap(True)
        box.pack_start(self.prompt_status_label, False, False, 0)

        return box

    def _on_prompt_toggled(self, check: Gtk.CheckButton, key: str) -> None:
        try:
            config.update_settings(**{key: check.get_active()})
            self.prompt_status_label.set_text("")
        except OSError as exc:
            self.prompt_status_label.set_text(f"Couldn't save this setting: {exc}")

    # ------------------------------------------------------------------
    def _on_delete_event(self, *_args) -> bool:
        self.hide()
        return True  # prevent actual destroy; tray brings it back


class TimeshiftCompanionApp(Gtk.Application):
    def __init__(self):
        # "_11dash11", not "11dash11": no element of a GApplication ID may
        # start with a digit, and GLib silently dropped the invalid ID,
        # which disabled single-instance behavior (a second launch opened
        # a second window and tray icon). Leading "_" is the standard
        # freedesktop escape for this. Must match the .desktop file name.
        # (The polkit action IDs, io.github.11dash11.timeshiftondemand.*,
        # follow polkit's own rules and are unaffected.)
        super().__init__(application_id="io.github._11dash11.timeshiftondemand")
        # Built on first activation by main.py's "activate" handler, which
        # also creates the tray icon.
        self.window: TimeshiftCompanionWindow | None = None
