"""
app.py — GTK3 main window: Dashboard / Backup / Settings / Maintenance tabs.

Consolidates what used to be three separate surfaces (zenity dialog +
spawned terminal monitor, timeshift-status.sh output, and the cron
check/fix scripts) into one window. No time-window restriction — the
Backup tab's trigger button is always enabled.

The backup drive is no longer a hardcoded UUID (that assumed this one
machine) — it's read from config.py, set via the Settings tab. If unset,
the Backup tab still works (the button is always clickable) but reports
"no drive configured" instead of guessing one. Snapshot management stays
Timeshift's own job: the Dashboard shows a read-only glance plus an
"Open Timeshift" button, no delete/restore capability here.
"""

from __future__ import annotations

import subprocess

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

import config  # noqa: E402
import scheduling  # noqa: E402
import status  # noqa: E402
from backup_runner import BackupRunner  # noqa: E402

LOG_POLL_INTERVAL_MS = 500
STATUS_REFRESH_INTERVAL_MS = 30_000


class TimeshiftCompanionWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application):
        super().__init__(application=app, title="Timeshift On Demand")
        self.set_default_size(720, 480)

        self.runner = BackupRunner(config.load_settings().backup_drive_uuid)

        notebook = Gtk.Notebook()
        self.add(notebook)

        self.dashboard = self._build_dashboard_tab()
        notebook.append_page(self.dashboard, Gtk.Label(label="Dashboard"))

        self.backup_tab = self._build_backup_tab()
        notebook.append_page(self.backup_tab, Gtk.Label(label="Backup"))

        self.settings_tab = self._build_settings_tab()
        notebook.append_page(self.settings_tab, Gtk.Label(label="Settings"))

        self.maintenance_tab = self._build_maintenance_tab()
        notebook.append_page(self.maintenance_tab, Gtk.Label(label="Maintenance"))

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

        self.snapshot_label = Gtk.Label(label="Loading snapshot history...", xalign=0)
        self.disk_label = Gtk.Label(label="Loading disk usage...", xalign=0)

        advisory = Gtk.Label(
            label=(
                "This is a read-only glance. Companion cannot browse, restore, "
                "or delete snapshots — use “Open Timeshift” below for that; "
                "it's the only way to work with your snapshot data.\n"
                "Note: on-demand backups aren't automatically pruned by "
                "Timeshift's own retention settings — they can accumulate "
                "indefinitely, so check in and delete old ones in Timeshift "
                "occasionally."
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

        for w in (self.snapshot_label, self.disk_label, btn_row, advisory):
            box.pack_start(w, False, False, 0)

        return box

    def refresh_dashboard(self) -> None:
        """
        Full refresh: snapshot info (privileged — a real pkexec call each
        time, see status.get_snapshots()) plus drive status (cheap,
        unprivileged). Call this only from explicit user actions or
        one-time triggers — window open, a manual Refresh click, once
        after a backup completes — never from the silent auto-refresh
        timer (see _on_status_refresh_tick, which calls
        _refresh_drive_status() only). Prompting for authentication every
        30 seconds just because the window is open would be exactly the
        kind of nagging this project has otherwise avoided.
        """
        self._refresh_snapshot_info()
        self._refresh_drive_status()

    def _refresh_snapshot_info(self) -> None:
        snaps = status.get_snapshots()
        if snaps.error:
            self.snapshot_label.set_text(f"Snapshots: unavailable — {snaps.error}")
        else:
            count = len(snaps.tags)
            latest = snaps.latest or "none"
            self.snapshot_label.set_text(f"Snapshots: {count} total, latest {latest}")

    def _refresh_drive_status(self) -> None:
        drive_uuid = config.load_settings().backup_drive_uuid
        if not drive_uuid:
            self.disk_label.set_text("Backup drive: not configured — see the Settings tab")
            return

        mount = status.find_backup_mount(drive_uuid)
        if not mount:
            self.disk_label.set_text(f"Backup drive ({drive_uuid}): not currently mounted")
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
        # Deliberately drive-status only — see refresh_dashboard()'s
        # docstring for why snapshot info is excluded from this timer.
        self._refresh_drive_status()
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
        box.pack_start(scroller, True, True, 0)

        return box

    def _on_trigger_clicked(self, *_args) -> None:
        if self.runner.is_running:
            return
        self.trigger_btn.set_sensitive(False)
        self.status_line.set_text("Backup running...")
        self.runner.start(on_done=self._on_backup_done_from_thread)

    def _on_backup_done_from_thread(self, result) -> None:
        # Called from the worker thread — hop back to the GTK main loop
        # before touching any widgets.
        GLib.idle_add(self._on_backup_done_main_thread, result)

    def _on_backup_done_main_thread(self, result) -> bool:
        self.trigger_btn.set_sensitive(True)
        if not result.drive_mounted:
            if not self.runner.drive_uuid:
                self.status_line.set_text("No backup drive configured — see the Settings tab.")
            else:
                # Covers "drive not mounted", "Timeshift itself has no
                # backup device configured", and "Companion/Timeshift
                # device UUIDs don't match" — all three set
                # drive_mounted=False; the exact reason is already in the
                # log view above, no need to guess at one specific reason
                # here and risk stating the wrong one.
                self.status_line.set_text("Backup did not run — see the log above for why.")
        elif result.success:
            self.status_line.set_text("Backup completed successfully.")
        else:
            self.status_line.set_text(f"Backup failed (exit code {result.exit_code}).")

        # Only spend the privileged snapshot-list refresh (a real pkexec
        # prompt each time, see status.get_snapshots()) when a snapshot
        # may actually have been created. A failed or skipped attempt
        # didn't change the snapshot count, so re-fetching it would just
        # be another authentication prompt for no new information —
        # confirmed as real friction during Samsung RF511 testing, where
        # repeated failed attempts each cost two prompts instead of one.
        if result.success:
            self.refresh_dashboard()
        else:
            self._refresh_drive_status()
        return False  # one-shot idle call

    def _on_log_poll_tick(self) -> bool:
        for line in self.runner.poll_log_lines():
            end_iter = self.log_buffer.get_end_iter()
            self.log_buffer.insert(end_iter, line + "\n")
        # Auto-scroll to the bottom
        mark = self.log_buffer.get_insert()
        self.log_view.scroll_to_mark(mark, 0.0, False, 0.0, 0.0)
        return True

    # ------------------------------------------------------------------
    # Settings tab
    # ------------------------------------------------------------------
    def _build_settings_tab(self) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(12)

        box.pack_start(Gtk.Label(label="Backup drive:", xalign=0), False, False, 0)

        self.drive_combo = Gtk.ComboBoxText()
        box.pack_start(self.drive_combo, False, False, 0)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        refresh_btn = Gtk.Button(label="Refresh List")
        refresh_btn.connect("clicked", lambda *_: self._populate_drive_combo())
        save_btn = Gtk.Button(label="Save Selection")
        save_btn.connect("clicked", self._on_save_drive_clicked)
        clear_btn = Gtk.Button(label="Clear")
        clear_btn.connect("clicked", self._on_clear_drive_clicked)
        for w in (refresh_btn, save_btn, clear_btn):
            btn_row.pack_start(w, False, False, 0)
        box.pack_start(btn_row, False, False, 0)

        self.settings_status_label = Gtk.Label(label="", xalign=0)
        self.settings_status_label.set_line_wrap(True)
        box.pack_start(self.settings_status_label, False, False, 0)

        self._drive_uuids_by_index: list[str] = []
        self._populate_drive_combo()

        return box

    def _populate_drive_combo(self) -> None:
        self.drive_combo.remove_all()
        self._drive_uuids_by_index = []

        for d in status.list_candidate_drives():
            kind = "removable" if d.removable else "fixed"
            mount = d.mountpoint or "not mounted"
            label = d.label or "(no label)"
            self.drive_combo.append_text(f"{label} — {d.size or '?'} ({kind}) [{mount}] — {d.uuid}")
            self._drive_uuids_by_index.append(d.uuid)

        current = config.load_settings().backup_drive_uuid
        if current and current in self._drive_uuids_by_index:
            self.drive_combo.set_active(self._drive_uuids_by_index.index(current))
            self.settings_status_label.set_text(f"Currently configured: {current}")
        elif current:
            self.settings_status_label.set_text(
                f"Currently configured: {current} (not currently detected — plug it in, then Refresh)"
            )
        else:
            self.settings_status_label.set_text("No backup drive configured yet — pick one above and Save.")

    def _on_save_drive_clicked(self, *_args) -> None:
        idx = self.drive_combo.get_active()
        if idx < 0 or idx >= len(self._drive_uuids_by_index):
            self.settings_status_label.set_text("Pick a drive from the list first.")
            return
        uuid = self._drive_uuids_by_index[idx]
        config.save_settings(config.Settings(backup_drive_uuid=uuid))
        self.runner.drive_uuid = uuid
        self.settings_status_label.set_text(f"Saved. Backup drive set to: {uuid}")
        # Drive-status only, not the full refresh_dashboard() — picking a
        # drive doesn't change the snapshot count, so there's no reason to
        # spend a pkexec prompt on it (see refresh_dashboard()'s docstring).
        self._refresh_drive_status()

    def _on_clear_drive_clicked(self, *_args) -> None:
        config.save_settings(config.Settings(backup_drive_uuid=None))
        self.runner.drive_uuid = None
        self.settings_status_label.set_text("Cleared — no backup drive configured.")
        self._refresh_drive_status()

    # ------------------------------------------------------------------
    # Maintenance tab
    # ------------------------------------------------------------------
    def _build_maintenance_tab(self) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(12)

        self.cron_check_label = Gtk.Label(label="Cron integrity: not yet checked.", xalign=0)
        self.cron_check_label.set_line_wrap(True)
        box.pack_start(self.cron_check_label, False, False, 0)

        check_btn = Gtk.Button(label="Check Scheduling Integrity")
        check_btn.connect("clicked", self._on_check_scheduling_clicked)
        box.pack_start(check_btn, False, False, 0)

        fix_btn = Gtk.Button(label="Fix Scheduling (disables a reappeared legacy cron file)")
        fix_btn.connect("clicked", self._on_fix_scheduling_clicked)
        box.pack_start(fix_btn, False, False, 0)

        self.fix_result_label = Gtk.Label(label="", xalign=0)
        self.fix_result_label.set_line_wrap(True)
        box.pack_start(self.fix_result_label, False, False, 0)

        return box

    def _on_check_scheduling_clicked(self, *_args) -> None:
        result = scheduling.check_cron_integrity()
        self.cron_check_label.set_text(f"Cron integrity: {result.detail}")

    def _on_fix_scheduling_clicked(self, *_args) -> None:
        ok, detail = scheduling.fix_scheduling()
        # "Fix not applied" rather than "ran with errors" — `not ok` now
        # also covers fix_scheduling()'s deliberate refusal to run at all
        # when Timeshift's own scheduling is enabled (see scheduling.py),
        # which isn't an error, just an informed no-op.
        prefix = "Fix applied successfully." if ok else "Fix not applied:"
        self.fix_result_label.set_text(f"{prefix}\n{detail}")
        # Drive-status only — a cron/scheduling fix doesn't touch the
        # snapshot count either, same reasoning as the Settings tab above.
        self._refresh_drive_status()

    # ------------------------------------------------------------------
    def _on_delete_event(self, *_args) -> bool:
        self.hide()
        return True  # prevent actual destroy; tray brings it back


class TimeshiftCompanionApp(Gtk.Application):
    def __init__(self):
        # Placeholder namespace pending a real reverse-DNS ID once
        # published — see PROJECT.md, "Naming".
        super().__init__(application_id="org.timeshiftondemand.app")
        self.window: TimeshiftCompanionWindow | None = None

    def do_activate(self) -> None:
        if not self.window:
            self.window = TimeshiftCompanionWindow(self)
        self.window.present()
