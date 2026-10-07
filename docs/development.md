# 🧪 Development

How to change the integration without breaking anyone's history.

## Set up

```bash
make venv     # .venv with Home Assistant 2026.9.4 and the test plugin, via uv
make check    # what CI runs: ruff, pytest, diagrams and generated tables current
make hassfest # Home Assistant's own validation, the other CI job (needs Docker)
```

`requirements_test.txt` pins `pytest-homeassistant-custom-component`, which pins
exactly one Home Assistant release. Move it with the Home Assistant you run, not
to a beta.

## Layout

| Path | What |
|---|---|
| `custom_components/bright_meter/api.py` | The HTTP client. Knows nothing about Home Assistant |
| `custom_components/bright_meter/coordinator.py` | The poll, the import, the sensors' snapshot |
| `custom_components/bright_meter/statistic_ids.py` | Assigns and stores statistic ids |
| `custom_components/bright_meter/config_flow.py` | The guided setup, reauthentication, options |
| `custom_components/bright_meter/sensor.py` | Sensors, from entity descriptions |
| `custom_components/bright_meter/diagnostics.py` | Diagnostics without identifiers |
| `custom_components/bright_meter/brand/` | Icons for Home Assistant, light and dark |
| `tests/const.py` | `FakeGlowmarktClient`: the API, including its misbehaviour |
| `tools/gen_diagram.py` | The diagrams in `docs/assets/` |
| `tools/gen_docs.py` | The statistics and sensor tables in the README and `entities.md` |
| `tests/test_hacs.py` | HACS's own `hacs.json` and manifest schemas, structure and brand icon |

## Rules

- **Never change a statistic id or the domain.** Either orphans every imported
  hour on every install. New ids come only from `statistic_ids.py`.
- **A test first.** Each behaviour change starts with a test that fails without
  it. The fake client can repeat or reverse hours, fail a chosen readings call,
  fail tariff or resource reads, and block readings on an event.
- **Nothing written before every read succeeds.** Keep the import's
  read-then-write-once shape; see [how-it-works.md](how-it-works.md#one-pass).
- **Documentation never lies.** Tables with a source of truth are generated
  (`python3 tools/gen_docs.py`); diagrams too (`python3 tools/gen_diagram.py`).
  `make check` fails when either is stale.
- Conventional commits, no AI attribution.

## Release

1. Move the `[Unreleased]` section of `CHANGELOG.md` under a new version.
2. Bump `version` in `custom_components/bright_meter/manifest.json`.
3. `make check`, merge, tag `vX.Y.Z`, then `gh release create`.
4. Install with `scripts/install-on-ha.sh` and check the import's log line.

## Going public

When it has run well on a live account for a few weeks:

- [ ] Make the repository public.
- [ ] Remove the `ignore:` line from `.github/workflows/validate.yaml`: HACS's
      action can then read `hacs.json` and the manifest itself (until then,
      `tests/test_hacs.py` checks them against HACS's own schemas).
- [ ] Add scheduled CI: weekly Hassfest and HACS validation, and a latest-HA
      canary.
- [ ] In the README and `docs/setup.md`, make HACS the first install route and
      drop "once this repository is public"; add the my.home-assistant HACS
      button.
- [ ] Check every doc for anything private: hostnames, account details.
