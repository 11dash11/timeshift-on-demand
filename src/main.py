#!/usr/bin/env python3
"""
main.py — entry point for Timeshift Companion.

Launch model: desktop launcher icon AND tray applet, per JShin's brief.
The window hides-on-close rather than quitting; the tray's Quit item is
the actual exit path. If tray support isn't available on this system,
falls back to launcher-icon-only behaviour (window quits normally on
close) rather than failing to start.
"""

from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from app import TimeshiftCompanionApp  # noqa: E402
import tray  # noqa: E402


def main() -> int:
    app = TimeshiftCompanionApp()

    indicator_holder = {}  # keep a strong ref so GC doesn't drop the indicator

    def on_activate(_app):
        if not app.window:
            from app import TimeshiftCompanionWindow

            app.window = TimeshiftCompanionWindow(app)
        app.window.present()

        if "built" not in indicator_holder:
            indicator = tray.build_tray(
                app,
                on_backup_now=lambda: app.window._on_trigger_clicked(),
                on_show_window=lambda: app.window.present(),
                on_quit=lambda: app.quit(),
            )
            indicator_holder["built"] = True
            indicator_holder["indicator"] = indicator
            if indicator is None:
                print(
                    "Timeshift Companion: no AppIndicator3 binding found — "
                    "running without a tray icon. Window will quit normally "
                    "on close instead of hiding to tray.",
                    file=sys.stderr,
                )
                # Without a tray, hiding on close would strand the user with
                # no way to reopen the window — restore normal quit-on-close.
                app.window.disconnect_by_func(app.window._on_delete_event)
                app.window.connect("delete-event", lambda *_: False)

    app.connect("activate", on_activate)
    return app.run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
