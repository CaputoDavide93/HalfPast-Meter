# 📊 The Energy dashboard

The integration's history lives in external statistics, not in its sensors.
This page says which statistic goes where, and how to move over from another
integration without counting anything twice.

## Which statistic goes where

Open **Settings → Dashboards → Energy**. The guided setup's last page lists the
exact ids for your account; for a single site they are:

| Dashboard field | Statistic |
|---|---|
| Electricity grid → **Grid consumption** | `bright_meter:electricity_consumption` |
| Electricity grid → cost: **Use an entity tracking the total costs** | `bright_meter:electricity_consumption_cost` |
| Electricity grid → **Return to grid** | `bright_meter:electricity_export` (only if Bright has an export stream) |
| Gas → **Gas consumption** | `bright_meter:gas_consumption` |
| Gas → cost: **Use an entity tracking the total costs** | `bright_meter:gas_consumption_cost` |

A second site, or a second account, gets the same names with a short suffix;
see [how-it-works.md](how-it-works.md#statistic-ids).

## Moving over from another integration

If the dashboard already shows the same meters through another integration
(Octopus Energy, a Glow display on MQTT, a utility meter helper), the same kWh
would be counted twice. The setup's last page names every source the dashboard
already has.

1. Note what the dashboard shows today, so you can compare.
2. In **Settings → Dashboards → Energy**, delete the existing sources for the
   meters Bright covers.
3. Add the `bright_meter:` statistics from the table above.
4. Compare a past day with the Bright app: the totals should match.

The other integration's history is not deleted by removing it from the
dashboard; it simply stops being shown.

## What the dashboard will look like

- **Past days fill in; today stays empty or partial.** Readings arrive 30
  minutes to two days late.
- **Hours can change** for up to a week as Glowmarkt revises them. Each pass
  re-reads the week and the bars follow.
- **A gap stays a gap.** An hour Glowmarkt never collects has no bar, rather
  than a misleading zero.
- **Costs include the standing charge**, so a day with no use still costs
  something. They come from the meter's own tariff, so they can differ from a
  supplier's bill (VAT, discounts, rate changes the meter has not received).

## Live power

The dashboard's *Now* card and power graphs need a live sensor in watts. Bright
cannot give one: it has no live data for an account without Glow hardware. A
Glow display or CAD publishing on local MQTT provides that, alongside this
integration's history.
