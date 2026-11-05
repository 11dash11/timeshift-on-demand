"""Unit tests for BackupRunner's log writing (no GTK, no pkexec).
Run: python3 tests/test_backup_runner.py   (tests/run-tests.sh calls this too)"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import backup_runner  # noqa: E402


class StreamTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        backup_runner.LOGFILE = Path(self.tmp.name) / "backup.log"
        self.r = backup_runner.BackupRunner("uuid")

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


if __name__ == "__main__":
    unittest.main()
