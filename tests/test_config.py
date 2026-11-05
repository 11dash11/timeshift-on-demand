"""Unit tests for src/config.py.
Run: python3 tests/test_config.py   (tests/run-tests.sh calls this too)"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import config  # noqa: E402


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        config.CONFIG_PATH = Path(self.tmp.name) / "sub" / "config.json"

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text):
        config.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.CONFIG_PATH.write_text(text)

    def test_missing_file_means_prompts_on(self):
        s = config.load_settings()
        self.assertIsNone(s.backup_drive_uuid)
        self.assertTrue(s.prompt_on_login)
        self.assertTrue(s.prompt_on_resume)

    def test_existing_config_without_the_new_keys_keeps_prompts_on(self):
        self.write(json.dumps({"backup_drive_uuid": "abc"}))
        s = config.load_settings()
        self.assertEqual(s.backup_drive_uuid, "abc")
        self.assertTrue(s.prompt_on_login and s.prompt_on_resume)

    def test_only_explicit_false_turns_a_prompt_off(self):
        self.write(json.dumps({"prompt_on_login": False, "prompt_on_resume": "false"}))
        s = config.load_settings()
        self.assertFalse(s.prompt_on_login)
        self.assertTrue(s.prompt_on_resume)

    def test_broken_or_non_object_config_means_defaults(self):
        for text in ("{not json", "[]", "null"):
            with self.subTest(text=text):
                self.write(text)
                self.assertEqual(config.load_settings(), config.Settings())

    def test_saving_or_clearing_the_drive_keeps_prompt_settings(self):
        config.update_settings(prompt_on_login=False, prompt_on_resume=False)
        config.update_settings(backup_drive_uuid="abc")
        config.update_settings(backup_drive_uuid=None)
        s = config.load_settings()
        self.assertFalse(s.prompt_on_login)
        self.assertFalse(s.prompt_on_resume)

    def test_toggling_a_prompt_keeps_the_drive_and_other_prompt(self):
        config.update_settings(backup_drive_uuid="abc")
        config.update_settings(prompt_on_resume=False)
        s = config.load_settings()
        self.assertEqual(s.backup_drive_uuid, "abc")
        self.assertTrue(s.prompt_on_login)
        self.assertFalse(s.prompt_on_resume)


if __name__ == "__main__":
    unittest.main()
