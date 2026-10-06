"""Tests for Glowmarkt setup, discovery and sensors."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bright_meter.api import GlowmarktAuthError
from custom_components.bright_meter.const import DOMAIN

from .const import (
    HOURLY_COST,
    HOURLY_ENERGY,
    RES_ELEC,
    RES_ELEC_COST,
    RES_GAS,
    RESOURCES_ELEC_ONLY,
    TARIFF_ELEC,
    VIRTUAL_ENTITIES_TWO,
    FakeGlowmarktClient,
)


async def _setup(
    hass: HomeAssistant, entry: MockConfigEntry, client: FakeGlowmarktClient
) -> None:
    entry.add_to_hass(hass)
    with patch(
        "custom_components.bright_meter.GlowmarktClient", return_value=client
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    await entry.runtime_data.async_wait_for_backfill()
    await hass.async_block_till_done()


async def test_setup_creates_sensors_for_both_fuels(
    bright_env, hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """A dual-fuel account gets ten sensors, and reactive power is ignored.

    Per fuel: the last full day, how far the data reaches, the unit rate and
    the standing charge, plus the last full day of its cost.
    """
    client = FakeGlowmarktClient()
    await _setup(hass, config_entry, client)

    assert config_entry.state is ConfigEntryState.LOADED

    entities = sorted(
        eid for eid in hass.states.async_entity_ids("sensor") if "smart_home" in eid
    )
    assert entities == [
        "sensor.smart_home_electricity_cost_last_full_day",
        "sensor.smart_home_electricity_data_until",
        "sensor.smart_home_electricity_last_full_day",
        "sensor.smart_home_electricity_standing_charge",
        "sensor.smart_home_electricity_unit_rate",
        "sensor.smart_home_gas_cost_last_full_day",
        "sensor.smart_home_gas_data_until",
        "sensor.smart_home_gas_last_full_day",
        "sensor.smart_home_gas_standing_charge",
        "sensor.smart_home_gas_unit_rate",
    ]


def _hours_in(hass: HomeAssistant, day: date) -> int:
    zone = dt_util.get_default_time_zone()
    # Subtracting two datetimes that share a tzinfo is wall-clock arithmetic
    # and always says 24; in UTC it says 23 or 25 on the clock-change days.
    start = dt_util.as_utc(datetime.combine(day, datetime.min.time(), zone))
    end = dt_util.as_utc(
        datetime.combine(day + timedelta(days=1), datetime.min.time(), zone)
    )
    return int((end - start).total_seconds() // 3600)


async def test_last_full_day_values(
    bright_env, hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Yesterday, whole, in kWh and pounds, with the day it covers."""
    await _setup(hass, config_entry, FakeGlowmarktClient())

    energy = hass.states.get("sensor.smart_home_electricity_last_full_day")
    day = date.fromisoformat(energy.attributes["day"])
    assert day == dt_util.now().date() - timedelta(days=1)
    hours = _hours_in(hass, day)
    assert float(energy.state) == pytest.approx(hours * HOURLY_ENERGY)
    assert energy.attributes["statistic_id"] == f"{DOMAIN}:electricity_consumption"

    cost = hass.states.get("sensor.smart_home_electricity_cost_last_full_day")
    assert float(cost.state) == pytest.approx(hours * HOURLY_COST / 100)
    assert cost.attributes["unit_of_measurement"] == "GBP"


