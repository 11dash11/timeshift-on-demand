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


if __name__ == "__main__":
    unittest.main()
