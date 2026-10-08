"""A slow stretch of history must not block a stream's whole import.

Seen on a live account: the first import of a year of hourly electricity
readings stopped with "Could not reach the Glowmarkt API: " (an empty message:
a timeout). The cost stream's 30-day chunks came back in time, the consumption
stream's did not, and the whole stream was abandoned, then retried the same way.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.bright_meter.api import GlowmarktClient, GlowmarktTimeoutError
from custom_components.bright_meter.const import BASE_URL, CONF_BACKFILL_DAYS, DOMAIN

from .const import (
    AUTH_OK,
    HOURLY_ENERGY,
    PASSWORD,
    RES_ELEC,
    USERNAME,
    FakeGlowmarktClient,
)
from .test_statistics import _rows, _settle


async def test_a_timeout_says_it_timed_out(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A timeout is its own error, with a message that says so."""
    aioclient_mock.post(f"{BASE_URL}/auth", json=AUTH_OK)
    aioclient_mock.get(f"{BASE_URL}/resource/r1/readings", exc=TimeoutError())
    client = GlowmarktClient(async_get_clientsession(hass), USERNAME, PASSWORD)

    with pytest.raises(GlowmarktTimeoutError, match="timed out"):
        await client.async_get_readings(
            "r1", datetime(2026, 1, 1), datetime(2026, 1, 31), "PT1H"
        )


async def test_slow_chunks_are_split_until_they_answer(
    bright_env, hass: HomeAssistant
) -> None:
    """Chunks over five days time out: they are halved until they come back."""
    client = FakeGlowmarktClient()
    client.slow_over_days = {RES_ELEC: 5}
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=USERNAME,
        data={"username": USERNAME, "password": PASSWORD},
        options={CONF_BACKFILL_DAYS: 40},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.bright_meter.GlowmarktClient", return_value=client):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    await _settle(hass, entry)

    rows = await _rows(hass, f"{DOMAIN}:electricity_consumption")
    assert len(rows) >= 40 * 24 - 2
    assert rows[-1]["sum"] == pytest.approx(len(rows) * HOURLY_ENERGY)
    assert entry.runtime_data.data.data_until.get(RES_ELEC) is not None
