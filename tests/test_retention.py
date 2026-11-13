"""Unit tests for retention.py (no GTK).
Run: python3 tests/test_retention.py   (tests/run-tests.sh calls this too)"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import retention  # noqa: E402

REAL = {  # what both machines have
    "schedule_monthly": "true", "schedule_weekly": "true", "schedule_daily": "true",
    "schedule_hourly": "false", "schedule_boot": "false",
    "count_monthly": "1", "count_weekly": "2", "count_daily": "2",
    "count_hourly": "6", "count_boot": "5",
}


def text(config, tag_counts=None):
    return retention.describe(retention.from_config(config), tag_counts)


class FromConfigTests(unittest.TestCase):
    def test_reads_flags_and_counts(self):
        r = retention.from_config(REAL)
        self.assertEqual(r.enabled, {"daily": True, "weekly": True, "monthly": True,
                                     "hourly": False, "boot": False})
        self.assertEqual(r.keep, {"daily": 2, "weekly": 2, "monthly": 1, "hourly": 6, "boot": 5})

    def test_daily_count_is_read_even_when_daily_is_off(self):
        r = retention.from_config({**REAL, "schedule_daily": "false"})
        self.assertFalse(r.enabled["daily"])
        self.assertEqual(r.keep["daily"], 2)

    def test_missing_or_bad_values_never_raise(self):
        r = retention.from_config({"count_daily": "lots", "count_weekly": "-3", "schedule_daily": True})
        self.assertIsNone(r.keep["daily"])
        self.assertIsNone(r.keep["weekly"])
        self.assertIsNone(r.keep["monthly"])
        self.assertFalse(r.enabled["weekly"])
        self.assertEqual(retention.from_config({}).enabled, {k: False for k, _ in retention.LEVELS})


class DescribeTests(unittest.TestCase):
    def test_normal_setup_has_no_warning(self):
        t = text(REAL)
        self.assertIsNone(t.warning)
        self.assertEqual(t.daily_rule,
                         "Companion backups are tagged daily: kept for a rolling 2 days (at least 2 snapshots).")
        self.assertEqual(t.levels, "Daily/Weekly/Monthly on, keep 2/2/1")   # only the levels that are on

    def test_levels_line_shapes(self):
        off = {k: "false" if k.startswith("schedule_") else v for k, v in REAL.items()}
        self.assertEqual(text(off).levels, "none on")
        self.assertEqual(text({**off, "schedule_daily": "true", "schedule_boot": "true"}).levels,
                         "Daily/Boot on, keep 2/5")
        self.assertEqual(text({**REAL, "count_weekly": "x"}).levels, "Daily/Weekly/Monthly on, keep 2/?/1 (? = count not set)")

    def test_warning_when_weekly_and_monthly_are_both_off(self):
        for cfg in ({**REAL, "schedule_weekly": "false", "schedule_monthly": "false"},                        # daily only
                    {k: "false" if k.startswith("schedule_") else v for k, v in REAL.items()},                # everything off
                    {**REAL, "schedule_weekly": "false", "schedule_monthly": "false",
                     "schedule_hourly": "true", "schedule_boot": "true"}):                                      # daily/hourly/boot
            with self.subTest(cfg=cfg):
                w = text(cfg).warning
                self.assertEqual(
                    w,
                    "No weekly or monthly restore points are scheduled. Companion backups are kept for a "
                    "rolling 2 days (at least 2 snapshots), so history only goes back a few days. To keep "
                    "longer history, enable Weekly and/or Monthly in Timeshift → Settings → Schedule.")
                self.assertNotIn("scheduling is off", w)  # not true when Daily is on

    def test_either_weekly_or_monthly_is_enough_for_no_warning(self):
        for on in ("schedule_weekly", "schedule_monthly"):
            cfg = {**REAL, "schedule_weekly": "false", "schedule_monthly": "false", on: "true"}
            self.assertIsNone(text(cfg).warning, on)

    def test_n_comes_from_count_daily_when_daily_level_is_off(self):
        cfg = {k: "false" if k.startswith("schedule_") else v for k, v in REAL.items()}
        cfg["count_daily"] = "7"
        self.assertIn("rolling 7 days (at least 7 snapshots)", text(cfg).warning)

    def test_singular_count(self):
        self.assertIn("rolling 1 day (at least 1 snapshot).", text({**REAL, "count_daily": "1"}).daily_rule)

    def test_unknown_count_reads_as_a_sentence(self):
        # missing, non-numeric, negative and a list value, as rendered, in both places
        for bad in (None, "x", "-3", [1]):
            cfg = {k: v for k, v in REAL.items() if k != "count_daily"}
            if bad is not None:
                cfg["count_daily"] = bad
            cfg_off = {**cfg, "schedule_weekly": "false", "schedule_monthly": "false"}
            with self.subTest(bad=bad):
                self.assertEqual(
                    text(cfg).daily_rule,
                    "Companion backups are tagged daily: kept for the number of days set in Timeshift.")
                self.assertEqual(
                    text(cfg_off).warning,
                    "No weekly or monthly restore points are scheduled. Companion backups are kept for "
                    "the number of days set in Timeshift, so history is short. To keep longer history, "
                    "enable Weekly and/or Monthly in Timeshift → Settings → Schedule.")
                self.assertNotIn("a rolling a", text(cfg).daily_rule)

    def test_zero_count_does_not_read_like_a_clean_rolling_window(self):
        t = text({**REAL, "count_daily": "0"})
        self.assertEqual(
            t.daily_rule,
            "Companion backups are tagged daily: kept for 0 days (Timeshift's daily keep count is 0, "
            "so each Companion backup is deleted as soon as it's made).")
        self.assertNotIn("rolling 0", t.daily_rule)

    def test_zero_count_warning_is_one_readable_sentence(self):
        cfg = {**REAL, "count_daily": "0", "schedule_weekly": "false", "schedule_monthly": "false"}
        self.assertEqual(
            text(cfg).warning,
            "No weekly or monthly restore points are scheduled. Companion backups are kept for 0 days "
            "(Timeshift's daily keep count is 0, so each Companion backup is deleted as soon as it's made). "
            "To keep longer history, enable Weekly and/or Monthly in Timeshift → Settings → Schedule.")

    def test_tag_counts_and_the_overlap_note(self):
        t = text(REAL, {"daily": 5, "weekly": 2, "monthly": 1})
        # level order, and no visible overlap text: that is the tooltip (TAGS_NOTE)
        self.assertEqual(t.tags, "Snapshots by tag: daily 5, weekly 2, monthly 1")
        self.assertEqual(text(REAL, {"ondemand": 1, "daily": 2}).tags, "Snapshots by tag: daily 2, ondemand 1")
        self.assertIn("more than one tag", retention.TAGS_NOTE)

    def test_no_tags_shown_when_unknown_or_empty(self):
        self.assertIsNone(text(REAL, None).tags)
        self.assertIsNone(text(REAL, {}).tags)


if __name__ == "__main__":
    unittest.main()
