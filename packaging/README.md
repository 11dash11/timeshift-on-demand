# Packaging and helper contracts

How the pieces in `packaging/` fit together, and the contracts between
the unprivileged app/scripts and the two root helpers. For building and
installing, see [docs/installation.md](../docs/installation.md).

## What gets installed where

| Source | Installed to | What it is |
|---|---|---|
| `src/*.py` | `/usr/share/timeshift-on-demand/` | The GTK app (unprivileged) |
| `bin/timeshift-on-demand` | `/usr/bin/` | Launcher for the GTK app |
| `bin/timeshift-on-demand-prompt` | `/usr/bin/` | Login/resume Yes/No prompt + backup run |
| `bin/timeshift-on-demand-gui-monitor` | `/usr/lib/timeshift-on-demand/` | Live progress window used by the prompt |
| `helpers/timeshift-on-demand-backup-helper` | `/usr/lib/timeshift-on-demand/` | Root helper: create a snapshot |
| `helpers/timeshift-on-demand-cronfix-helper` | `/usr/lib/timeshift-on-demand/` | Root helper: disable a reappeared cron file |
| `polkit/io.github.11dash11.timeshiftondemand.policy` | `/usr/share/polkit-1/actions/` | The two polkit actions |
| `io.github._11dash11.timeshiftondemand.desktop` | `/usr/share/applications/` | App launcher |
| `timeshift-on-demand-autostart.desktop` | `/etc/xdg/autostart/` | Runs the prompt at login |
| `systemd-sleep/timeshift-on-demand-resume-hook` | `/usr/lib/systemd/system-sleep/` | Runs the prompt after resume |
| `man/*.1` | `/usr/share/man/man1/` | Manpages |

Two naming schemes, on purpose: the GApplication ID and launcher use
`io.github._11dash11.timeshiftondemand` (a GApplication ID element can't
start with a digit, hence the `_`), while the polkit action IDs use
`io.github.11dash11.timeshiftondemand.*`, which polkit accepts.

## Privileged actions

Both helpers take **no arguments** and are invoked only as
`pkexec <absolute path>`. Each path is named in its action's
`org.freedesktop.policykit.exec.path` annotation, so polkit picks the
action automatically; callers never name the action ID. Both actions are
`auth_admin` (asked every time), with no `allow_gui`: none of the helpers
needs a display.

### Backup — `io.github.11dash11.timeshiftondemand.backup`

```
pkexec /usr/lib/timeshift-on-demand/timeshift-on-demand-backup-helper
```

- Runs `timeshift --create --tags D --scripted`, streaming its output
  (unbuffered, via `stdbuf` + `tee -p`, so a closed reader can't kill
  the backup with SIGPIPE).
- **Exit code:** `0` = snapshot created. Timeshift's known GObject crash
  (exit 134 or 139) also counts as `0`, but only when this run's own
  output contains "Snapshot saved successfully" and none of the known
  write-failure phrases. Anything else is passed through unchanged.
  pkexec's own `126` (dialog dismissed) and `127` (not authorized) reach
  the caller the same way.
- Tested by `tests/run-tests.sh` (a fake `timeshift`; no root needed).
- **Caller's job, not the helper's:** waiting for the drive to mount,
  checking that Companion's and Timeshift's configured drives match, and
  any `ionice`/`nice` (wrap the `pkexec` call itself).

### Cron fix — `io.github.11dash11.timeshiftondemand.cronfix`

```
pkexec /usr/lib/timeshift-on-demand/timeshift-on-demand-cronfix-helper
```

- If `/etc/cron.d/timeshift-hourly` exists, renames it to
  `timeshift-hourly.disabled` and restarts cron; otherwise does nothing.
  Never re-enables any systemd timer.
- `src/scheduling.py` refuses to call it while any of Timeshift's own
  schedule levels is on, since Timeshift would just recreate the file.
  The integrity check itself is unprivileged.

### Snapshot list — no privileged action

The Dashboard reads `<mount>/timeshift/snapshots/*/info.json` on
Timeshift's mounted backup drive directly (`status.get_snapshots()`);
Timeshift makes those world-readable. A third action that ran
`timeshift --list` was removed in `0.1.0~dev14`.

## Auto-prompts (login and resume)

Both run `timeshift-on-demand-prompt` as the logged-in user, independent
of whether the GTK app is running. The script reads the drive UUID from
`~/.config/timeshift-on-demand/config.json`, waits up to 180 s for that
drive to mount, asks with `zenity`, then opens the progress window and
runs `ionice -c2 -n7 nice -n10 pkexec <backup-helper>`, the same helper
the GUI uses.

- **Login:** the XDG autostart entry, in the session's normal
  environment.
- **Resume:** the root `system-sleep` hook finds each active
  wayland/x11 session, borrows its `DISPLAY`/`WAYLAND_DISPLAY`, and
  starts the prompt through that user's own `systemd --user` manager
  (`systemd-run --user --machine=<user>@.host`, uniquely named unit), so
  pkexec can find the session's polkit agent. See the hook's header
  comment for why each simpler approach failed. It logs to
  `/var/log/timeshift-on-demand-resume-hook.log` (rotated weekly via
  `/etc/logrotate.d/timeshift-on-demand`).

## Logs

- `~/.local/share/timeshift-on-demand/backup.log`: every backup attempt,
  GUI and prompt alike. Trimmed to its last 256 KiB once it passes 1 MiB.
- `/var/log/timeshift-on-demand-resume-hook.log`: the resume hook only.

## Parked: drive-connect trigger

`future/drive-connect-trigger/` is a designed but deliberately unwired
third trigger (fires when the backup drive is plugged in). Its README
explains the two open tradeoffs. Nothing there is installed.
