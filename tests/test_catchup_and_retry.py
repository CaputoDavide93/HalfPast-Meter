"""The two-hour catch-up rule survives restarts; diagnostics survive retries.

Seen on a live account: restarts and setup retries each reset an in-memory
timer, so Bright was asked to catch up from the DCC on every one of them, while
its API answered HTTP 500 for every electricity stream. And downloading
diagnostics during a setup retry raised AttributeError (no runtime_data).
"""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)

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
from .test_statistics import _settle

SETUP_CLIENT = "custom_components.bright_meter.GlowmarktClient"


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=USERNAME,
        data={"username": USERNAME, "password": PASSWORD},
        options={CONF_BACKFILL_DAYS: 2},
    )


async def test_a_reload_does_not_ask_for_another_catchup(
    bright_env, hass: HomeAssistant
) -> None:
    """Within two hours, a restarted entry does not ask Bright again."""
    client = FakeGlowmarktClient()
    entry = _entry()
    entry.add_to_hass(hass)
    with patch(SETUP_CLIENT, return_value=client):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await _settle(hass, entry)
        asked = len(client.catchup_calls)
        assert asked

        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        await _settle(hass, entry)

    assert len(client.catchup_calls) == asked


async def test_diagnostics_work_while_setup_is_retrying(
    bright_env, hass_client, hass: HomeAssistant
) -> None:
    """Every stream failing leaves the entry retrying; diagnostics still answer."""
    client = FakeGlowmarktClient()
    client.failing = {RES_ELEC, RES_ELEC_COST, RES_GAS, RES_GAS_COST}
    entry = _entry()
    entry.add_to_hass(hass)
    with patch(SETUP_CLIENT, return_value=client):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY

    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    assert diagnostics["coordinator"]["loaded"] is False
    assert diagnostics["entry"]["state"] == "setup_retry"
    assert "res-elec" not in str(diagnostics)
