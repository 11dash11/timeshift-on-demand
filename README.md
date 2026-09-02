# Timeshift On Demand

A small GTK3 companion app for [Timeshift](https://github.com/linuxmint/timeshift)
(called **Companion** throughout these docs) that adds a manual
on-demand backup trigger with live progress, an at-a-glance
snapshot/status dashboard, and a cron-integrity check for Timeshift's
own scheduling — launched from a tray icon or desktop launcher, and
(optionally) prompted at login or after resume from suspend.

It is built **on top of** Timeshift, not instead of it. Snapshot
browsing, restoring, and deleting stay entirely Timeshift's job; this
app never touches that.

> **Status:** early, personal-use development (`0.1.0~dev8`). Tested on
> Zorin OS 18.1 (Ubuntu 24.04-based) — see
> [Known limitations](#known-limitations) for what's still rough around
> the edges.

**Full documentation:** [Installation](docs/installation.md) ·
[Usage guide](docs/usage.md) · [Troubleshooting](docs/troubleshooting.md)

## Why this exists

The obvious question: Timeshift already has its own Schedule tab with
automatic Daily/Weekly/Monthly/etc. snapshot levels — so what's the
point of another app on top of it?

**A schedule is a clock, not an event.** Timeshift's scheduled levels
fire at a fixed time regardless of whether your backup drive is
actually connected at that moment. That's fine if your backup target
is an internal disk that's always there. It's not fine if it's an
external USB drive that only gets plugged in sometimes — which is
exactly the setup this project was built for. If the drive isn't
attached when the scheduled slot runs, Timeshift just silently skips
it, with no nudge telling you to plug in and catch up.

Companion's login and resume-from-suspend prompts exist to fill
specifically that gap: they check whether the drive is connected at
the moment you're actually at the machine — right after logging in, or
right after waking from suspend — and only then ask if you want to
back up. That's an *event*-driven trigger ("your drive is here, right
now"), not a clock-driven one, and it's genuinely something Timeshift's
own scheduler has no way to do, because it doesn't know or care what's
plugged in.

On-demand backups also just have their own everyday usefulness even
alongside scheduling: sometimes you want a snapshot **right now** —
before a risky system change, after finishing something significant —
and clicking one button beats waiting for the next scheduled slot or
opening Timeshift's full GUI to trigger it manually there.

Two smaller reasons on top of that:

- **A narrower privilege model.** Timeshift's own GUI re-execs its
  entire process as root via `pkexec` at launch. Companion's own
  process never runs as root — it asks for three separate, narrowly
  scoped, single-purpose privileged actions instead (see
  [Privilege model](#privilege-model) below), each authorized on its
  own.
- **Surfacing a real Timeshift quirk.** The Maintenance tab explains
  (rather than leaves you to puzzle over) a legacy cron file that can
  reappear depending on Timeshift's own scheduling state — see
  [usage.md's Maintenance tab section](docs/usage.md#maintenance-tab).

None of this replaces Timeshift's own scheduling — Companion is
designed to sit alongside it, not instead of it. If you don't need any
of the above (say, your backup drive is always connected), Timeshift's
own Schedule tab alone is a completely reasonable choice, and this app
has nothing to add for that case.

### The personal reason

My actual reason for vibe-coding this with Claude Code: after decades
as an IT administrator, I know how easy it is to assume something's
"working" in the background — until you're in real trouble and find
out it wasn't. Since switching to Linux three or four years ago, I've
leaned on Timeshift to get me out of tight spots more than once, and
somewhere along the way I started assuming Timeshift+Linux was just
bulletproof. That assumption caught me off guard a couple of times.

I didn't want to give up Timeshift — I wanted a habit-forming layer on
top of it. Something that asks me, plainly, was the last backup
successful? and gives me a deliberate Yes/No moment each time, instead
of trusting a silent background schedule to just handle it. Some days
the honest answer is "not today, the system's too busy" — and that's
fine, as long as it's a choice I actually made, not a gap I didn't
notice. Companion also keeps reminding me that checking in on old
snapshots and pruning them is still my job — not something it, or
Timeshift, quietly does for me.

That daily awareness is the actual point. It's a safety habit, not a
technical guarantee. I hope it's useful to you too — and if it isn't,
that's completely fine as well.

## What it does

- **Dashboard** — a read-only glance: snapshot count and latest tag,
  backup-drive usage, and an "Open Timeshift" button for anything beyond
  that glance (browsing, restoring, deleting snapshots).
- **Backup** — a "Backup Now" button with a live streamed log of the
  run, same exit-134-crash tolerance Timeshift's own CLI needs (see
  [Privilege model](#privilege-model)).
- **Settings** — pick which drive Companion should wait for before
  attempting a backup. This is independent of Timeshift's own configured
  backup device — both need to point at the same drive for a backup to
  actually succeed; Companion tells you plainly if they don't match.
- **Maintenance** — checks whether a legacy `/etc/cron.d/timeshift-hourly`
  file has reappeared, and explains *why*: if any of Timeshift's own
  Schedule levels (Daily/Weekly/Monthly/Hourly/Boot) are enabled, that
  file existing is expected Timeshift behavior, not a bug, and the fix
  action refuses to run rather than fight something that would just come
  back.
- **Tray icon** — Backup Now / Show Window / Quit, so the app can stay
  out of the way between uses.
- **Login and resume-from-suspend prompts** (optional, install-time) — a
  `zenity` Yes/No prompt that, on confirmation, opens the same live
  progress monitor as the Backup tab, independent of whether the main
  window is open.

## Requirements

- A Debian- or Ubuntu-based system with the official `timeshift` package
  installed and configured (Companion doesn't set up Timeshift itself —
  run Timeshift's own first-run setup, including picking a backup
  device, before using Companion's Backup tab).
- GTK3 + Python 3 GObject introspection (`python3-gi`, `gir1.2-gtk-3.0`).
- `pkexec`/`polkit`, `zenity`, `libnotify-bin`. A tray icon needs
  `gir1.2-ayatanaappindicator3-0.1` (or the legacy `gir1.2-appindicator3-0.1`);
  without either, the app still runs, just without a tray icon.

All of the above are declared as package dependencies — installing the
`.deb` pulls them in automatically.

## Installing

No published release yet. Build and install from source:

```bash
sudo apt build-dep .          # or install debhelper/dpkg-dev manually
dpkg-buildpackage -us -uc -b
sudo apt install ../timeshift-on-demand_<version>_all.deb
```

See [docs/installation.md](docs/installation.md) for the full walkthrough
(including the "set up Timeshift itself first" step that trips people up
most), and [docs/usage.md](docs/usage.md) for a tour of every tab with
screenshots.

## Privilege model

Companion itself never runs as root — unlike Timeshift's own GUI, which
re-execs its entire process as root via `pkexec` at launch. Instead,
exactly three narrow, argument-free actions are individually authorized
via `polkit`, each resolving to one bundled helper script that does
exactly one thing:

| Action | Does |
|---|---|
| `io.github.11dash11.timeshiftondemand.backup` | Runs `timeshift --create`, with the crash-tolerance workaround for a known upstream progress-parser bug |
| `io.github.11dash11.timeshiftondemand.cronfix` | Disables a reappeared legacy cron file (only when Timeshift's own scheduling is actually off) |
| `io.github.11dash11.timeshiftondemand.list` | Runs `timeshift --list` — Timeshift requires root for this unconditionally, even for a read-only listing |

## What this project deliberately doesn't do

- Doesn't vendor or fork Timeshift — depends on the official package.
- Doesn't manage snapshots (browse/restore/delete) — that's Timeshift's
  own GUI, one click away via "Open Timeshift".
- Doesn't touch Timeshift's own scheduled backups — if you want
  scheduled *and* on-demand backups, both work together; Companion's
  Maintenance tab recognizes and respects Timeshift's own Schedule
  settings rather than fighting them.

## Known limitations

- **On-demand snapshots aren't automatically pruned.** Timeshift's
  "Keep N" retention only applies to its own Daily/Weekly/Monthly/
  Hourly/Boot schedule levels — on-demand snapshots get a separate
  `ondemand` tag with no configured limit of its own, so they accumulate
  indefinitely unless you delete old ones yourself in Timeshift.
  (Confirmed: an on-demand snapshot *can* get absorbed into that day's
  "daily" bucket and pruned along with it, but only if it's created
  before that day's scheduled daily backup already ran — not something
  to rely on, just an occasional side effect.)
- The resume-from-suspend prompt reaches into an already-running desktop
  session from a root, session-less `systemd-logind` hook — inherently
  more fragile than the login-time prompt (which runs inside your normal
  session with no special handling needed). Tested and working on
  Zorin/GNOME + Wayland; may need adjustment on other desktop
  environments.

## License

[GPL-3.0](LICENSE) — the same license Timeshift itself uses.
