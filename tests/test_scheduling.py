"""Unit tests for src/scheduling.py (read-only cron-file explanation).
Run: python3 tests/test_scheduling.py   (tests/run-tests.sh calls this too)"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import scheduling  # noqa: E402


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        scheduling.CRON_FILE_LEGACY = d / "timeshift-hourly"
        scheduling.TIMESHIFT_CONFIG = d / "timeshift.json"

    def tearDown(self):
        self.tmp.cleanup()

    def state(self, cron, config):
        if cron:
            scheduling.CRON_FILE_LEGACY.write_text("x")
        if config is not None:
            scheduling.TIMESHIFT_CONFIG.write_text(config)
        return scheduling.check_cron_integrity()

    def test_no_file(self):
        r = self.state(False, json.dumps({"schedule_daily": "true"}))
        self.assertFalse(r.legacy_cron_present)
        self.assertIn("nothing to explain", r.detail)

    def test_file_with_schedule_on_is_expected(self):
        r = self.state(True, json.dumps({"schedule_daily": "true"}))
        self.assertTrue(r.schedule_enabled)
        self.assertIn("expected Timeshift", r.detail)

    def test_file_with_schedule_off(self):
        r = self.state(True, json.dumps({"schedule_daily": "false"}))
        self.assertIs(r.schedule_enabled, False)
        self.assertIn("levels are all off", r.detail)

    def test_unreadable_config_does_not_claim_schedule_is_off(self):
        for config in (None, "{not json"):
            with self.subTest(config=config):
                scheduling.TIMESHIFT_CONFIG.unlink(missing_ok=True)
                r = self.state(True, config)
                self.assertIsNone(r.schedule_enabled)
                self.assertIn("can't tell", r.detail)
                self.assertNotIn("all off", r.detail)


if __name__ == "__main__":
    unittest.main()
