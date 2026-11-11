"""Unit tests for status.find_backup_mount (no GTK, no real findmnt).
Run: python3 tests/test_status.py   (tests/run-tests.sh calls this too)"""
import os
import subprocess
import sys
import unittest
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


if __name__ == "__main__":
    unittest.main()
