# Packaging skeleton — invocation contract

This is the interface `src/backup_runner.py` and `src/scheduling.py` must
be reworked against in the next phase (not done yet — see `PROJECT.md`
status checklist). Nothing in `src/` calls these helpers today.

## Backup action

```
pkexec /usr/lib/timeshift-on-demand/timeshift-on-demand-backup-helper
```

- No arguments, ever — see the helper's own header comment for why.
- Resolves to polkit action `org.timeshiftondemand.app.backup` via the
  `org.freedesktop.policykit.exec.path` annotation in
  `packaging/polkit/org.timeshiftondemand.app.policy` — this is automatic;
  callers never reference the action id directly, just the exec path.
- Exit code contract: `0` = snapshot created (including the exit-134
  tolerated case); any other code = real failure. stdout/stderr both
  carry human-readable progress/diagnostic lines — capture and stream
  both, same as the old `timeshift-monitor.sh` did for the flat script.
- Priority: if `ionice`/`nice` is still wanted, wrap the `pkexec` call
  itself from the caller side (e.g. `ionice -c2 -n7 nice -n10 pkexec ...`)
  — do not add priority logic inside the helper.
- Drive-mount wait (the old "wait up to 6×5s for the UUID to mount"
  logic) stays the *caller's* job, using whatever UUID the new Settings
  tab has stored — the helper assumes the backup device is already
  mounted and configured in Timeshift's own config by the time it runs.

## Cron-fix action

```
pkexec /usr/lib/timeshift-on-demand/timeshift-on-demand-cronfix-helper
```

- No arguments. Resolves to `org.timeshiftondemand.app.cronfix`.
- Exit code is whatever `mv`/`systemctl reload cron` returns; stdout
  carries one human-readable line either way (fixed vs. nothing-to-do).
- The **read side** (`check_cron_integrity()` in `scheduling.py`) stays
  unprivileged and untouched — it already only reads world-readable
  paths. Only the **fix side** goes through pkexec now, and only for the
  legacy-cron-file rename + cron reload — no timer re-enable step, ever
  (see `PROJECT.md`, "Maintenance tab — Fix Scheduling").

## Snapshot-list action

```
pkexec /usr/lib/timeshift-on-demand/timeshift-on-demand-list-helper
```

- No arguments. Resolves to `org.timeshiftondemand.app.list`.
- Confirmed (2026-08-29 testing): `timeshift --list` refuses
  unconditionally without root on every install, not just this machine —
  only `--version`/`--help` work unprivileged. This helper is the
  narrow, read-only alternative to Timeshift's own answer to that
  (re-exec the whole GUI as root), consistent with this project's choice
  not to follow that pattern.
- Uses `auth_admin_keep` in the polkit action (the other two use plain
  `auth_admin`) — this is a read-only glance `status.get_snapshots()`
  may be called from more than once in quick succession (Refresh click,
  once after a backup), and re-prompting every single time would be the
  nagging this project has avoided elsewhere. Never applied to the
  backup or cron-fix actions, which are consequential writes.
- **Strictly informational.** `src/app.py`'s Dashboard carries a visible
  advisory: this glance cannot browse, restore, or delete snapshots —
  "Open Timeshift" is the only way to do any of that. Do not extend this
  helper's scope beyond `--list` without revisiting that framing.
- **Never call this from a silent/automatic refresh.** `app.py`'s 30s
  Dashboard auto-tick deliberately calls only the unprivileged drive/disk
  status, not this — every call here is a real authorization request.

## Auto-prompt paths (login + resume-from-suspend)

Separate from the GUI entirely — `app.py`/`main.py` are not involved in
either of these, and there is no single-instance/GApplication concern
because of that. Both trigger points call the same script:

```
timeshift-on-demand-prompt
```

which reads `backup_drive_uuid` from `~/.config/timeshift-on-demand/config.json`
itself (a `python3 -c` one-liner, not a call into `src/config.py` — this
script has no Python module dependency of its own beyond what
`timeshift-on-demand-gui-monitor` needs), waits for that drive to mount,
shows a `zenity --question` confirm, and on Yes spawns
`timeshift-on-demand-gui-monitor` — a small standalone GTK progress
window, not a spawned terminal — before running
`ionice -c2 -n7 nice -n10 pkexec timeshift-on-demand-backup-helper`
directly — same pkexec target the GUI's `backup_runner.py` uses, so
there is exactly one place the actual backup logic lives. Replacing the
old terminal-based monitor with a real GTK window (2026-08-30) also
dropped the `gnome-terminal`/`xterm` dependency entirely — nothing here
needs a terminal emulator to exist anymore.

- **Login:** `packaging/timeshift-on-demand-autostart.desktop`
  (`/etc/xdg/autostart/`) runs `timeshift-on-demand-prompt` with a normal,
  already-correct session environment — no special handling needed.
- **Resume from suspend:** `packaging/systemd-sleep/timeshift-on-demand-resume-hook`
  (`/usr/lib/systemd/system-sleep/`) runs as root with no session
  environment of its own, so it has to locate an active graphical
  session's user and borrow that session's DISPLAY/WAYLAND_DISPLAY/
  DBUS_SESSION_BUS_ADDRESS from a real process in it before it can
  `runuser -u <user> -- timeshift-on-demand-prompt`. This is the most
  environment-fragile part of the whole project — verified only
  conceptually (syntax-checked, not run through an actual suspend/resume
  cycle) as of this pass. Treat it as needing real on-machine testing
  before being trusted, not as done.

## Everything else needed before this builds

Not part of this pass — tracked in `PROJECT.md`'s status checklist:
- `src/` rework (sudo → pkexec per above, hardcoded UUID/paths → Settings
  tab + XDG config, trimmed systemd unit list, dropped timer step, new
  Settings tab UI).
- Real Maintainer/Homepage/Source in `debian/control`,
  `debian/changelog`, `debian/copyright` (all currently `PLACEHOLDER`).
- Real reverse-DNS namespace in place of `org.timeshiftondemand.app.*`
  once a GitHub org/handle is claimed — touches the polkit action ids,
  the helper install path (`/usr/lib/timeshift-on-demand/` could stay as
  a plain product-name path even after the namespace firms up — the
  polkit *action id* is the part that must match the final namespace,
  not necessarily the filesystem path).
- A real `debian/timeshift-on-demand.1` or install-time smoke test
  hasn't been attempted yet — this skeleton has not been run through
  `dpkg-buildpackage`/`lintian`.
