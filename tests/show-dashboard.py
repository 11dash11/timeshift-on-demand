#!/usr/bin/env python3
"""Open the real Companion window with the retention lines taken from a
sample timeshift.json, so the "no weekly or monthly" warning can be looked at
(and screenshotted for the docs) without changing the live Schedule settings.
Everything else (snapshots, drive, last backup) is real and read-only.

Usage: python3 tests/show-dashboard.py [tests/fixtures/timeshift-weekly-monthly-off.json]
Needs a display. Close the window to exit. Runs under its own application id, so it
doesn't touch an already-running Companion."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

import app  # noqa: E402
import retention  # noqa: E402
import status  # noqa: E402

path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "fixtures", "timeshift-weekly-monthly-off.json")
with open(path, encoding="utf-8") as f:
    sample = json.load(f)
status.get_retention = lambda: retention.from_config(sample)

gtk_app = Gtk.Application(application_id="test.timeshiftondemand.showdashboard")
gtk_app.register(None)
win = app.TimeshiftCompanionWindow(gtk_app)
win.connect("destroy", Gtk.main_quit)
win.present()
Gtk.main()
