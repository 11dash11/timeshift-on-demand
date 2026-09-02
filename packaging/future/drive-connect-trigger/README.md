# Drive-connect trigger — drafted, deliberately not active

**Status: designed and written 2026-08-30, not wired into the package.**
None of the three files in this directory are referenced by
`debian/install`, so building `timeshift-on-demand` today does not ship
them. This is intentional — JShin asked for the design to be recorded
and built so it's ready to activate later, without committing to it now.

## What this would add

A third automatic trigger for the `zenity` on-demand-backup prompt,
alongside the two already shipping (login autostart, resume-from-suspend
hook): prompting when the *configured backup drive itself* gets
connected, not just at login or resume.

## Why it wasn't just added outright

Two honest, unresolved costs, surfaced by JShin asking "how big a deal is
this, really" rather than accepting a first-pass "sounds fine":

1. **The rule fires more often than "plug in the backup drive."** It
   matches on `ENV{ID_FS_UUID}!=""` — any block device with a filesystem
   UUID — which includes every internal partition at boot (EFI, root,
   home, ...), not just external drive connects. On a typical machine
   that's several extra firings every single boot, forever, in addition
   to any actual drive connect/disconnect. Each firing is cheap (a
   lightweight check script, well under 100ms), but it's permanent
   low-grade `journalctl` noise on every machine this ships to — small,
   but real, not "zero-overhead."
2. **An unverified risk, not a reassured-against one.** During this same
   session's resume-hook testing, the actual backup drive (a WD My
   Passport) was observed failing USB re-enumeration repeatedly during a
   single reconnect attempt (`device number 5` → error → `device number
   6` → error → eventually `8`/`9` succeeds — see PROJECT.md, ninth
   round). Whether a *failed* enumeration attempt ever gets far enough
   for udev to populate `ID_FS_UUID` before erroring out — which would
   mean this rule could fire multiple times in a burst during one
   troubled reconnect, not once — was never actually tested. Building
   this without resolving that unknown would mean shipping an
   unverified multi-fire risk on exactly the class of flaky hardware
   already observed firsthand this session.

Neither is disqualifying — the design itself (see below) is sound and
adds no new privileged surface (it's a root-context system integration
point handing off to the user's own session, the same shape as the
already-shipping resume-hook, not a new polkit action). They're just
real tradeoffs worth deciding about deliberately rather than defaulting
into.

## The design

- `99-timeshift-on-demand-drive.rules` — a **generic** udev rule (same
  for every user, never regenerated per-install) matching any block
  device connecting with a filesystem UUID, asking systemd to start a
  templated instance service parameterized by that device's own UUID
  (`%E{ID_FS_UUID}`). This sidesteps the old flat-script system's actual
  blocker — its udev rule had one specific UUID hardcoded into the rule
  file itself, which can't be shipped in a package where every user has
  a different backup drive.
- `timeshift-on-demand-drive-trigger@.service` — the templated system
  unit the rule starts, root-context, same trust level as the
  already-shipping `timeshift-on-demand-resume-hook`.
- `timeshift-on-demand-drive-trigger-check` — does the actual "is this
  UUID one some active user configured" comparison, checking each active
  graphical session's own `~/.config/timeshift-on-demand/config.json`
  (world-readable-by-owner, same as everywhere else in this project).
  Only on a match does it launch `timeshift-on-demand-prompt`, via the
  same `systemd-run --user --machine=` mechanism validated for the
  resume-hook (see that script's own header comment for exactly which
  two real bugs that specific launch mechanism fixes — they'd apply
  equally here with a different mechanism).

All three passed a syntax-level check (`bash -n`, `systemd-analyze
verify`) — nothing has been tested against a real device-connect event,
since it isn't installed anywhere.

## To activate this later

1. Resolve the two open costs above — at minimum, actually test whether
   a failed USB re-enumeration can populate `ID_FS_UUID` before it
   fails; decide whether the permanent journal noise is acceptable.
2. Add all three files to `debian/install`:
   - `packaging/future/drive-connect-trigger/99-timeshift-on-demand-drive.rules` → `etc/udev/rules.d/`
   - `packaging/future/drive-connect-trigger/timeshift-on-demand-drive-trigger@.service` → `etc/systemd/system/`
   - `packaging/future/drive-connect-trigger/timeshift-on-demand-drive-trigger-check` → `usr/lib/timeshift-on-demand/`
3. Add the check script to `debian/rules`' `override_dh_fixperms` chmod
   list (same reasoning as the other bundled scripts — debhelper's
   default executable-directory detection doesn't reliably cover
   `/usr/lib/timeshift-on-demand/`).
4. Move this directory's files out of `packaging/future/` into
   `packaging/udev/` (rule) and `packaging/systemd-system/` or similar
   (unit + check script), matching this project's existing
   one-purpose-per-directory convention under `packaging/`.
5. Test the check script manually first (`sudo bash
   timeshift-on-demand-drive-trigger-check <a-real-uuid>`), exactly the
   same efficient debug pattern used to validate the resume-hook, before
   trusting the actual udev-triggered path.
6. Update `PROJECT.md`, `packaging/README.md`, and the top-level
   `README.md`'s feature list once it's real.
