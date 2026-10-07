# 🛠️ Troubleshooting

Symptom first. Most "faults" are the feed being late, which is normal.

## Setup

| Symptom | Cause | Fix |
|---|---|---|
| *Bright rejected those details* | Wrong email or password | Sign in to the Bright app with them first; use exactly the same |
| *Could not reach Bright* | No internet, or `api.glowmarkt.com` down | Try again later |
| *This account has no electricity or gas meter yet* | The meter is not set up or not sharing data in Bright | Finish setup in the app, wait until it shows usage |
| *Your meters* says **no readings yet** | Bright has not collected anything yet | Normal for a day or two; carry on, it fills in on its own |
| *This Bright account is already set up* | The same email is already added | One entry per account |

## After setup

<details>
<summary><b>The numbers stop at yesterday, or earlier</b></summary>

That is the feed. Check the **data until** sensors: they show the end of the
newest whole hour Glowmarkt holds. If it is more than two days old:

- open the Bright app, which asks Glowmarkt to fetch from the DCC;
- check the app itself shows recent data; if not, the problem is upstream of
  Home Assistant.

</details>

<details>
<summary><b>An hour or a day is missing in the Energy dashboard</b></summary>

Glowmarkt has no reading for it yet, or never collected it. Missing hours are
left out rather than drawn as zero. If they arrive within a week they fill in
on their own; older gaps stay gaps.

</details>

<details>
<summary><b>The energy looks doubled</b></summary>

Two sources measure the same meter. Remove the other one from the Energy
dashboard: see [energy-dashboard.md](energy-dashboard.md#moving-over-from-another-integration).

</details>

<details>
<summary><b>The cost doesn't match the bill</b></summary>

The cost statistics are Glowmarkt's figures: the meter's rates, standing charge
included, with whatever VAT Glowmarkt applies. A supplier's discounts, VAT
changes and rates the meter has not received yet all make them differ.

</details>

<details>
<summary><b>The unit rate or standing charge is unknown, or wrong</b></summary>

They are the rates held by the meter, sent by the supplier over the DCC. After
a switch it can take days or weeks for the new rates to arrive, and some
suppliers never send them. Unknown means neither stream had a tariff; the
sensors never show zero for that.

</details>

<details>
<summary><b>Home Assistant asks for the password again</b></summary>

Bright rejected the stored password, usually because it was changed in the app.
Enter the new one in the notification; nothing is lost.

</details>

## Collecting evidence

- **Diagnostics:** the integration's menu → **Download diagnostics**. For each
  stream it shows how far the data reaches, the last full day and the tariff,
  with no email, password, meter ids or site name.
- **Debug logging:** add to `configuration.yaml`, restart, reproduce, then look
  in **Settings → System → Logs**:

  ```yaml
  logger:
    logs:
      custom_components.bright_meter: debug
  ```

  The import logs how many hours it wrote per stream, and why it stopped if it
  did.
