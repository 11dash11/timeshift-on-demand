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

from tray_state import tray_state  # noqa: E402

# Shown instead of the normal icon (AppIndicator's "attention" status)
# while a backup runs. A stock symbolic theme icon, present in the Adwaita,
# Zorin and Breeze themes; the idle icon is never changed, so going back
# to normal can't go wrong.
ATTENTION_ICON = "emblem-synchronizing-symbolic"


class Tray:
    """The tray icon and its menu. Keep a reference for the app's lifetime."""

    def __init__(self, indicator, backup_item):
        self.indicator = indicator
        self._backup_item = backup_item
        self.set_running(False)

    def set_running(self, running: bool) -> None:
        """Label, clickability and icon, all from one place (tray_state.py)."""
        state = tray_state(running)
        self._backup_item.set_label(state.label)
        self._backup_item.set_sensitive(state.item_sensitive)
        self.indicator.set_status(
            AppIndicator3.IndicatorStatus.ATTENTION
            if state.attention_icon
            else AppIndicator3.IndicatorStatus.ACTIVE
        )


def build_tray(app, on_backup_now, on_show_window, on_quit):
    """
    Returns a Tray (keep a reference alive for the app's lifetime) or None
    if tray support isn't available.
    """
    if AppIndicator3 is None:
        return None

    indicator = AppIndicator3.Indicator.new(
        "timeshift-on-demand",
        "drive-harddisk",
        AppIndicator3.IndicatorCategory.APPLICATION_STATUS,
    )
    indicator.set_attention_icon_full(ATTENTION_ICON, "Backup running")

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

    return Tray(indicator, backup_item)
