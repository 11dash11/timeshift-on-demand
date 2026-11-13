# Installation

## Requirements

- A Debian- or Ubuntu-based system (tested on Zorin OS 18.1 / Ubuntu 24.04).
- The official [`timeshift`](https://github.com/linuxmint/timeshift) package,
  GTK3 + Python 3 GObject introspection, `pkexec`/polkit, `zenity`,
  `libnotify-bin`. **All of these, including `timeshift` itself, are
  declared `.deb` dependencies** — installing the package pulls all of
  them in automatically, there's nothing to install separately first. A
  tray icon additionally needs `gir1.2-ayatanaappindicator3-0.1` (or the
  legacy `gir1.2-appindicator3-0.1`) — without either, the app still
  runs, just without a tray icon.

## Before you install: set up Timeshift itself first

This is the single most common source of confusion, so it gets said up
front: **Companion does not configure Timeshift.** It's a companion, not
a replacement — Timeshift needs to already know where to back up to
before Companion's "Backup Now" button can do anything.

If you haven't used Timeshift on this machine yet:

1. Open Timeshift itself (search your application menu, or run
   `timeshift-launcher`).
2. Go through its own first-run setup — snapshot type (RSYNC is the
   common choice), and crucially, **pick a backup device**.
3. Set its Schedule tab (Daily/Weekly/Monthly/etc.). This also decides
   how long your backups are kept: Companion's backups are tagged
   `daily` and pruned by Timeshift's daily "Keep" count, so with Weekly
   and Monthly both off you only have the last few days of history. The
   Dashboard shows the settings and warns about that case; see
   [usage.md](usage.md#dashboard-tab). Companion works alongside
   Timeshift's own scheduling, not against it.

Once Timeshift itself has a backup device configured, Companion is ready
to use.

## Building and installing the package

No published release yet — build from source:

```bash
git clone https://github.com/11dash11/timeshift-on-demand.git
cd timeshift-on-demand
sudo apt build-dep .          # or install debhelper/dpkg-dev manually
dpkg-buildpackage -us -uc -b
```

This produces `../timeshift-on-demand_<version>_all.deb` (one directory
above the source tree — normal `dpkg-buildpackage` convention).

Install it:

```bash
sudo apt install ../timeshift-on-demand_<version>_all.deb
```

**If you're upgrading an existing install**, use `apt install`/
`apt install --reinstall` from a terminal rather than a file manager's
"Install Application" right-click action — in testing, that GUI action
didn't reliably recognize a newer version was available and could
silently no-op instead of upgrading. Check afterward with:

```bash
dpkg -l timeshift-on-demand
```

The `Version` column should match what you just built.

## First launch

Launch Companion from your application menu ("Timeshift On Demand"), or
from a terminal:

```bash
timeshift-on-demand
```

It opens on the **Dashboard** tab:

![Dashboard tab](images/dashboard.png)

Companion backs up to whichever drive Timeshift itself is set up with, so
there's nothing to pick in Companion. The Dashboard shows that drive (and
says so if Timeshift has no backup device yet; see
[troubleshooting.md](troubleshooting.md#timeshift-has-no-backup-device-set)).

The **Settings** tab only holds the two login/resume prompt checkboxes:

![Settings tab](images/settings.png)

## Auto-prompts (optional, on by default)

Two things are installed automatically and don't need separate setup:

- A **login prompt** — a `zenity` Yes/No dialog asking whether to run an
  on-demand backup, shown once per login/session start.
- A **resume-from-suspend prompt** — the same dialog, shown after
  waking from suspend.

Both are entirely independent of whether the Companion window or tray
icon is open — see [usage.md](usage.md#auto-prompts-login-and-resume)
for how they work.

**Turning them off.** Companion's Settings tab has a checkbox for each
prompt. The change applies from the next login or resume.

## Verifying the install

```bash
dpkg -l timeshift-on-demand              # package status = ii
which timeshift-on-demand                # /usr/bin/timeshift-on-demand
pkaction --action-id io.github.11dash11.timeshiftondemand.backup   # confirms polkit picked up the policy
```

If the tray icon doesn't appear after launching, that's expected on
desktop environments without an AppIndicator extension installed — the
app still works fully from its main window.
