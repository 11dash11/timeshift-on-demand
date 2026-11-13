"""Unit tests for status.find_backup_mount (no GTK, no real findmnt).
Run: python3 tests/test_status.py   (tests/run-tests.sh calls this too)"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import retention  # noqa: E402
import status  # noqa: E402

USER = "/media/jay77/Timeshift-Jay11"
TS = "/run/timeshift/111930/backup"


def fake_findmnt(stdout):
    return mock.patch.object(
        status, "_run",
        return_value=subprocess.CompletedProcess([], 0, stdout=stdout, stderr=""),
    )


class FindBackupMountTests(unittest.TestCase):
    def test_single_mount(self):
        with fake_findmnt(USER + "\n"):
            self.assertEqual(status.find_backup_mount("u"), USER)

    def test_user_mount_then_timeshift_mount(self):
        with fake_findmnt(f"{USER}\n{TS}\n"):
            self.assertEqual(status.find_backup_mount("u"), USER)

    def test_timeshift_mount_then_user_mount(self):
        with fake_findmnt(f"{TS}\n{USER}\n"):
            self.assertEqual(status.find_backup_mount("u"), USER)

    def test_only_timeshift_mount_counts_as_not_mounted(self):
        # e.g. a stale /run/timeshift mount left by a crashed run
        with fake_findmnt(TS + "\n"):
            self.assertIsNone(status.find_backup_mount("u"))

    def test_not_mounted(self):
        with fake_findmnt(""):
            self.assertIsNone(status.find_backup_mount("u"))

    def test_no_uuid(self):
        self.assertIsNone(status.find_backup_mount(None))


class TimeshiftDeviceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Path(self.tmp.name) / "timeshift.json"
        self.patch = mock.patch.object(status, "TIMESHIFT_CONFIG", self.cfg)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def uuid(self, text):
        self.cfg.write_text(text)
        return status.get_timeshift_backup_device_uuid()

    def test_normal(self):
        self.assertEqual(self.uuid('{"backup_device_uuid": "abc-123"}'), "abc-123")

    def test_empty_or_missing_key(self):
        self.assertIsNone(self.uuid('{"backup_device_uuid": ""}'))
        self.assertIsNone(self.uuid("{}"))

    def test_wrong_types_and_broken_files_mean_none(self):
        for text in ('{"backup_device_uuid": 5}', '{"backup_device_uuid": ["a"]}', "[]", "null",
                     '"text"', "{not json", ""):
            with self.subTest(text=text):
                self.assertIsNone(self.uuid(text))

    def test_missing_file_and_undecodable_bytes(self):
        self.assertIsNone(status.get_timeshift_backup_device_uuid())
        self.cfg.write_bytes(b"\xff\xfe{")
        self.assertIsNone(status.get_timeshift_backup_device_uuid())

    def test_snapshots_error_instead_of_crash_on_non_object_config(self):
        self.cfg.write_text("[]")
        self.assertIn("isn't configured", status.get_snapshots().error)


class SnapshotTagTests(unittest.TestCase):
    """get_snapshots reads each snapshot's tags from its info.json."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.snaps = root / "mnt" / "timeshift" / "snapshots"
        self.snaps.mkdir(parents=True)
        self.cfg = root / "timeshift.json"
        self.cfg.write_text('{"backup_device_uuid": "u"}')
        self.patches = [
            mock.patch.object(status, "TIMESHIFT_CONFIG", self.cfg),
            mock.patch.object(status, "find_backup_mount", return_value=str(root / "mnt")),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def snap(self, name, info):
        d = self.snaps / name
        d.mkdir()
        if info is not None:
            (d / "info.json").write_text(info)

    def test_counts_per_tag_and_overlap(self):
        self.snap("2026-11-01_10-00-00", '{"tags": "weekly"}')
        self.snap("2026-11-12_09-00-00", '{"tags": "daily"}')
        self.snap("2026-11-13_09-00-00", '{"tags": "daily weekly"}')   # two tags on one snapshot
        info = status.get_snapshots()
        self.assertEqual(len(info.names), 3)
        self.assertEqual(info.tag_counts, {"weekly": 2, "daily": 2})   # adds up to 4, not 3
        self.assertEqual(info.latest_tags, "daily weekly")

    def test_list_and_comma_formats_and_odd_files(self):
        self.snap("2026-11-01_10-00-00", '{"tags": ["monthly", "weekly"]}')
        self.snap("2026-11-02_10-00-00", '{"tags": "daily,weekly"}')
        self.snap("2026-11-03_10-00-00", "{not json")
        self.snap("2026-11-04_10-00-00", "[]")
        self.snap("2026-11-05_10-00-00", '{"comments": ""}')              # no tags key
        self.snap("2026-11-06_10-00-00", None)                            # no info.json: incomplete
        info = status.get_snapshots()
        self.assertEqual(info.tag_counts, {"monthly": 1, "weekly": 2, "daily": 1})
        self.assertEqual(info.incomplete, 1)

    def test_sample_config_with_weekly_and_monthly_off_gives_the_warning(self):
        # Neither machine ever shows this warning, so exercise it from a sample
        # timeshift.json (the same file tests/show-dashboard.py uses for screenshots).
        fixture = Path(__file__).resolve().parent / "fixtures" / "timeshift-weekly-monthly-off.json"
        self.cfg.write_text(fixture.read_text())
        r = status.get_retention()
        self.assertEqual((r.enabled["weekly"], r.enabled["monthly"], r.enabled["daily"]), (False, False, True))
        text = retention.describe(r, {"daily": 3})
        self.assertIn("No weekly or monthly restore points are scheduled", text.warning)
        self.assertIn("rolling 2 days (at least 2 snapshots)", text.warning)

    def test_get_retention(self):
        self.assertEqual(status.get_retention().keep["daily"], None)       # config has no counts
        self.cfg.write_text('{"count_daily": "2", "schedule_weekly": "true"}')
        r = status.get_retention()
        self.assertEqual((r.keep["daily"], r.enabled["weekly"]), (2, True))
        self.cfg.write_text("[]")
        self.assertIsNone(status.get_retention())


if __name__ == "__main__":
    unittest.main()
