"""
tray_state.py — what the tray should look like while a backup runs or not.

A pure function of one bool, so one call sets the menu label, whether the
item can be clicked, and whether the tray icon is in its "attention"
(backup running) state, and they can't drift apart. No GTK here, so it is
unit-tested without a display (tests/test_tray_state.py). tray.py applies it.
"""

from __future__ import annotations

from typing import NamedTuple

IDLE_LABEL = "Backup Now"
RUNNING_LABEL = "Backup running…"
# One state for the whole run, including the wait for the drive to mount.


class TrayState(NamedTuple):
    label: str
    item_sensitive: bool
    attention_icon: bool


def tray_state(running: bool) -> TrayState:
    if running:
        return TrayState(RUNNING_LABEL, False, True)
    return TrayState(IDLE_LABEL, True, False)
