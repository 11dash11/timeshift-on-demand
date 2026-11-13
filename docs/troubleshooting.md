# Troubleshooting

Every scenario below was actually hit and diagnosed during this
project's own testing — not hypothetical.

## "Backup drive not mounted after wait" / nothing happens

Companion waits up to 3 minutes (36 attempts, 5 seconds apart) for the
backup drive (the one Timeshift is set to use) to mount before giving up quietly. If you triggered a
backup (manually or via an auto-prompt) and nothing visible happened:

1. Check the drive is actually connected and mounted. The UUID to look
   for is Timeshift's `backup_device_uuid` in
   `/etc/timeshift/timeshift.json`:
   `findmnt -S UUID=<that-uuid>`.
2. Check the log:
   ```bash
   tail -30 ~/.local/share/timeshift-on-demand/backup.log
   ```
   You'll see `Waiting for drive to mount (attempt N)...` lines followed
   by either a mount confirmation or a give-up message.
3. **A drive can fail to reconnect cleanly after suspend, specifically.**
   This happened for real during testing: a USB drive dropped during
   resume and the kernel's own re-enumeration attempts failed outright
   (`error -110`, `error -71` in `dmesg`/`journalctl -k`) — it needed a
   physical unplug/replug to recover, not just more waiting. If a drive
   that was connected before suspend doesn't show up after resume, check
   `dmesg`/`journalctl -k` for USB errors around that timestamp before
   assuming it's a Companion problem — it may genuinely be a hardware/USB
   power issue on resume, not something any amount of retrying fixes.
4. **A drive can also reconnect, just slowly — this is different from
   #3 above and doesn't need a replug.** Also confirmed for real: some
   USB enclosures don't survive suspend cleanly (`journalctl -k` shows
   `device offline error`, an aborted ext4 journal, and an unclean
   unmount right as the system suspends). On resume, the drive
   re-enumerates fine, but ext4 has to run journal recovery before it's
   mountable — and that recovery time varies a lot (5 seconds to over
   two minutes, observed across three real resumes on the same drive).
   Companion's 3-minute wait budget exists specifically to cover this;
   if `journalctl -k` around the resume timestamp shows
   `EXT4-fs (...): recovery complete` followed by `mounted filesystem`,
   the drive was on its way back on its own — just give it the full
   wait rather than replugging or assuming something's broken.

## Dashboard shows "Snapshots: unavailable — …"

The Dashboard reads snapshots straight from Timeshift's own backup drive
(the one set in Timeshift → Settings → Location). The message after the
dash says why it couldn't:

- **"Timeshift's backup drive isn't mounted"** — plug it in (or open it
  in your file manager so the desktop mounts it), then click Refresh.
  Companion doesn't mount drives itself.
