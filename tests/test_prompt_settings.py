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
            ("findmnt", f'echo called >> "{self.findmnt_log}"; printf "%s" "${{FINDMNT_OUT-/mnt/backup}}"'),
        ):
            stub = stubs / name
            stub.write_text(f"#!/bin/bash\n{body}\n")
            stub.chmod(0o755)
        self.config = d / "config" / "timeshift-on-demand" / "config.json"
        self.config.parent.mkdir(parents=True)
        self.log = d / "data" / "timeshift-on-demand" / "backup.log"
        (d / "run").mkdir()  # the script's done-flag goes here, not in the real /run/user
        self.env = dict(
            os.environ,
            XDG_RUNTIME_DIR=str(d / "run"),
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


class PromptMountTests(PromptTests):
    """Drive lookup with Timeshift's own /run/timeshift mount present."""

    def mounted_at(self, findmnt_out):
        self.env["FINDMNT_OUT"] = findmnt_out
        self.run_prompt({}, "--login")
        return self.log.read_text()

    def test_user_mount_first(self):
        log = self.mounted_at("/media/u/drive\n/run/timeshift/1/backup\n")
        self.assertIn("Backup drive mounted at /media/u/drive.", log)

    def test_timeshift_mount_first(self):
        log = self.mounted_at("/run/timeshift/1/backup\n/media/u/drive\n")
        self.assertIn("Backup drive mounted at /media/u/drive.", log)


class PromptFailureTests(PromptTests):
    """The failure notification shows the helper's reason. Runs a copy of
    the script with its absolute helper/monitor paths pointed at stubs."""

    def run_failing(self, helper_output, status):
        d = Path(self.tmp.name)
        helper = d / "helper"
        helper.write_text(f"#!/bin/bash\nprintf '%b' '{helper_output}'\nexit {status}\n")
        helper.chmod(0o755)
        monitor = d / "monitor.py"
        monitor.write_text("")
        script = d / "prompt-under-test"
        script.write_text(
            PROMPT.read_text()
            .replace("/usr/lib/timeshift-on-demand/timeshift-on-demand-backup-helper", str(helper))
            .replace("/usr/lib/timeshift-on-demand/timeshift-on-demand-gui-monitor", str(monitor))
        )
        notify_log = d / "notify.log"
        for name, body in (
            ("zenity", "exit 0"),
            ("pkexec", 'exec "$@"'),
            ("ionice", 'shift 2; exec "$@"'),
            ("nice", 'shift 1; exec "$@"'),
            ("notify-send", f'echo "$@" >> "{notify_log}"'),
        ):
            stub = d / "bin" / name
            stub.write_text(f"#!/bin/bash\n{body}\n")
            stub.chmod(0o755)
        self.config.write_text(json.dumps({"backup_drive_uuid": "abc"}))
        # Earlier run's reason in the same log must not be picked up.
        self.log.parent.mkdir(parents=True, exist_ok=True)
        self.log.write_text("BACKUP-FAILED: stale reason from an older run\n")
        subprocess.run(["bash", str(script)], env=self.env, timeout=30, check=False)
        return notify_log.read_text() if notify_log.exists() else ""

    def test_notification_shows_reason(self):
        out = self.run_failing("Syncing\\n\\nBACKUP-FAILED: Check the drive.\\n", 1)
        self.assertIn("Check the drive.", out)
        self.assertNotIn("stale reason", out)
        self.assertNotIn("Exit code", out)

    def test_reason_found_after_progress_fragment_on_same_line(self):
        out = self.run_failing("5% complete\\rBACKUP-FAILED: Check the drive.\\n", 1)
        self.assertIn("Check the drive.", out)

    def test_no_reason_falls_back_to_exit_code(self):
        out = self.run_failing("boom\\n", 3)
        self.assertIn("Exit code 3", out)
        self.assertNotIn("stale reason", out)


if __name__ == "__main__":
    unittest.main()
