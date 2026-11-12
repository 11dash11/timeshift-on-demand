"""Unit tests for BackupRunner's log writing (no GTK, no pkexec).
Run: python3 tests/test_backup_runner.py   (tests/run-tests.sh calls this too)"""
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import backup_runner  # noqa: E402
import last_run  # noqa: E402


class StreamTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        backup_runner.LOGFILE = Path(self.tmp.name) / "backup.log"
        self.r = backup_runner.BackupRunner()

    def tearDown(self):
        self.tmp.cleanup()

    def file_lines(self):
        p = backup_runner.LOGFILE
        return [l.split("  ", 1)[1] for l in p.read_text().splitlines()] if p.exists() else []

    def test_progress_not_written_each_but_all_queued(self):
        self.r._stream_output(["Start\n", "1.00% complete (a remaining)\n",
                               "2.00% complete (b remaining)\n", "3.00% complete (c remaining)\n",
                               "Done\n"])
        self.assertEqual(len(self.r.poll_log_lines()), 5)  # Backup tab sees every update
        self.assertEqual(self.file_lines(),
                         ["Start", "3.00% complete (c remaining)", "Done"])

    def test_errors_written_in_order_between_runs(self):
        self.r._stream_output(["1.00% complete (x remaining)", "ERROR: disk full",
                               "5.00% complete (y remaining)", "Failed to create snapshot"])
        self.assertEqual(self.file_lines(),
                         ["1.00% complete (x remaining)", "ERROR: disk full",
                          "5.00% complete (y remaining)", "Failed to create snapshot"])

    def test_last_progress_kept_when_stream_ends_or_raises(self):
        def gen():
            yield "40.00% complete (z remaining)"
            raise OSError("pipe broke")
        with self.assertRaises(OSError):
            self.r._stream_output(gen())
        self.assertEqual(self.file_lines(), ["40.00% complete (z remaining)"])

    def test_blank_lines_ignored(self):
        self.r._stream_output(["", "   ", "7.00% complete (q remaining)", "ok"])
        self.assertEqual(self.file_lines(), ["7.00% complete (q remaining)", "ok"])

    def test_failure_reason_captured_from_marker_line(self):
        self.r._stream_output(["Syncing", "", "BACKUP-FAILED: Check the drive.", ""])
        self.assertEqual(self.r._failure_reason, "Check the drive.")
        self.assertIn("BACKUP-FAILED: Check the drive.", self.file_lines())  # still logged

    def test_no_marker_means_no_reason(self):
        self.r._stream_output(["Syncing", "Done"])
        self.assertIsNone(self.r._failure_reason)


