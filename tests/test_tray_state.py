"""Unit tests for tray_state.py (no GTK).
Run: python3 tests/test_tray_state.py   (tests/run-tests.sh calls this too)"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import tray_state  # noqa: E402


class TrayStateTests(unittest.TestCase):
    def test_idle(self):
        s = tray_state.tray_state(False)
        self.assertEqual((s.label, s.item_sensitive, s.attention_icon),
                         ("Backup Now", True, False))

    def test_running(self):
        s = tray_state.tray_state(True)
        self.assertEqual((s.label, s.item_sensitive, s.attention_icon),
                         ("Backup running…", False, True))

    def test_label_and_clickability_never_disagree(self):
        for running in (True, False):
            s = tray_state.tray_state(running)
            self.assertEqual(s.label == tray_state.IDLE_LABEL, s.item_sensitive)
            self.assertEqual(s.attention_icon, running)


if __name__ == "__main__":
    unittest.main()
