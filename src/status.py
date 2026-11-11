"""
status.py — read-only Timeshift + backup-drive status.

Replaces timeshift-status.sh. No privilege required for any of these —
including the snapshot list, which is read straight from Timeshift's
world-readable snapshot folders on the mounted backup drive (see
get_snapshots()), not via `timeshift --list`.

There's no systemd-unit status here: this package installs no systemd
units of its own (the drive-connect trigger is parked, unwired, in
packaging/future/drive-connect-trigger/), and Timeshift's own
timeshift-backup.service/.timer aren't something a normal install has.
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


@dataclass
class SnapshotInfo:
    # Snapshot names (their start timestamps), oldest first.
    names: list[str] = field(default_factory=list)
    # Timeshift's current tags for the newest snapshot, e.g. "ondemand" or
    # "daily" (Timeshift's scheduler can re-tag an on-demand snapshot later).
    latest_tags: str = ""
    # Snapshot folders with no info.json: a run still in progress, or one
    # that died before finishing.
    incomplete: int = 0
    error: Optional[str] = None

    @property
    def latest(self) -> Optional[str]:
        return self.names[-1] if self.names else None


@dataclass
class DiskUsage:
    mount_point: Optional[str]
    total_gb: Optional[float]
    used_gb: Optional[float]
    free_gb: Optional[float]
    percent_used: Optional[float]
    error: Optional[str] = None


def _run(cmd: list[str], timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, check=False
    )


def get_snapshots() -> SnapshotInfo:
    """
    List Timeshift's snapshots (oldest first) without any privilege, by
    reading its RSYNC-mode layout on the backup drive directly:
    <mount>/timeshift/snapshots/<YYYY-MM-DD_HH-MM-SS>/info.json. Timeshift
    creates those folders 0755 and info.json 0644 (confirmed on both the
    Dell and the Samsung, 2026-09-24), and writes info.json only once a
    snapshot has finished, so a folder without one is counted as
    incomplete rather than as a snapshot.

    This replaced a `pkexec timeshift --list` call. That needed an admin
    password every time the window opened, and wasn't read-only either:
    Timeshift rewrites its /etc/cron.d files and may mount the backup
    device on every run. The cost of reading the drive directly is that
    it must already be mounted (by the desktop, as for backups) — this
    no longer mounts it. Cheap enough to call from the auto-refresh timer.
    """
    config = _timeshift_config()
    if config is None:
        return SnapshotInfo(error="Timeshift isn't configured yet (no /etc/timeshift/timeshift.json)")
    if str(config.get("btrfs_mode", "false")).lower() == "true":
        return SnapshotInfo(error="Timeshift is in BTRFS mode — use “Open Timeshift” to see snapshots")
    uuid = config.get("backup_device_uuid")
    if not uuid:
        return SnapshotInfo(error="Timeshift has no backup device configured yet")
    mount = find_backup_mount(uuid)
    if not mount:
        return SnapshotInfo(error="Timeshift's backup drive isn't mounted")

    snap_dir = Path(mount) / "timeshift" / "snapshots"
    try:
        entries = sorted(p for p in snap_dir.iterdir() if p.is_dir() and SNAPSHOT_RE.fullmatch(p.name))
    except FileNotFoundError:
        return SnapshotInfo()  # drive mounted, Timeshift hasn't created a snapshot on it yet
    except OSError as exc:
        return SnapshotInfo(error=f"can't read {snap_dir}: {exc.strerror}")

    names = [p.name for p in entries if (p / "info.json").is_file()]
    info = SnapshotInfo(names=names, incomplete=len(entries) - len(names))
    if names:
        try:
            data = json.loads((snap_dir / names[-1] / "info.json").read_text(encoding="utf-8"))
            info.latest_tags = str(data.get("tags", ""))
        except (OSError, json.JSONDecodeError):
            pass  # count and name are still right; tags are just a nicety
    return info


def get_backup_drive_usage(mount_point: str) -> DiskUsage:
    """
    Disk usage for the backup drive. Pass in the mount point (e.g.
    resolved via find_backup_mount() at call time) — this module doesn't
    hardcode a UUID, it's the caller's job to know which drive Timeshift
    is configured to use (get_timeshift_backup_device_uuid()).
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
    # Timeshift mounts the drive a second time at /run/timeshift/<pid>/backup
    # while it runs, so findmnt can print two lines. Take the first
    # target that isn't one of those.
    for line in result.stdout.splitlines():
        target = line.strip()
        if target and not target.startswith("/run/timeshift/"):
            return target
    return None


TIMESHIFT_CONFIG = Path("/etc/timeshift/timeshift.json")


def get_timeshift_backup_device_uuid() -> Optional[str]:
    """
    The UUID of the backup device Timeshift itself is configured with, which
    is where every backup goes and so the only drive Companion needs to wait
    for. (Older versions had a separate drive setting of their own, and the
    two could disagree: confirmed on the Samsung RF511, 2026-08-30, the
    backup then failed with Timeshift's "Device not found: UUID=...".)

    /etc/timeshift/timeshift.json is world-readable (644), so no privilege
    is needed. Returns None if the config is missing, unreadable or not a
    JSON object, or the field is empty or not a string (Timeshift never
    configured, e.g. its first-run setup wasn't completed).
    """
    uuid = (_timeshift_config() or {}).get("backup_device_uuid")
    return uuid if isinstance(uuid, str) and uuid else None


def _timeshift_config() -> Optional[dict]:
    """/etc/timeshift/timeshift.json (world-readable), or None if missing/unreadable."""
    try:
        data = json.loads(TIMESHIFT_CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # includes missing, unreadable, bad JSON/UTF-8
        return None
    return data if isinstance(data, dict) else None
