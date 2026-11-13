"""Tests the window's tray callback and notification rules (needs a display;
skipped without one, e.g. in a package build).
Run: python3 tests/test_app_tray.py   (tests/run-tests.sh calls this too)"""
import os
import sys
import tempfile
import unittest
from datetime import datetime
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

try:
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk

    HAVE_DISPLAY = Gtk.init_check()[0]
except Exception:  # no PyGObject / no display
    HAVE_DISPLAY = False

if HAVE_DISPLAY:
    import app as A
    import backup_runner


def result(code=0, mounted=True, reason=None):
    return backup_runner.BackupResult(datetime.now(), datetime.now(), code, mounted, reason)


@unittest.skipUnless(HAVE_DISPLAY, "needs a display")
class TrayWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Registered once (a second registration clashes on the session bus);
        # that emits "startup", which windows must wait for.
        cls.app = Gtk.Application(application_id="test.timeshiftondemand.tray")
        cls.app.register(None)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._old = {k: os.environ.get(k) for k in ("XDG_DATA_HOME", "XDG_CONFIG_HOME")}
        os.environ["XDG_DATA_HOME"] = os.environ["XDG_CONFIG_HOME"] = self.tmp.name
        self.win = A.TimeshiftCompanionWindow(self.app)
        self.calls = []
        self.win.on_running_changed = self.calls.append
        # Gio.Notification can't be read back, so replace Gio with a mock
        # that records each notification's title, body and priority.
        self.sent = []
        self.gio = mock.patch.object(A, "Gio")
        gio = self.gio.start()

        def new_note(title):
            note = mock.Mock()
            note.title = title
            return note

        gio.Notification.new.side_effect = new_note
        app = mock.Mock()
        app.send_notification.side_effect = lambda i, n: self.sent.append(n)
        self.win.get_application = lambda: app
        self.active = False
        self.win.is_active = lambda: self.active

    def tearDown(self):
        self.gio.stop()
        self.win.destroy()
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def test_no_maintenance_tab(self):
        notebook = self.win.get_child()
        labels = [notebook.get_tab_label_text(notebook.get_nth_page(i)) for i in range(notebook.get_n_pages())]
        self.assertEqual(labels, ["Dashboard", "Backup", "Settings"])

    def dashboard(self, config, snaps):
        """The retention part of the Dashboard as a dict of what is shown."""
        import status
        with mock.patch.object(status, "get_retention",
                               return_value=A.retention.from_config(config) if config is not None else None), \
                mock.patch.object(status, "get_snapshots", return_value=snaps):
            self.win._refresh_snapshot_info()
        w = self.win
        return {
            "retention": w.retention_label.get_text(),
            "tags": w.tags_label.get_text() if w.tags_label.get_visible() else None,
            "warning": w.retention_warning_label.get_text() if w.retention_warning_label.get_visible() else None,
            "warning_markup": w.retention_warning_label.get_label(),
        }

    NORMAL = {"schedule_daily": "true", "schedule_weekly": "true", "schedule_monthly": "true",
              "count_daily": "2", "count_weekly": "2", "count_monthly": "1"}

    def test_dashboard_retention_lines_and_no_warning_normally(self):
        import status
        snaps = status.SnapshotInfo(names=["a", "b", "c"], tag_counts={"daily": 3, "weekly": 1})
        d = self.dashboard(self.NORMAL, snaps)
        self.assertIn("rolling 2 days (at least 2 snapshots)", d["retention"])
        self.assertIn("Daily on (keep 2)", d["retention"])
        self.assertEqual(d["tags"], "Snapshots by tag: daily 3, weekly 1 (tags overlap, so these can add up "
                                    "to more than the 3 total)")
        self.assertIsNone(d["warning"])
        self.assertEqual(self.win.tags_label.get_tooltip_text(), A.retention.TAGS_NOTE)

    def test_warning_shows_bold_and_plain_when_weekly_and_monthly_are_off(self):
        import json
        import status
        fixture = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures",
                               "timeshift-weekly-monthly-off.json")
        with open(fixture, encoding="utf-8") as f:
            cfg = json.load(f)
        d = self.dashboard(cfg, status.SnapshotInfo(names=["a"], tag_counts={"daily": 1}))
        self.assertIn("No weekly or monthly restore points are scheduled", d["warning"])
        self.assertTrue(d["warning_markup"].startswith("<b>"))
        self.assertNotIn("color", d["warning_markup"])          # information, not an error colour
        # and it goes away again once Weekly is on
        d = self.dashboard({**cfg, "schedule_weekly": "true"}, status.SnapshotInfo(names=["a"]))
        self.assertIsNone(d["warning"])

    def test_dashboard_retention_without_a_readable_drive_or_config(self):
        import status
        d = self.dashboard({"schedule_weekly": "true", "count_daily": "2"},
                           status.SnapshotInfo(error="Timeshift's backup drive isn't mounted"))
        self.assertIn("rolling 2 days", d["retention"])        # levels still shown from the config
        self.assertIsNone(d["tags"])                           # tag counts need the drive
        d = self.dashboard(None, status.SnapshotInfo())
        self.assertIn("unavailable", d["retention"])
        self.assertIsNone(d["warning"])

    def test_start_sets_running_once_and_second_click_does_nothing(self):
        self.win.runner.is_running = False
        with mock.patch.object(self.win.runner, "start") as start:
            start.side_effect = lambda on_done: setattr(self.win.runner, "is_running", True)
            self.win._on_trigger_clicked()
            self.win._on_trigger_clicked()  # guard: already running
        self.assertEqual(self.calls, [True])
        self.assertEqual(start.call_count, 1)

    def test_every_ending_restores_idle_first(self):
        endings = {
            "success": result(0),
            "failed": result(1, reason="x"),
            "dialog dismissed": result(126, reason="x"),
            "never started": result(0, mounted=False, reason="x"),
            "runner error": result(-1, reason="x"),
        }
        for name, res in endings.items():
            with self.subTest(name):
                self.calls.clear()
                self.win._on_backup_done_main_thread(res)
                self.assertEqual(self.calls, [False])

    def test_no_tray_means_no_callback_and_no_error(self):
        self.win.on_running_changed = None
        self.win._on_backup_done_main_thread(result(0))

    def test_a_failing_tray_callback_never_blocks_the_windows_recovery(self):
        self.win.on_running_changed = mock.Mock(side_effect=RuntimeError("tray gone"))
        self.win.trigger_btn.set_sensitive(False)
        self.win._on_backup_done_main_thread(result(1, reason="Check the drive."))
        self.assertTrue(self.win.trigger_btn.get_sensitive())      # button re-enabled
        self.assertIn("Backup failed", self.win.status_line.get_text())
        self.assertEqual(len(self.sent), 1)                         # notification still sent

    def test_a_failing_tray_callback_does_not_stop_a_start(self):
        self.win.runner.is_running = False
        self.win.on_running_changed = mock.Mock(side_effect=RuntimeError("tray gone"))
        with mock.patch.object(self.win.runner, "start") as start:
            self.win._on_trigger_clicked()
        start.assert_called_once()

    def test_running_state_ends_even_if_a_later_step_raises(self):
        with mock.patch.object(self.win, "refresh_dashboard", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.win._on_backup_done_main_thread(result(0))
        self.assertEqual(self.calls, [False])

    def titles(self):
        return [n.title for n in self.sent]

    def test_failure_always_notifies_urgent_even_with_window_in_front(self):
        for active in (False, True):
            with self.subTest(active=active):
                self.sent.clear()
                self.active = active
                self.win._on_backup_done_main_thread(result(1, reason="Check the drive."))
                self.assertEqual(len(self.sent), 1)
                self.assertIn("FAILED", self.titles()[0])
                self.sent[0].set_body.assert_called_with("Check the drive.")
                self.sent[0].set_priority.assert_called_once()  # urgent

    def test_success_and_cancelled_skipped_when_window_in_front_only(self):
        for res in (result(0), result(126, reason="Dismissed.")):
            self.active = True
            self.sent.clear()
            self.win._on_backup_done_main_thread(res)
            self.assertEqual(self.sent, [])
            self.active = False
            self.win._on_backup_done_main_thread(res)
            self.assertEqual(len(self.sent), 1)
            self.sent[0].set_priority.assert_not_called()  # not urgent

    def test_never_started_run_gets_a_plain_notice_when_window_not_in_front(self):
        self.active = False
        self.win._on_backup_done_main_thread(result(0, mounted=False, reason="No device."))
        self.assertEqual(len(self.sent), 1)
        self.assertIn("did not run", self.titles()[0])
        self.sent.clear()
        self.active = True
        self.win._on_backup_done_main_thread(result(0, mounted=False, reason="No device."))
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
