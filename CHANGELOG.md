# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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
