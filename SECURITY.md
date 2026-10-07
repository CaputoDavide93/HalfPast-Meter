# 🔒 Security Policy

## Reporting a vulnerability

Please report vulnerabilities privately via
[GitHub Security Advisories](https://github.com/CaputoDavide93/HalfPast-Meter/security/advisories/new)
rather than opening a public issue. You should get a response within a week.

## Data handling

- The integration stores the Bright app email and password in the Home
  Assistant config entry, and sends them only to `api.glowmarkt.com` to obtain a
  session token. The token lives in memory and is renewed before it expires.
- It reads the household's half-hourly electricity and gas consumption, cost
  and tariff from that API, and writes them into Home Assistant's own recorder
  database. Nothing is sent anywhere else.
- Diagnostics leave out the email, the password, the Glowmarkt resource and site
  ids and the site name.
- `scripts/install-on-ha.sh` reads its SSH target from a gitignored `.env`, and
  uses key-based SSH only (`BatchMode=yes`).

## Supported versions

Only the latest commit on `main` is supported.
