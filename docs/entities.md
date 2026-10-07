# 📋 Statistics and sensors

Both tables are generated from the integration's own constants and
translations by `tools/gen_docs.py`, so they cannot drift from the code.

## Statistics

The history, for the Energy dashboard. Hourly, each hour where it happened.
An export statistic appears only if Bright has an export stream. A second site
or account gets the same names with a short suffix; see
[how-it-works.md](how-it-works.md#statistic-ids).

<!-- AUTOGEN:statistics -->
| Statistic | Unit | Stream |
|---|---|---|
| `bright_meter:electricity_consumption` | kWh | `electricity.consumption` |
| `bright_meter:electricity_consumption_cost` | GBP | `electricity.consumption.cost` |
| `bright_meter:electricity_export` | kWh | `electricity.export` |
| `bright_meter:gas_consumption` | kWh | `gas.consumption` |
| `bright_meter:gas_consumption_cost` | GBP | `gas.consumption.cost` |
<!-- /AUTOGEN:statistics -->

## Sensors

One device per Bright site. None has a `state_class`: the statistics above are
the history, and a second series built from the sensors would double-count.

<!-- AUTOGEN:entities -->
| Sensor | Unit | What it is |
|---|---|---|
| Electricity last full day | kWh | The last whole local day; `day` attribute says which |
| Electricity data until | timestamp | End of the newest whole hour Glowmarkt holds (diagnostic) |
| Electricity unit rate | GBP/kWh | Unit rate the meter holds |
| Electricity standing charge | GBP/d | Standing charge the meter holds |
| Electricity cost last full day | GBP | The last whole local day; `day` attribute says which |
| Export last full day | kWh | The last whole local day; `day` attribute says which |
| Export data until | timestamp | End of the newest whole hour Glowmarkt holds (diagnostic) |
| Gas last full day | kWh | The last whole local day; `day` attribute says which |
| Gas data until | timestamp | End of the newest whole hour Glowmarkt holds (diagnostic) |
| Gas unit rate | GBP/kWh | Unit rate the meter holds |
| Gas standing charge | GBP/d | Standing charge the meter holds |
| Gas cost last full day | GBP | The last whole local day; `day` attribute says which |
<!-- /AUTOGEN:entities -->

- **Last full day** is the newest local day whose every hour has arrived,
  built from local midnights (23, 24 or 25 hours). Its `day` attribute says
  which day; `statistic_id` names the matching statistic.
- **Data until** is the end of the newest whole hour Glowmarkt holds.
- **Unit rate** and **standing charge** are the rates held by the meter, read
  every 6 hours; unknown when the supplier has sent none.
