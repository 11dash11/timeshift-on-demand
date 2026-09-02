"""
status.py — read-only Timeshift + backup-drive status.

Replaces timeshift-status.sh. No privilege required for any of these
except `timeshift --list`, which Timeshift itself permits as read-only
for the snapshot metadata (falls back gracefully if it errors).

Systemd-unit status reporting from the first-pass draft has been
dropped: this package installs no systemd units of its own (the
drive-connect auto-trigger chain — udev rule, trigger.service, user
unit — was retired per PROJECT.md, "Drive-trigger" decision), and
timeshift-backup.service/.timer were intentionally deleted on the
reference machine (see
projects/script-consolidation/docs/decisions-log.md, item 12) — neither
is something a fresh install elsewhere would have. There is nothing
meaningful left to report here.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


SNAPSHOT_RE = re.compile(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}")

LIST_HELPER = "/usr/lib/timeshift-on-demand/timeshift-on-demand-list-helper"

# Same pkexec return-value contract as backup_runner.py (`man pkexec`,
# RETURN VALUE) — named here too so get_snapshots()'s error message can
# distinguish "you said no" / "auth is broken" from a real Timeshift
# failure, instead of lumping all three together.
PKEXEC_EXIT_DISMISSED = 126
PKEXEC_EXIT_NOT_AUTHORIZED = 127

# `_run()`'s default 15s timeout (meant for fast unprivileged queries like
# findmnt/lsblk) is nowhere near enough for a pkexec call: confirmed via a
# real screenshot during Samsung RF511 testing — "Command ['pkexec', ...]
# timed out after 15 seconds" fired while the user was still typing their
# password into the graphical polkit dialog, killing the process out from
# under them. A human taking >15s to see a dialog and type a password is
# completely ordinary, not an edge case. 120s is generous for that without
# hanging indefinitely if the dialog is truly abandoned.
PKEXEC_TIMEOUT_S = 120


@dataclass
class SnapshotInfo:
    tags: list[str] = field(default_factory=list)
    raw_output: str = ""
    error: Optional[str] = None

    @property
    def latest(self) -> Optional[str]:
        return self.tags[-1] if self.tags else None


@dataclass
class DiskUsage:
    mount_point: Optional[str]
    total_gb: Optional[float]
    used_gb: Optional[float]
    free_gb: Optional[float]
    percent_used: Optional[float]
    error: Optional[str] = None


@dataclass
class DriveInfo:
    uuid: str
    label: Optional[str]
    mountpoint: Optional[str]
    size: Optional[str]
    removable: bool


def _run(cmd: list[str], timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, check=False
    )


def get_snapshots() -> SnapshotInfo:
    """
    List known snapshots (newest last). Confirmed (2026-08-29 testing,
    this machine): `timeshift --list` refuses unconditionally without
    root ("Application needs admin access", exit 1) — this is
    unconditional Timeshift behavior (only --version/--help work
    unprivileged), not a config quirk, so a plain unprivileged call is
    pointless here. Goes through the bundled list-helper via `pkexec`
    instead — see packaging/helpers/timeshift-on-demand-list-helper and
    packaging/README.md.

    Strictly read-only, same as the helper itself: this function must
    never be called from a silent/automatic refresh path (see app.py's
    Dashboard — the 30s auto-tick deliberately does not call this) since
    every call is a real pkexec authorization request, not a free query.
    Callers should only invoke this from an explicit user action (window
    open, a manual Refresh click, once after a backup completes).
    """
    try:
        result = _run(["pkexec", LIST_HELPER], timeout=PKEXEC_TIMEOUT_S)
    except FileNotFoundError as exc:
        return SnapshotInfo(error=str(exc))
    except subprocess.TimeoutExpired:
        return SnapshotInfo(
            error=f"timed out waiting {PKEXEC_TIMEOUT_S}s for authentication — try Refresh again"
        )

    tags = SNAPSHOT_RE.findall(result.stdout)
    if result.returncode != 0 and not tags:
        if result.returncode == PKEXEC_EXIT_DISMISSED:
            message = "authentication dialog was dismissed"
        elif result.returncode == PKEXEC_EXIT_NOT_AUTHORIZED:
            message = "authentication failed or was denied (pkexec exit 127)"
        else:
            message = (result.stdout.strip() + " " + result.stderr.strip()).strip()
        return SnapshotInfo(error=message or f"timeshift --list exited {result.returncode}")

    return SnapshotInfo(tags=tags, raw_output=result.stdout)


def get_backup_drive_usage(mount_point: str) -> DiskUsage:
    """
    Disk usage for the backup drive. Pass in the mount point (e.g.
    resolved via find_backup_mount() at call time) — this module doesn't
    hardcode a UUID, it's the caller's job to know which drive the user
    configured in the Settings tab (see config.py).
    """
    try:
        usage = shutil.disk_usage(mount_point)
    except (FileNotFoundError, OSError) as exc:
        return DiskUsage(
            mount_point=mount_point,
            total_gb=None,
            used_gb=None,
            free_gb=None,
            percent_used=None,
            error=str(exc),
        )

    gb = 1024 ** 3
    total_gb = usage.total / gb
    used_gb = usage.used / gb
    free_gb = usage.free / gb
    percent_used = (usage.used / usage.total * 100) if usage.total else None

    return DiskUsage(
        mount_point=mount_point,
        total_gb=round(total_gb, 1),
        used_gb=round(used_gb, 1),
        free_gb=round(free_gb, 1),
        percent_used=round(percent_used, 1) if percent_used is not None else None,
    )


def find_backup_mount(uuid: Optional[str]) -> Optional[str]:
    """Resolve the current mount point for a drive UUID, or None if unset/unmounted."""
    if not uuid:
        return None
    try:
        result = _run(["findmnt", "-nr", "-S", f"UUID={uuid}", "-o", "TARGET"])
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    target = result.stdout.strip()
    return target or None


TIMESHIFT_CONFIG = Path("/etc/timeshift/timeshift.json")


def get_timeshift_backup_device_uuid() -> Optional[str]:
    """
    Timeshift's OWN configured backup device UUID — a completely separate
    setting from Companion's own config.json (see config.py). Confirmed
    via real testing (Samsung RF511, 2026-08-30): Companion's Settings tab
    only controls which drive *Companion* waits for before attempting a
    backup — it has no effect on what Timeshift itself will actually try
    to write to. A fresh Timeshift install (or one repointed at a
    different device) can have this set to nothing, or to a UUID that
    doesn't match what's configured in Companion, and the on-demand
    backup will fail with Timeshift's own "Device not found: UUID=..."
    error — confusing if nothing upstream explains why.

    /etc/timeshift/timeshift.json is world-readable (644) — no privilege
    needed. Returns None if the config is missing/unreadable or the field
    is empty (Timeshift never configured, e.g. its first-run setup wasn't
    completed yet).
    """
    try:
        data = json.loads(TIMESHIFT_CONFIG.read_text(encoding="utf-8"))
    except (FileNotFoundError, PermissionError, json.JSONDecodeError):
        return None
    return data.get("backup_device_uuid") or None


def list_candidate_drives() -> list[DriveInfo]:
    """
    Block devices with a filesystem UUID, for the Settings tab's drive
    picker. Deliberately includes both removable and fixed drives — some
    users back up to a fixed secondary internal drive, not just an
    external one — the `removable` flag lets the UI label them, not
    filter them out.
    """
    try:
        result = _run(["lsblk", "-J", "-o", "NAME,UUID,LABEL,MOUNTPOINT,RM,SIZE,FSTYPE"])
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []

    drives: list[DriveInfo] = []

    def walk(devices: list[dict]) -> None:
        for dev in devices:
            uuid = dev.get("uuid")
            # Confirmed on this machine: an unfiltered list includes the
            # swap partition (it has a UUID like any other filesystem) —
            # never a valid backup target, so exclude it explicitly
            # rather than relying on the user to not pick it.
            if uuid and dev.get("fstype") != "swap":
                drives.append(
                    DriveInfo(
                        uuid=uuid,
                        label=dev.get("label"),
                        mountpoint=dev.get("mountpoint"),
                        size=dev.get("size"),
                        removable=bool(dev.get("rm")),
                    )
                )
            walk(dev.get("children") or [])

    walk(data.get("blockdevices", []))
    return drives
