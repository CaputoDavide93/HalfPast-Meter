# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.4.0] - 2026-10-08

### Added

- **Switch the Energy dashboard for you**, from the setup's last page or from
  **Configure**: replaces only the grid and gas sources with HalfPast Meter's,
  leaves a fuel without data on its current source, leaves solar, batteries,
  water and devices alone, validates the result with Home Assistant's own
  schema, and keeps the dashboard as it was for **Restore the Energy
  dashboard**.
- **Configure** is a menu: history depth, the dashboard switch, and restore.

### Changed

- The setup's last page lists the statistics grouped by dashboard section in
  the editor's order (consumption before cost), marks streams with no data
  yet, and names the sources already there by integration and field instead
  of raw statistic ids.

## [0.3.4] - 2026-10-07

### Fixed

- **One stream's error no longer stops setup.** Seen on a live account: with
  the gas meter broken at Bright's end, setup said "Could not reach Bright"
  although the login and electricity were fine. Any error for one stream (not
  only a refusal) now marks that stream on the meters page and setup carries
  on; the poll and the import skip it, warn once until it answers again, and
  retry it every pass. Only failing to list the meters, or every stream
  failing at once, is treated as Bright being unreachable.
- Setup logs which call failed and what Bright answered, instead of only
  showing "Could not reach Bright"; the client logs every request's path and
  status at debug level.

### Added

- `scripts/check-login.py` can read `BRIGHT_EMAIL` and `BRIGHT_PASSWORD` from
  the git-ignored `.env`, and times each stream's call.

## [0.3.3] - 2026-10-07

### Fixed

- **A stream Bright refuses no longer reads as a wrong password.** Seen on a
  live account: the login was accepted, but one stream (a gas meter Bright was
  fixing) answered 403, and setup said "Bright rejected those details". A
  refusal that persists after a fresh, successful sign-in is now access denied
  for that call: setup lists the stream as refused and carries on, and the
  integration imports the other streams and picks the refused one up once Bright
  shares it. A wrong password still fails at sign-in, as before.
- Spaces around the email, from autofill or pasting, are ignored at sign-in.

### Added

- `scripts/check-login.py`: checks a Bright login the way the integration signs
  in, without echoing the password, and says what Bright answered.

## [0.3.2] - 2026-10-07

### Changed

- The repository is renamed to `HalfPast-Meter` to match the integration;
  the manifest's documentation and issue-tracker links, the README, the setup
  guide and the security policy point at the new address. GitHub redirects the
  old one.

## [0.3.1] - 2026-10-07

### Changed

- The integration is now called **HalfPast Meter**, in Home Assistant's
  integration list, in HACS and in the docs. Chosen after a survey of the
  field: "Halfhour" was taken by another Home Assistant integration in
  September 2026, and "X Energy", "Power X" and "X Meter" names crowd HACS.
  The domain `bright_meter` and every `bright_meter:` statistic id are
  unchanged, so nothing imported is lost.
- A simpler mark: three rising bars on a rounded tile, drawn as flat shapes,
  with light and dark versions; the wide logo and the banner use it.

## [0.3.0] - 2026-10-07

### Added

- HACS readiness: HACS's validation action in CI, and `tests/test_hacs.py`
  checking `hacs.json` and the manifest against HACS's own schemas, which its
  action cannot read while the repository is private.
- A guided setup in four pages: sign in; your meters, with how far each
  stream's data reaches and, for an account with several sites, which to
  import; how much history; and the exact statistic for each Energy dashboard
  field, naming any sources the dashboard already has.
- Documentation in `docs/`: setup, the Energy dashboard, statistics and
  sensors, how it works, troubleshooting, the Glowmarkt API, development; and
  two more diagrams (the setup pages, one import pass).
- The sign-in page and README say the integration works with Hildebrand Glow's
  Bright app, and is not affiliated with Hildebrand or Glow.
- `after_dependencies: [energy]` in the manifest: the setup reads the Energy
  dashboard's sources (Hassfest requires it). `make hassfest` runs the same
  validation locally.
- Brand icon and logo (a house holding half-hourly bars, with "Smart Meter" set
  in Inter, SIL OFL), each in light and dark versions, and a README banner.

Fixes from an independent code review; each has a regression test.

### Fixed

- A long gap before the re-import window no longer restarts the running total
  at zero beside older rows (a large negative step): the anchor is searched out
  to the deepest history the options allow.
- Nothing is written until every chunk, the anchor and the stored hours have
  been read; the rewrite then goes to the recorder as one batch, and a depth
  increase clears the series only after the reads succeed. A failure part-way
  leaves the stored series as it was.
- A repeated hour is counted once, and readings are summed in time order
  whatever order the API answers in. Two different readings for one hour fail
  the pass instead of being guessed at.
- An error answered with HTTP 200, a missing data list, a malformed row or a
  non-finite value is an error, not "no readings", so it can no longer flatten
  stored hours.
- Statistic ids are assigned once per stream and stored: a second site or a
  second account no longer renames or overwrites an existing history, and ids
  no longer carry the site name (which reached diagnostics).
- Waiting for the import on unload no longer cancels it.
- A failed import no longer marks the integration as updated successfully.
- The tariff fallback tries the cost stream when the consumption stream errors.
- A rejected password during meter discovery starts reauthentication.
- The last-full-day sensors work with a one-day history depth: the sensors'
  week is read whatever the depth.


## [0.2.0] - 2026-10-06

First version of `bright_meter`, started from
[existentia/hildebrand-glow-ha](https://github.com/existentia/hildebrand-glow-ha)
v0.1.2 (MIT) and renamed from its `glowmarkt` domain.

### Changed

- Readings are requested with `nulls=1`, and nothing at or after `last-time` is
  imported: a half-hour Glowmarkt has not collected is a gap, never zero use,
  and a real zero is imported at once instead of being held back 48 hours.
- The trailing window re-read on every pass grows from 72 hours to a week, and
  any stored row a pass has no reading for is rewritten flat, so the running
  total never steps backwards.
- kWh statistics declare the `energy` unit class.
- The options flow reloads the entry itself (`OptionsFlowWithReload`); the
  update listener is gone, since Home Assistant 2026.12 rejects having both.

### Added

- Sensors per stream: the last whole local day (23, 24 or 25 hours, skipped if
  any hour is a gap) and how far the data reaches.
- Sensors per fuel: the unit rate and standing charge the meter holds, read
  every six hours.
- Diagnostics without credentials or identifiers.
- A rejected password in the background import starts reauthentication.

### Removed

- The "today" sensors: the feed is 30 minutes to two days late, and their
  running total relied on an unverified timezone offset.
