"""Diagnostics carry what helps debug the feed, and no identifier."""

from __future__ import annotations

import json
from unittest.mock import patch

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)

from homeassistant.core import HomeAssistant

from .const import PASSWORD, RES_ELEC, USERNAME, VE_ID, FakeGlowmarktClient


async def test_diagnostics_redact_credentials_and_identifiers(
    bright_env, hass_client, hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """No password, email, resource or site id, or site name in the dump."""
    config_entry.add_to_hass(hass)
    with patch(
        "custom_components.bright_meter.GlowmarktClient",
        return_value=FakeGlowmarktClient(),
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
        await config_entry.runtime_data.async_wait_for_backfill()
        await hass.async_block_till_done()

    diagnostics = await get_diagnostics_for_config_entry(
        hass, hass_client, config_entry
    )
    dumped = json.dumps(diagnostics)

    for secret in (PASSWORD, USERNAME, RES_ELEC, VE_ID, "Smart Home"):
        assert secret not in dumped, secret

    streams = {s["statistic_id"]: s for s in diagnostics["streams"]}
    electricity = streams["bright_meter:electricity_consumption"]
    assert electricity["classifier"] == "electricity.consumption"
    assert electricity["data_until"] is not None
    assert electricity["last_full_day"]["value"] > 0
    assert electricity["tariff"] == {"unit_rate_p": 24.46, "standing_charge_p": 49.66}
    assert diagnostics["entry"]["options"] == {"backfill_days": 2}
