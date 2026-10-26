# ❓ FAQ

Short answers. Setup is in [setup.md](setup.md), problems by symptom in
[troubleshooting.md](troubleshooting.md).

## What is HalfPast Meter?

HalfPast Meter is a Home Assistant integration that imports a UK smart meter's
electricity and gas readings, and their cost, as hourly statistics for the Energy
dashboard. It reads them from Hildebrand's Glowmarkt API, the service behind the free
Bright app, and places each hour where it was measured.

## Which energy suppliers does it work with?

It works with any supplier whose SMETS2 meter you can add to Bright. Bright reads the
meter through the DCC, the national smart-meter network, so the supplier does not matter.
Meters Bright cannot read are out of reach.

## Do I need a Bright account?

Yes. Setup asks for the Bright app's email and password, and the app must already show
your usage. A new account can take a day or two before the meter's data arrives.

## Is it live?

No. The feed is half-hourly and reaches Glowmarkt 30 minutes to two days late. Live power
needs a Glow display or CAD on local MQTT, which this integration does not read.

## How do I install it?

With HACS: add this repository as a custom repository of type **Integration**, download
**HalfPast Meter** and restart Home Assistant; [setup.md](setup.md#2-install-the-integration)
has a button that does the first step. Without HACS, copy
`custom_components/bright_meter/` into Home Assistant's `config/custom_components/` and
restart.

## Is everything proven?

Not yet. It runs on a live Bright account, but the catch-up rate limit, the
standing-charge half-hour, the tariff payload shape and gas in m³ are unverified (the
list is in the [README](../README.md#-tested-and-unverified)). A report from an account
unlike that one helps most.

## Where do the credentials go?

The Bright email and password stay in the Home Assistant config entry and are sent only to
`api.glowmarkt.com`. See [SECURITY.md](../SECURITY.md). Diagnostics leave them out.

## Why is the domain `bright_meter` but the name HalfPast Meter?

The domain was chosen first and cannot change: the imported statistics are named
`bright_meter:...`, and renaming either would orphan the stored history. Only the display
name changed.

## Why does the Energy dashboard count everything twice?

Another integration feeds the same meter. Remove it from the dashboard; see
[energy-dashboard.md](energy-dashboard.md#moving-over-from-another-integration).

## Why does the cost not match my bill?

The cost statistics are Glowmarkt's figures, with the meter's rates and whatever VAT
Glowmarkt applies. Treat them as an estimate.

## What happens to missing hours?

They stay missing. A gap gets no row, and a real zero is imported as zero. If the hour
arrives within a week, the next pass fills it in.

## How do I report a problem?

Open an issue with the Home Assistant version, the integration version, and the diagnostics
download from the integration's page (it leaves out the email, password, meter ids and site
name). Say whether the account has electricity, gas, export, or several sites.
