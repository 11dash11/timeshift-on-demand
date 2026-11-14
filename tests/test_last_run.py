"""Unit tests for last_run.py (no GTK).
Run: python3 tests/test_last_run.py   (tests/run-tests.sh calls this too)"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
sys.path.insert(0, SRC)
import last_run  # noqa: E402


class LastRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.tmp.name
        self.path = Path(self.tmp.name) / "timeshift-on-demand" / "last-run.json"

    def tearDown(self):
        if self._old is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._old
        self.tmp.cleanup()

    def test_nothing_recorded(self):
        self.assertIsNone(last_run.load())
        self.assertEqual(last_run.describe(None), "Last backup: no run recorded yet")

    def test_success_round_trip(self):
        last_run.record(last_run.SUCCESS, source="prompt")
        got = last_run.load()
        self.assertEqual((got.outcome, got.reason, got.source), ("success", None, "prompt"))
        self.assertIn("succeeded", last_run.describe(got))
        self.assertIn("login/resume prompt", last_run.describe(got))

    def test_failure_carries_reason_and_newest_wins(self):
        last_run.record(last_run.SUCCESS, source="backup-tab")
        last_run.record(last_run.FAILED, "Check the\ndrive.", source="backup-tab")
        got = last_run.load()
        self.assertEqual(got.outcome, "failed")
        self.assertEqual(got.reason, "Check the drive.")  # whitespace collapsed
        text = last_run.describe(got)
        self.assertIn("FAILED", text)
        self.assertTrue(text.endswith("Check the drive."))

    def test_layout_puts_the_source_after_last_backup(self):
        at = datetime(datetime.now().year, 11, 14, 12, 7).astimezone()
        when = at.strftime("%a %d %b %H:%M")
        cases = {
            "tray": "Last backup (tray icon)",
            "backup-tab": "Last backup (Backup tab)",
            "prompt": "Last backup (login/resume prompt)",
            None: "Last backup",       # older record, no source
            "elsewhere": "Last backup",  # unknown source: left out
        }
        for source, head in cases.items():
            with self.subTest(source=source):
                ok = last_run.LastRun(at, "success", source=source)
                self.assertEqual(last_run.describe(ok), f"{head}: succeeded — {when}")
                bad = last_run.LastRun(at, "failed", "Check the drive.", source=source)
                self.assertEqual(last_run.describe(bad), f"{head}: FAILED — {when}\nCheck the drive.")
                bare = last_run.LastRun(at, "failed", source=source)
                self.assertEqual(last_run.describe(bare), f"{head}: FAILED — {when}")

    def test_old_cancelled_file_reads_as_nothing(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{"outcome": "cancelled", "finished_at": "2026-11-11T07:55:00"}')
        self.assertIsNone(last_run.load())

    def test_year_shown_only_when_not_current(self):
        this_year = datetime.now().year
        old = last_run.LastRun(datetime(this_year - 1, 3, 2, 10, 0).astimezone(), "success")
        new = last_run.LastRun(datetime(this_year, 1, 2, 10, 0).astimezone(), "success")
        self.assertIn(str(this_year - 1), last_run.describe(old))
        self.assertNotIn(str(this_year), last_run.describe(new))

    def test_reason_is_capped(self):
        last_run.record(last_run.FAILED, "x" * 5000)
        self.assertEqual(len(last_run.load().reason), last_run.MAX_REASON)

    def test_unknown_outcome_ignored(self):
        last_run.record("great", "x")
        self.assertFalse(self.path.exists())

    def test_corrupt_or_odd_files_read_as_nothing(self):
        self.path.parent.mkdir(parents=True)
        for bad in ("{not json", "[]", "null", '{"outcome": "success"}',
                    '{"outcome": "nope", "finished_at": "2026-11-11T07:55:00"}',
                    '{"outcome": "success", "finished_at": "yesterday"}',
                    '{"outcome": "success", "finished_at": "0001-01-01T00:00:00+05:00"}',
                    '{"outcome": "success", "finished_at": "9999-12-31T23:59:59-05:00"}'):
            self.path.write_text(bad)
            self.assertIsNone(last_run.load(), bad)

    def test_odd_field_types_dropped_not_crashed(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps(
            {"outcome": "failed", "finished_at": "2026-11-11T07:55:00", "reason": 5, "source": []}))
        got = last_run.load()
        self.assertEqual((got.reason, got.source), (None, None))

    def test_unwritable_location_does_not_raise(self):
        os.environ["XDG_DATA_HOME"] = "/proc/nope"
        last_run.record(last_run.SUCCESS)  # must not raise

    def test_no_temp_file_left_behind(self):
        last_run.record(last_run.SUCCESS)
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["last-run.json"])

    def test_command_line(self):
        env = dict(os.environ, XDG_DATA_HOME=self.tmp.name)
        r = subprocess.run(
            [sys.executable, os.path.join(SRC, "last_run.py"), "record", "--source", "prompt",
             "--outcome", "failed", "--reason=-starts with a dash"],
            env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        got = last_run.load()
        self.assertEqual((got.outcome, got.reason, got.source), ("failed", "-starts with a dash", "prompt"))


if __name__ == "__main__":
    unittest.main()
