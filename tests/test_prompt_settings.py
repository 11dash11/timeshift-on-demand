"""Tests timeshift-on-demand-prompt's on/off check with stub zenity and findmnt.
The stub zenity answers No, so no backup and no monitor window ever start.
Run: python3 tests/test_prompt_settings.py   (tests/run-tests.sh calls this too)"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

PROMPT = Path(__file__).resolve().parent.parent / "packaging/bin/timeshift-on-demand-prompt"


class PromptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.zenity_log = d / "zenity.log"
        self.findmnt_log = d / "findmnt.log"
        stubs = d / "bin"
        stubs.mkdir()
        for name, body in (
            ("zenity", f'echo called >> "{self.zenity_log}"; exit 1'),
            ("findmnt", f'echo called >> "{self.findmnt_log}"; echo /mnt/backup'),
        ):
            stub = stubs / name
            stub.write_text(f"#!/bin/bash\n{body}\n")
            stub.chmod(0o755)
        self.config = d / "config" / "timeshift-on-demand" / "config.json"
        self.config.parent.mkdir(parents=True)
        self.log = d / "data" / "timeshift-on-demand" / "backup.log"
        self.env = dict(
            os.environ,
            PATH=f"{stubs}:{os.environ['PATH']}",
            XDG_CONFIG_HOME=str(d / "config"),
            XDG_DATA_HOME=str(d / "data"),
        )

    def tearDown(self):
        self.tmp.cleanup()

    def run_prompt(self, settings, *args):
        if isinstance(settings, str):
            self.config.write_text(settings)
        else:
            self.config.write_text(json.dumps({"backup_drive_uuid": "abc", **settings}))
        result = subprocess.run(["bash", str(PROMPT), *args], env=self.env, timeout=30, check=False)
        self.assertEqual(result.returncode, 0)
        return self.zenity_log.exists()

    def test_login_off_stops_before_drive_wait_and_says_why(self):
        self.assertFalse(self.run_prompt({"prompt_on_login": False}, "--login"))
        self.assertFalse(self.findmnt_log.exists())
        self.assertIn("prompt_on_login is off", self.log.read_text())

    def test_resume_off_stops(self):
        self.assertFalse(self.run_prompt({"prompt_on_resume": False}, "--resume"))
        self.assertIn("prompt_on_resume is off", self.log.read_text())

    def test_settings_are_separate(self):
        self.assertTrue(self.run_prompt({"prompt_on_login": False}, "--resume"))
        self.zenity_log.unlink()
        self.assertTrue(self.run_prompt({"prompt_on_resume": False}, "--login"))

    def test_on_by_default(self):
        self.assertTrue(self.run_prompt({}, "--login"))

    def test_no_flag_always_prompts(self):
        self.assertTrue(self.run_prompt({"prompt_on_login": False, "prompt_on_resume": False}))

    def test_non_false_values_leave_it_on(self):
        self.assertTrue(self.run_prompt({"prompt_on_login": "false"}, "--login"))

    def test_broken_config_does_not_turn_it_off(self):
        # The drive lookup also fails on this, so the script stops at "no
        # drive", but it must not report the prompt as off.
        self.run_prompt("{not json", "--login")
        self.assertNotIn("is off", self.log.read_text())
        self.assertIn("No backup drive configured", self.log.read_text())


if __name__ == "__main__":
    unittest.main()
