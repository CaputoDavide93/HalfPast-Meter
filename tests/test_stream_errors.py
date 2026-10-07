"""One stream's error is that stream's problem, not the account's.

Seen on a live account: with the gas meter broken at Bright's end, setup said
"Could not reach Bright" although the login was good and electricity was fine:
one stream's error (not a refusal) failed the whole sign-in. Only a failure to
list the meters at all is a reason to stop.
"""

from __future__ import annotations

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bright_meter.api import GlowmarktConnectionError
from custom_components.bright_meter.const import CONF_BACKFILL_DAYS, DOMAIN

from .const import (
    PASSWORD,
    RES_ELEC,
    RES_ELEC_COST,
    RES_GAS,
    RES_GAS_COST,
    USERNAME,
    FakeGlowmarktClient,
)
from .test_statistics import _rows, _settle

FLOW_CLIENT = "custom_components.bright_meter.config_flow.GlowmarktClient"
SETUP_CLIENT = "custom_components.bright_meter.GlowmarktClient"


async def _sign_in(hass: HomeAssistant, client: FakeGlowmarktClient):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(FLOW_CLIENT, return_value=client):
        return await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD}
        )


async def _set_up(hass: HomeAssistant, client: FakeGlowmarktClient) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=USERNAME,
        data={CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
        options={CONF_BACKFILL_DAYS: 2},
    )
    entry.add_to_hass(hass)
    with patch(SETUP_CLIENT, return_value=client):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    if entry.state is ConfigEntryState.LOADED:
        await _settle(hass, entry)
    return entry


async def test_setup_goes_on_when_one_stream_errors(
    bright_env, hass: HomeAssistant, caplog
) -> None:
    """The meters page names the failing stream; the sign-in is not blamed."""
    client = FakeGlowmarktClient()
    client.failing = {RES_GAS}
    result = await _sign_in(hass, client)

    assert result["step_id"] == "meters"
    listing = result["description_placeholders"]["meters"]
    gas = next(line for line in listing.splitlines() if "Gas:" in line)
    assert "error" in gas
    electricity = next(line for line in listing.splitlines() if "Electricity:" in line)
    assert "data until" in electricity
    # The log says which stream and what Bright answered.
    assert "gas.consumption" in caplog.text
    assert "HTTP 500" in caplog.text


async def test_failing_to_list_the_meters_still_stops_setup(
    bright_env, hass: HomeAssistant, caplog
) -> None:
    """No meters listed at all is a real connection problem, and is logged."""
    client = FakeGlowmarktClient()
    client.resources_error = GlowmarktConnectionError("resources failed with HTTP 502")
    result = await _sign_in(hass, client)

    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "cannot_connect"}
    assert "HTTP 502" in caplog.text


async def test_one_failing_stream_leaves_the_others_importing(
    bright_env, hass: HomeAssistant
) -> None:
    """Gas failing: the entry loads, electricity imports, gas stays unknown."""
    client = FakeGlowmarktClient()
    client.failing = {RES_GAS, RES_GAS_COST}
    entry = await _set_up(hass, client)

    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.last_update_success
    assert await _rows(hass, f"{DOMAIN}:electricity_consumption")
    assert not await _rows(hass, f"{DOMAIN}:gas_consumption")
    assert hass.states.get("sensor.smart_home_gas_last_full_day").state == "unknown"


async def test_every_stream_failing_is_an_update_failure(
    bright_env, hass: HomeAssistant
) -> None:
    """When nothing answers, Bright is down: retry, do not pretend all is well."""
    client = FakeGlowmarktClient()
    client.failing = {RES_ELEC, RES_ELEC_COST, RES_GAS, RES_GAS_COST}
    entry = await _set_up(hass, client)

    assert entry.state is ConfigEntryState.SETUP_RETRY
