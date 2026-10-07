# 🔌 The Glowmarkt API

What this integration uses of Hildebrand Glow's Glowmarkt API, and what is
known and unknown about it. Sources: Hildebrand's *Glowmarkt API documentation,
Individual User for Bright* (v1.8, April 2026), the Swagger specs at
`api.glowmarkt.com/api-docs`, and the code and issue trackers of earlier
integrations. Glowmarkt, Glow, Bright and Hildebrand are Hildebrand's names;
this project is independent of them.

## Calls

Base `https://api.glowmarkt.com/api/v0-1`, every call with `applicationId`
(the Bright app's public id) and, after sign-in, a `token` header.

| Call | Used for | Notes |
|---|---|---|
| `POST /auth` | Sign in | A bad password can come back as 401 or as HTTP 200 with `valid: false`; both are treated as rejected. The token lasts 7 days; the client renews it an hour early and retries once after a 401 or 403 |
| `GET /virtualentity` | Sites on the account | One per property |
| `GET /virtualentity/{veId}/resources` | Streams of a site | Keyed by `classifier` |
| `GET /resource/{id}/last-time` | How far the data reaches | `lastTs`, the start of the newest half-hour held |
| `GET /resource/{id}/readings` | Hourly readings | `period=PT1H`, `function=sum`, `offset=0`, `nulls=1`; at most 31 days per call, so 30-day chunks |
| `GET /resource/{id}/catchup` | Ask Glowmarkt to fetch from the DCC | Failures are ignored; stale data is the only cost |
| `GET /resource/{id}/tariff` | The rates the meter holds | Two documented shapes, both parsed; may be empty |

Requests time out after 45 seconds.

## Streams

| Classifier | Unit | Imported as |
|---|---|---|
| `electricity.consumption` | kWh | kWh |
| `electricity.consumption.cost` | pence | GBP (÷ 100) |
| `electricity.export` | kWh | kWh |
| `gas.consumption` | kWh | kWh |
| `gas.consumption.cost` | pence | GBP (÷ 100) |

Reactive-power classifiers (`electricity.import.reactive` and `.export.reactive`)
exist for large non-domestic sites and are ignored.

## Quirks the code defends against

- **Without `nulls=1`, an uncollected half-hour reads as 0**, which looks like
  a house using nothing. With it, the gap is `null`.
- **Readings arrive late and get revised**, so a week is re-read every pass.
- **A non-zero `offset`** changes what the returned timestamps mean in ways the
  documentation does not settle; everything is requested in UTC.
- **HTTP 200 can carry an error.** A response without a well-formed data list
  is an error, never "no readings".
- **The tariff can be missing**, or arrive in either of two shapes.

## Not yet verified against a live account

- How often a catch-up may be requested. Two hours is the figure other projects
  use; the integration keeps to it.
- Which half-hour of the day carries the standing charge in the cost stream.
- The tariff payload's real shape for a given supplier.
- Whether gas ever comes back in m³ rather than kWh.

## Prior art

| Project | Licence | What it taught |
|---|---|---|
| [existentia/hildebrand-glow-ha](https://github.com/existentia/hildebrand-glow-ha) | MIT | The starting point: token renewal, catch-up, external statistics |
| [baileyboy0304/homeassistant-glowbright](https://github.com/baileyboy0304/homeassistant-glowbright) | MIT | `nulls=1`, complete hours only, recomputing totals on revision |
| [HandyHat/ha-hildebrandglow-dcc](https://github.com/HandyHat/ha-hildebrandglow-dcc) | MIT | The failure modes: "today" sensors, doubled standing charges, a token never renewed |
| [Mossop/glowmarkt-rs](https://github.com/Mossop/glowmarkt-rs) | MIT | Typed request and response shapes, per-period range limits |