class TrimTests(unittest.TestCase):
    """_trim_log with the limits patched small, so no test writes 10 MiB."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = Path(self.tmp.name) / "backup.log"
        self.patches = [
            mock.patch.object(backup_runner, "LOGFILE", self.log),
            mock.patch.object(backup_runner, "LOG_MAX_BYTES", 100),
            mock.patch.object(backup_runner, "LOG_KEEP_BYTES", 40),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_limits_are_10_mib_and_5_mib(self):
        for p in self.patches[1:]:
            p.stop()
        try:
            self.assertEqual(backup_runner.LOG_MAX_BYTES, 10 * 1024 * 1024)
            self.assertEqual(backup_runner.LOG_KEEP_BYTES, 5 * 1024 * 1024)
        finally:
            for p in self.patches[1:]:
                p.start()

    def test_under_or_at_the_limit_is_untouched(self):
        for size in (10, 100):
            self.log.write_bytes(b"x" * size)
            backup_runner._trim_log()
            self.assertEqual(self.log.stat().st_size, size)

    def test_over_the_limit_keeps_only_the_last_keep_bytes(self):
        self.log.write_bytes(b"a" * 61 + b"b" * 40)  # 101 bytes
        backup_runner._trim_log()
        self.assertEqual(self.log.read_bytes(), b"b" * 40)
        self.assertEqual([p.name for p in self.log.parent.iterdir()], ["backup.log"])

    def test_missing_log_and_unwritable_dir_do_not_raise(self):
        backup_runner._trim_log()  # no file
        self.log.write_bytes(b"x" * 101)
        with mock.patch.object(Path, "write_bytes", side_effect=OSError("read-only")):
            backup_runner._trim_log()  # swallowed: a log we can't trim must not stop a backup
        self.assertEqual(self.log.stat().st_size, 101)


class RunBackupOutcomeTests(unittest.TestCase):
    """_run_backup end to end with a fake pkexec; checks the result and the
    last-run record both paths feed the Dashboard from."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        backup_runner.LOGFILE = Path(self.tmp.name) / "backup.log"
        self._old = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.tmp.name
        import status
        self.patches = [
            mock.patch.object(status, "get_timeshift_backup_device_uuid", return_value="u"),
            mock.patch.object(status, "find_backup_mount", return_value="/mnt/b"),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        if self._old is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._old
        self.tmp.cleanup()

    def run_with(self, output, code):
        proc = mock.MagicMock()
        proc.__enter__.return_value = proc
        proc.stdout = iter(output)
        proc.returncode = code
        with mock.patch.object(backup_runner.subprocess, "Popen", return_value=proc):
            return backup_runner.BackupRunner()._run_backup(datetime.now())

    def test_success_recorded(self):
        r = self.run_with(["ok\n"], 0)
        self.assertTrue(r.success)
        self.assertIsNone(r.reason)
        self.assertEqual(last_run.load().outcome, "success")

    def test_failure_uses_helper_reason(self):
        r = self.run_with(["x\n", "\n", "BACKUP-FAILED: Check the drive.\n"], 1)
        self.assertEqual(r.reason, "Check the drive.")
        got = last_run.load()
        self.assertEqual((got.outcome, got.reason, got.source), ("failed", "Check the drive.", "backup-tab"))

    def test_helper_unable_to_start_timeshift_is_failed_not_cancelled(self):
        r = self.run_with(["\n", "BACKUP-FAILED: Timeshift could not be started (is it installed?). Exit code 127.\n"], 1)
        self.assertFalse(r.cancelled)
        self.assertIn("could not be started", r.reason)
        self.assertEqual(last_run.load().outcome, "failed")

    def test_failure_without_helper_reason_still_gets_a_sentence(self):
        r = self.run_with(["boom\n"], 3)
        self.assertIn("exit code 3", r.reason)
        self.assertEqual(last_run.load().outcome, "failed")

    def test_dismissed_dialog_is_cancelled_and_leaves_the_record_alone(self):
        last_run.record(last_run.FAILED, "Check the drive.", source="prompt")
        r = self.run_with([], 126)
        self.assertTrue(r.cancelled)
        self.assertIn("dismissed", r.reason)
        got = last_run.load()  # the earlier failure must still be showing
        self.assertEqual((got.outcome, got.reason), ("failed", "Check the drive."))

    def test_denied_is_failed_with_sentence(self):
        r = self.run_with([], 127)
        self.assertFalse(r.cancelled)
        self.assertIn("denied", r.reason)
        self.assertEqual(last_run.load().outcome, "failed")

    def test_waits_for_the_drive_timeshift_is_configured_with(self):
        import status
        with mock.patch.object(status, "get_timeshift_backup_device_uuid", return_value="ts-uuid"), \
                mock.patch.object(status, "find_backup_mount", return_value="/mnt/b") as find:
            self.run_with(["ok\n"], 0)
        find.assert_called_with("ts-uuid")

    def test_timeshift_without_a_device_says_so_and_does_not_run(self):
        import status
        last_run.record(last_run.SUCCESS, source="prompt")
        with mock.patch.object(status, "get_timeshift_backup_device_uuid", return_value=None), \
                mock.patch.object(backup_runner.subprocess, "Popen") as popen:
            r = backup_runner.BackupRunner()._run_backup(datetime.now())
        popen.assert_not_called()  # no pkexec prompt for nothing
        self.assertFalse(r.drive_mounted)
        self.assertIn("no backup device", r.reason)
        self.assertEqual(last_run.load().source, "prompt")  # record untouched

    def test_drive_that_never_mounts_gets_a_reason(self):
        with mock.patch("status.find_backup_mount", return_value=None), \
                mock.patch.object(backup_runner, "DRIVE_WAIT_ATTEMPTS", 1), \
                mock.patch.object(backup_runner, "DRIVE_WAIT_INTERVAL_S", 0):
            r = backup_runner.BackupRunner()._run_backup(datetime.now())
        self.assertFalse(r.drive_mounted)
        self.assertIn("wasn't mounted", r.reason)

    def test_run_that_never_started_leaves_record_alone(self):
        last_run.record(last_run.SUCCESS, source="prompt")
        with mock.patch("status.find_backup_mount", return_value=None), \
                mock.patch.object(backup_runner, "DRIVE_WAIT_ATTEMPTS", 1), \
                mock.patch.object(backup_runner, "DRIVE_WAIT_INTERVAL_S", 0):
            r = backup_runner.BackupRunner()._run_backup(datetime.now())
        self.assertFalse(r.drive_mounted)
        got = last_run.load()
        self.assertEqual((got.outcome, got.source), ("success", "prompt"))

    def test_runner_error_recorded_as_failed(self):
        r = backup_runner.BackupRunner()
        with mock.patch.object(r, "_run_backup", side_effect=RuntimeError("boom")):
            r._run(None)
        self.assertEqual(r.result.exit_code, -1)
        self.assertIn("boom", r.result.reason)
        self.assertEqual(last_run.load().outcome, "failed")


if __name__ == "__main__":
    unittest.main()
