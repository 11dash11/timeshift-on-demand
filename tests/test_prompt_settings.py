"""Tests timeshift-on-demand-prompt's on/off check and drive lookup with stub zenity and findmnt.
The stub zenity answers No, so no backup and no monitor window ever start.
Run: python3 tests/test_prompt_settings.py   (tests/run-tests.sh calls this too)"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

PROMPT = Path(__file__).resolve().parent.parent / "packaging/bin/timeshift-on-demand-prompt"
LAST_RUN = Path(__file__).resolve().parent.parent / "src/last_run.py"


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
            ("findmnt", f'echo "$*" >> "{self.findmnt_log}"; printf "%s" "${{FINDMNT_OUT-/mnt/backup}}"'),
        ):
            stub = stubs / name
            stub.write_text(f"#!/bin/bash\n{body}\n")
            stub.chmod(0o755)
        self.config = d / "config" / "timeshift-on-demand" / "config.json"
        self.config.parent.mkdir(parents=True)
        self.log = d / "data" / "timeshift-on-demand" / "backup.log"
        # Timeshift's config (the drive to wait for) is read from a fixed
        # /etc path, so run a copy of the script pointed at a test file.
        self.ts_config = d / "timeshift.json"
        self.ts_config.write_text(json.dumps({"backup_device_uuid": "ts-uuid-1"}))
        self.script = d / "prompt-under-test"
        self.script.write_text(PROMPT.read_text().replace("/etc/timeshift/timeshift.json", str(self.ts_config)))
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
            self.config.write_text(json.dumps(settings))
        result = subprocess.run(["bash", str(self.script), *args], env=self.env, timeout=30, check=False)
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
        # The prompt settings are unreadable, so they default to on and the
        # script carries on to the drive wait and the dialog.
        self.assertTrue(self.run_prompt("{not json", "--login"))
        self.assertNotIn("is off", self.log.read_text())

    def test_waits_for_the_drive_timeshift_is_configured_with(self):
        self.run_prompt({}, "--login")
        self.assertIn("UUID=ts-uuid-1", self.findmnt_log.read_text())

    def test_old_drive_setting_in_config_is_ignored(self):
        self.run_prompt({"backup_drive_uuid": "old-companion-uuid"}, "--login")
        self.assertIn("UUID=ts-uuid-1", self.findmnt_log.read_text())
        self.assertNotIn("old-companion-uuid", self.findmnt_log.read_text())

    def test_timeshift_without_a_device_stops_and_says_why(self):
        for text in ('{"backup_device_uuid": ""}', "{}", "[]", "{not json", '{"backup_device_uuid": 7}'):
            with self.subTest(text=text):
                self.findmnt_log.unlink(missing_ok=True)
                self.ts_config.write_text(text)
                self.assertFalse(self.run_prompt({}, "--login"))  # no dialog
                self.assertFalse(self.findmnt_log.exists())       # no drive wait
                self.assertIn("Timeshift has no backup device configured", self.log.read_text())

    def test_missing_timeshift_config_stops_too(self):
        self.ts_config.unlink()
        self.assertFalse(self.run_prompt({}, "--login"))
        self.assertIn("Timeshift has no backup device configured", self.log.read_text())


class PromptTrimTests(PromptTests):
    """The prompt script trims backup.log past 10 MiB to its last 5 MiB.
    Sparse files, so the tests don't write 10 MiB of real data."""

    MAX, KEEP = 10 * 1024 * 1024, 5 * 1024 * 1024

    def make_log(self, size, tail=b"TAILMARK\n"):
        self.log.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log, "wb") as f:
            f.truncate(size - len(tail))
            f.seek(0, os.SEEK_END)
            f.write(tail)

    def run_off(self):
        # prompt_on_login off: stops right after the trim, before any wait
        self.run_prompt({"prompt_on_login": False}, "--login")

    def test_over_10_mib_is_cut_to_the_last_5_mib(self):
        self.make_log(self.MAX + 1)
        self.run_off()
        data = self.log.read_bytes()
        self.assertLess(len(data), self.KEEP + 4096)       # 5 MiB plus this run's own lines
        self.assertGreater(len(data), self.KEEP - 4096)
        self.assertIn(b"TAILMARK", data)                   # the newest end is what's kept
        self.assertIn(b"Prompt script started", data)

    def test_at_exactly_10_mib_is_left_alone(self):
        self.make_log(self.MAX)
        self.run_off()
        self.assertGreaterEqual(self.log.stat().st_size, self.MAX)

    def test_a_5_mib_log_is_not_touched(self):
        self.make_log(self.KEEP)
        self.run_off()
        self.assertGreaterEqual(self.log.stat().st_size, self.KEEP)


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
            .replace("/etc/timeshift/timeshift.json", str(self.ts_config))
            .replace("/usr/lib/timeshift-on-demand/timeshift-on-demand-backup-helper", str(helper))
            .replace("/usr/lib/timeshift-on-demand/timeshift-on-demand-gui-monitor", str(monitor))
            .replace("/usr/share/timeshift-on-demand/last_run.py", str(LAST_RUN))
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
        self.config.write_text("{}")
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

    def recorded(self):
        path = Path(self.tmp.name) / "data" / "timeshift-on-demand" / "last-run.json"
        return json.loads(path.read_text())

    def test_failure_recorded_for_the_dashboard(self):
        self.run_failing("BACKUP-FAILED: Check the drive.\\n", 1)
        got = self.recorded()
        self.assertEqual((got["outcome"], got["reason"], got["source"]),
                         ("failed", "Check the drive.", "prompt"))

    def test_failure_without_reason_recorded_with_sentence(self):
        self.run_failing("boom\\n", 3)
        self.assertIn("exit code 3", self.recorded()["reason"])

    def test_success_recorded(self):
        self.run_failing("fine\\n", 0)
        self.assertEqual(self.recorded()["outcome"], "success")

    def test_dismissed_dialog_leaves_the_record_alone(self):
        d = Path(self.tmp.name) / "data" / "timeshift-on-demand"
        d.mkdir(parents=True)
        (d / "last-run.json").write_text(json.dumps(
            {"finished_at": "2026-11-08T11:59:00+02:00", "outcome": "failed",
             "reason": "earlier failure", "source": "prompt"}))
        self.run_failing("", 126)
        self.assertEqual(self.recorded()["reason"], "earlier failure")

    def test_helper_unable_to_start_timeshift_is_a_failure_not_a_cancel(self):
        # The helper turns the shell's 127 into exit 1 plus this reason
        # (see the helper), so it is recorded and notified as a failure.
        out = self.run_failing(
            "BACKUP-FAILED: Timeshift could not be started (is it installed?). Exit code 127.\\n", 1)
        got = self.recorded()
        self.assertEqual(got["outcome"], "failed")
        self.assertIn("could not be started", got["reason"])
        self.assertIn("could not be started", out)

    def test_no_reason_falls_back_to_exit_code(self):
        out = self.run_failing("boom\\n", 3)
        self.assertIn("Exit code 3", out)
        self.assertNotIn("stale reason", out)


if __name__ == "__main__":
    unittest.main()