- **"Timeshift has no backup device configured yet"** / **"isn't
  configured yet"** — run Timeshift's own first-run setup; see
  [installation.md](installation.md#before-you-install-set-up-timeshift-itself-first).
- **"Timeshift is in BTRFS mode"** — BTRFS snapshots don't live on a
  separate backup drive, so the Dashboard can't count them; use
  **Open Timeshift** instead.

## Timeshift has no backup device set

Companion has no drive setting of its own: it waits for, and backs up
to, the device Timeshift is configured with (Timeshift → Settings →
Location). If Timeshift has never been given one, Companion says so
straight away (on the Backup tab, in the Dashboard's "Backup drive"
line, or in `backup.log` for the login/resume prompt) instead of waiting
for a drive that doesn't exist, and doesn't ask for your password.

**Fix:** open Timeshift (**Open Timeshift** on the Dashboard), complete
its setup and pick the backup drive, then try again.

Older versions had a second drive setting in Companion that could
disagree with Timeshift's ("Timeshift is configured to back up to a
different device"). That setting is gone, so the mismatch can't happen.

## There's a /etc/cron.d/timeshift-hourly file

Expected, not a bug. Timeshift writes that file whenever any schedule
level (Daily, Weekly, Monthly, Hourly or Boot) is switched on, and
rewrites it every time it runs at all, including when Companion starts a
backup. It runs `timeshift --check --scripted` every hour, and that is
what creates the scheduled weekly and monthly restore points. If you
want no scheduled snapshots, untick every level in **Timeshift →
Settings → Schedule**; Timeshift then removes the file itself. Companion
doesn't touch it (older versions had a Maintenance tab and a "Fix
Scheduling" button for this, both gone).

Before you untick everything, see the next section: the Schedule levels
also decide how long your backups are kept.

## How long backups are kept

This is Timeshift 24.01.1's behaviour, read from its source and checked
against real snapshots on two machines; other versions haven't been
checked. The Dashboard's **Retention** lines show your settings.

- Companion backs up with `timeshift --create --tags D`, so every
  snapshot is tagged `daily`, whatever the schedule settings say. (The
  line "Tagged snapshot …: ondemand" in Timeshift's log is misleading;
  the snapshot itself is tagged `daily`. For the hourly, daily, weekly
  and monthly levels, Timeshift's "un-tagged" log line also always names
  the *oldest* snapshot, not the one it actually un-tagged.)
- After every backup Timeshift prunes using only its "Keep" counts, not
  the schedule on/off switches. With a daily count of N, it keeps every
  daily snapshot from the last N days, and never fewer than N snapshots
  (a snapshot with a comment is never removed, but still counts toward N).
  So the daily count applies even when the Daily level is off, and it is
  a rolling window, not calendar days.
- Weekly and Monthly levels work the same way with weeks and months, and
  are the only ways to get restore points older than the daily window.
  With both off, history goes back just the last few days. This is what
  the Dashboard's bold warning is about. **To keep longer history,
  enable Weekly and/or Monthly in Timeshift → Settings → Schedule.**
- One snapshot can carry several tags (for example daily and weekly), so
  the Dashboard's per-tag counts can add up to more than the total.
- If a scheduled backup is due, Timeshift may tag the oldest Companion
  backup made in the last hour or so (59 minutes) instead of creating
  another snapshot.
- Snapshots with a comment are never pruned, and neither are snapshots
  tagged `ondemand` (what a bare `timeshift --create`, or Timeshift's own
  GUI "Create" button, makes). Companion adds neither, and an automated
  test fails if its backup command ever changes.
- Run out of space instead? Prune by hand in Timeshift (**Open
  Timeshift**); Companion can't delete snapshots.

## The login/resume prompt didn't appear

Check the relevant log:

```bash
# For the login prompt or a manually-run check:
tail -30 ~/.local/share/timeshift-on-demand/backup.log

# For the resume-from-suspend prompt specifically:
cat /var/log/timeshift-on-demand-resume-hook.log
```

Common reasons, in order of likelihood:

1. **The configured drive wasn't mounted** at the time — same as the
   first section above. This is by far the most common cause; the
   prompt only appears once the drive-wait succeeds.
2. **No drive is configured yet** — check `backup.log` for "No backup
   drive configured."
3. **That prompt is turned off** in the Settings tab — check `backup.log`
   for "prompt_on_login is off" or "prompt_on_resume is off".
4. **(Resume prompt only) Session detection genuinely can't reach your
   desktop.** This is the most environment-fragile part of the whole
   project — reaching a real graphical session from a root, session-less
   hook context doesn't have one universally guaranteed API. If the
   resume-hook log shows it launched something but nothing appeared, that's
   worth reporting as a real issue with your specific desktop environment
   version, since this was only tested against GNOME/Wayland.

## Reinstalling after rebuilding from source

If you rebuild the package and reinstall, but changes don't seem to be
in effect:

- **The GTK app itself may still be an old running process.** Closing
  the window only hides it (the tray keeps it alive by design) — a
  package upgrade doesn't affect an already-running process's in-memory
  code. Fully quit via the tray icon's **Quit** option, then relaunch.
- **Standalone scripts** (the login/resume prompt, the privileged
  helpers) don't have this problem — they're read fresh from disk on
  every invocation, so a reinstall takes effect on their very next run
  with no need to quit anything.

## Where to find logs, if none of the above covers it

```bash
~/.local/share/timeshift-on-demand/backup.log       # all backup activity — GUI and auto-prompt alike
/var/log/timeshift-on-demand-resume-hook.log         # resume-from-suspend hook specifically (root-owned)
/var/log/timeshift/*.log                             # Timeshift's own logs (world-readable) — useful for
                                                      # confirming what Timeshift itself actually did
journalctl -k --since '-1 hour'                      # kernel log — useful for USB/drive issues
```
