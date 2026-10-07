"""A stream Bright refuses to share is not a wrong password.

Seen on a live account: the login was accepted (checked in a terminal) but the
setup said "Bright rejected those details". The client treated a 401/403 from
ANY call as bad credentials; when Bright refuses one stream (a meter it is
still fixing, say), the client signed in again successfully, was refused
again, and blamed the password. These drive the real client through mocked
HTTP, so they cover what the fake client cannot.
"""

from __future__ import annotations

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.bright_meter.api import (
    GlowmarktAccessDeniedError,
    GlowmarktClient,
)
from custom_components.bright_meter.const import BASE_URL, DOMAIN

from .const import AUTH_OK, PASSWORD, USERNAME

VE = [{"name": "Home", "veId": "ve-1"}]
RESOURCES = {
    "veId": "ve-1",
    "resources": [
        {"resourceId": "r-elec", "classifier": "electricity.consumption", "active": True},
        {"resourceId": "r-gas", "classifier": "gas.consumption", "active": True},
    ],
}


def _account(aioclient_mock: AiohttpClientMocker, gas_status: int = 403) -> None:
    aioclient_mock.post(f"{BASE_URL}/auth", json=AUTH_OK)
    aioclient_mock.get(f"{BASE_URL}/virtualentity", json=VE)
    aioclient_mock.get(f"{BASE_URL}/virtualentity/ve-1/resources", json=RESOURCES)
    aioclient_mock.get(
        f"{BASE_URL}/resource/r-elec/last-time", json={"data": {"lastTs": 1791230400}}
    )
    aioclient_mock.get(
        f"{BASE_URL}/resource/r-gas/last-time",
        status=gas_status,
        json={"error": "Access denied"},
    )


async def test_a_refused_call_after_a_good_login_is_not_an_auth_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Signed in fine, refused one resource: access denied, not bad credentials."""
    _account(aioclient_mock)
    client = GlowmarktClient(async_get_clientsession(hass), USERNAME, PASSWORD)

    with pytest.raises(GlowmarktAccessDeniedError):
        await client.async_get_last_time("r-gas")


async def test_setup_goes_on_when_bright_refuses_one_stream(
    bright_env, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The meters page shows the refused stream; the login is not blamed."""
    _account(aioclient_mock)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD}
    )

    assert result.get("errors") in (None, {})
    assert result["step_id"] == "meters"
    listing = result["description_placeholders"]["meters"]
    gas = next(line for line in listing.splitlines() if "Gas:" in line)
    assert "refuses" in gas


async def test_a_refused_stream_leaves_the_others_importing(
    bright_env, hass: HomeAssistant
) -> None:
    """Gas refused: electricity still imports, gas stays unknown, never zero."""
    from unittest.mock import patch

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.bright_meter.const import CONF_BACKFILL_DAYS

    from .const import RES_GAS, RES_GAS_COST, FakeGlowmarktClient
    from .test_statistics import _rows, _settle

    client = FakeGlowmarktClient()
    client.refused = {RES_GAS, RES_GAS_COST}
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=USERNAME,
        data={CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
        options={CONF_BACKFILL_DAYS: 2},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.bright_meter.GlowmarktClient", return_value=client):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    await _settle(hass, entry)

    assert entry.runtime_data.last_update_success
    assert await _rows(hass, f"{DOMAIN}:electricity_consumption")
    assert not await _rows(hass, f"{DOMAIN}:gas_consumption")
    gas = hass.states.get("sensor.smart_home_gas_last_full_day")
    assert gas.state == "unknown"
    electricity = hass.states.get("sensor.smart_home_electricity_last_full_day")
    assert electricity.state not in ("unknown", "unavailable")
