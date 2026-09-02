"""
tray.py — AppIndicator3 tray icon: Backup Now / Show Window / Quit.

Depends on the AyatanaAppIndicator3 (or legacy AppIndicator3) GObject
introspection binding, and on GNOME Shell's tray/StatusNotifierItem
support being active. Zorin's default desktop normally has this; if the
import fails, main.py falls back to launcher-icon-only and logs why,
rather than crashing the whole app.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")

try:
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3 as AppIndicator3
except (ValueError, ImportError):
    try:
        gi.require_version("AppIndicator3", "0.1")
        from gi.repository import AppIndicator3
    except (ValueError, ImportError):
        AppIndicator3 = None  # signals "no tray support" to main.py

from gi.repository import Gtk  # noqa: E402


def build_tray(app, on_backup_now, on_show_window, on_quit):
    """
    Returns the indicator object (keep a reference alive for the app's
    lifetime) or None if tray support isn't available.
    """
    if AppIndicator3 is None:
        return None

    indicator = AppIndicator3.Indicator.new(
        "timeshift-companion",
        "drive-harddisk",
        AppIndicator3.IndicatorCategory.APPLICATION_STATUS,
    )
    indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)

    menu = Gtk.Menu()

    backup_item = Gtk.MenuItem(label="Backup Now")
    backup_item.connect("activate", lambda *_: on_backup_now())
    menu.append(backup_item)

    show_item = Gtk.MenuItem(label="Show Window")
    show_item.connect("activate", lambda *_: on_show_window())
    menu.append(show_item)

    menu.append(Gtk.SeparatorMenuItem())

    quit_item = Gtk.MenuItem(label="Quit")
    quit_item.connect("activate", lambda *_: on_quit())
    menu.append(quit_item)

    menu.show_all()
    indicator.set_menu(menu)

    return indicator
