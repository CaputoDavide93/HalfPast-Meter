# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- A guided setup in four pages: sign in; your meters, with how far each
  stream's data reaches and, for an account with several sites, which to
  import; how much history; and the exact statistic for each Energy dashboard
  field, naming any sources the dashboard already has.
- Documentation in `docs/`: setup, the Energy dashboard, statistics and
  sensors, how it works, troubleshooting, the Glowmarkt API, development; and
  two more diagrams (the setup pages, one import pass).
- The sign-in page and README say the integration works with Hildebrand Glow's
  Bright app, and is not affiliated with Hildebrand or Glow.
- Brand icons (light and dark) and a README banner.

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