async def test_data_until_is_the_end_of_the_newest_whole_hour(
    bright_env, hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """The edge of the data, so a stalled feed shows as a date, not as zero."""
    await _setup(hass, config_entry, FakeGlowmarktClient())

    state = hass.states.get("sensor.smart_home_electricity_data_until")
    now_hour = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    assert dt_util.parse_datetime(state.state) == now_hour


async def test_tariff_sensors_in_pounds(
    bright_env, hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """The rates the meter holds, converted from pence."""
    await _setup(hass, config_entry, FakeGlowmarktClient())

    def value(entity_id: str) -> float:
        return float(hass.states.get(entity_id).state)

    assert value("sensor.smart_home_electricity_unit_rate") == pytest.approx(0.2446)
    assert value("sensor.smart_home_electricity_standing_charge") == pytest.approx(
        0.4966
    )
    assert value("sensor.smart_home_gas_unit_rate") == pytest.approx(0.0862)
    assert value("sensor.smart_home_gas_standing_charge") == pytest.approx(0.1253)
    assert (
        hass.states.get("sensor.smart_home_gas_unit_rate").attributes[
            "unit_of_measurement"
        ]
        == "GBP/kWh"
    )


async def test_tariff_falls_back_to_the_cost_stream(
    bright_env, hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Which stream answers the tariff call is undocumented, so both are asked."""
    client = FakeGlowmarktClient()
    client.tariffs = {RES_ELEC: None, RES_ELEC_COST: TARIFF_ELEC, RES_GAS: None}
    await _setup(hass, config_entry, client)

    assert float(
        hass.states.get("sensor.smart_home_electricity_unit_rate").state
    ) == pytest.approx(0.2446)
    # Neither gas stream has one: unknown, not zero.
    assert hass.states.get("sensor.smart_home_gas_unit_rate").state == "unknown"


async def test_tariff_is_read_a_few_times_a_day_not_every_poll(
    bright_env, hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """A refresh inside six hours does not ask for the tariff again."""
    client = FakeGlowmarktClient()
    await _setup(hass, config_entry, client)
    calls = len(client.tariff_calls)
    assert calls

    await config_entry.runtime_data.async_refresh()
    await config_entry.runtime_data.async_wait_for_backfill()
    await hass.async_block_till_done()

    assert len(client.tariff_calls) == calls


async def test_sensors_have_no_state_class(
    bright_env, hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Statistics come from the API import, so entities must not generate them.

    A state_class here would make the recorder produce a second series for the
    same energy, and adding both to the Energy dashboard would count it twice.
    """
    await _setup(hass, config_entry, FakeGlowmarktClient())

    for entity_id in hass.states.async_entity_ids("sensor"):
        assert "state_class" not in hass.states.get(entity_id).attributes, entity_id


async def test_rejected_password_starts_reauth(
    bright_env, hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """A password changed in the Bright app asks to be re-entered."""
    client = FakeGlowmarktClient()
    await _setup(hass, config_entry, client)

    async def rejected(resource_id: str) -> None:
        raise GlowmarktAuthError("rejected")

    client.async_get_last_time = rejected
    await config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


async def test_statistic_ids_single_installation(
    bright_env, hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """One installation gets short, readable statistic IDs."""
    client = FakeGlowmarktClient()
    await _setup(hass, config_entry, client)

    ids = {r.statistic_id for r in config_entry.runtime_data.resources}
    assert ids == {
        f"{DOMAIN}:electricity_consumption",
        f"{DOMAIN}:electricity_consumption_cost",
        f"{DOMAIN}:gas_consumption",
        f"{DOMAIN}:gas_consumption_cost",
    }


async def test_statistic_ids_disambiguated_across_installations(
    bright_env, hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Two sites sharing a classifier: one readable id, one with a hash suffix.

    The suffix comes from the resource id, never the site name, which is often
    an address and would otherwise surface in diagnostics.
    """
    client = FakeGlowmarktClient(
        virtual_entities=VIRTUAL_ENTITIES_TWO, resources=RESOURCES_ELEC_ONLY
    )
    await _setup(hass, config_entry, client)

    ids = sorted(r.statistic_id for r in config_entry.runtime_data.resources)
    assert len(config_entry.runtime_data.resources) == 4
    assert len(set(ids)) == 4
    for base in (
        f"{DOMAIN}:electricity_consumption",
        f"{DOMAIN}:electricity_consumption_cost",
    ):
        plain = [i for i in ids if i == base]
        suffixed = [i for i in ids if i.startswith(f"{base}_") and i != base]
        suffixed = [i for i in suffixed if len(i) == len(base) + 9]
        assert plain == [base]
        assert len(suffixed) == 1


async def test_unload(
    bright_env, hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """The entry unloads cleanly."""
    await _setup(hass, config_entry, FakeGlowmarktClient())

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.NOT_LOADED
