"""The guided setup: sign in, see the meters, choose a depth, wire the dashboard.

Each step is a page in Home Assistant's add-integration dialog. These tests
walk the dialog the way a person does and check what each page tells them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component

from custom_components.bright_meter.const import CONF_BACKFILL_DAYS, CONF_SITES, DOMAIN

from .const import (
    PASSWORD,
    RES_GAS,
    RESOURCES_ELEC_ONLY,
    USERNAME,
    VE_ID,
    VE_ID_SECOND,
    VIRTUAL_ENTITIES_TWO,
    FakeGlowmarktClient,
)
from .test_statistics import _settle

FLOW_CLIENT = "custom_components.bright_meter.config_flow.GlowmarktClient"
SETUP_CLIENT = "custom_components.bright_meter.GlowmarktClient"


async def _sign_in(hass: HomeAssistant, client: FakeGlowmarktClient):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["step_id"] == "user"
    with patch(FLOW_CLIENT, return_value=client):
        return await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD}
        )


async def _finish(hass: HomeAssistant, result, client, *, days: int = 2, sites=None):
    """Meters page, history page, dashboard page, then create the entry."""
    with patch(SETUP_CLIENT, return_value=client):
        meters_input = {} if sites is None else {CONF_SITES: sites}
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], meters_input
        )
        assert result["step_id"] == "history"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_BACKFILL_DAYS: days}
        )
        assert result["step_id"] == "energy"
        energy_page = result
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {}
        )
        await hass.async_block_till_done()
    return energy_page, result


async def test_one_site_walks_through_four_pages(
    bright_env, hass: HomeAssistant
) -> None:
    """Sign in, meters, history, dashboard: the entry carries what was chosen."""
    client = FakeGlowmarktClient()
    meters = await _sign_in(hass, client)

    assert meters["type"] is FlowResultType.FORM
    assert meters["step_id"] == "meters"
    listing = meters["description_placeholders"]["meters"]
    assert "Smart Home" in listing
    assert "Electricity" in listing and "Gas" in listing
    # One site: nothing to choose, so no site picker on the page.
    assert CONF_SITES not in meters["data_schema"].schema

    _, result = await _finish(hass, meters, client, days=30)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == USERNAME
    assert result["data"] == {
        CONF_USERNAME: USERNAME,
        CONF_PASSWORD: PASSWORD,
        CONF_SITES: [VE_ID],
    }
    assert result["options"] == {CONF_BACKFILL_DAYS: 30}
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    await _settle(hass, entry)


async def test_the_meters_page_says_how_far_each_stream_reaches(
    bright_env, hass: HomeAssistant
) -> None:
    """Each stream shows the time its data reaches, or that none has come yet."""
    client = FakeGlowmarktClient()
    newest = datetime(2026, 10, 6, 22, 30, tzinfo=UTC)

    async def last_time(resource_id: str):
        return None if resource_id == RES_GAS else newest

    client.async_get_last_time = last_time
    meters = await _sign_in(hass, client)

    listing = meters["description_placeholders"]["meters"]
    gas_line = next(line for line in listing.splitlines() if "Gas:" in line)
    electricity_line = next(
        line for line in listing.splitlines() if "Electricity:" in line
    )
    assert "no readings yet" in gas_line
    # The newest half-hour starts 22:30 UTC, so data reaches 23:00 UTC.
    assert "2026-10-06" in electricity_line


async def test_two_sites_let_you_choose(bright_env, hass: HomeAssistant) -> None:
    """With more than one site, the page offers them, all ticked by default."""
    client = FakeGlowmarktClient(
        virtual_entities=VIRTUAL_ENTITIES_TWO, resources=RESOURCES_ELEC_ONLY
    )
    meters = await _sign_in(hass, client)

    assert CONF_SITES in meters["data_schema"].schema
    _, result = await _finish(hass, meters, client, sites=[VE_ID_SECOND])

    assert result["data"][CONF_SITES] == [VE_ID_SECOND]
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    await _settle(hass, entry)
    assert {r.ve_id for r in entry.runtime_data.resources} == {VE_ID_SECOND}


async def test_choosing_no_site_is_refused(bright_env, hass: HomeAssistant) -> None:
    """An entry that imports nothing is not something anyone wants."""
    client = FakeGlowmarktClient(
        virtual_entities=VIRTUAL_ENTITIES_TWO, resources=RESOURCES_ELEC_ONLY
    )
    meters = await _sign_in(hass, client)

    result = await hass.config_entries.flow.async_configure(
        meters["flow_id"], {CONF_SITES: []}
    )

    assert result["step_id"] == "meters"
    assert result["errors"] == {"base": "no_site_selected"}


async def test_the_dashboard_page_names_the_statistics_to_pick(
    bright_env, hass: HomeAssistant
) -> None:
    """The last page lists the exact statistic ids, by dashboard field."""
    client = FakeGlowmarktClient()
    meters = await _sign_in(hass, client)
    energy, _ = await _finish(hass, meters, client)

    statistics = energy["description_placeholders"]["statistics"]
    assert "`bright_meter:electricity_consumption`" in statistics
    assert "`bright_meter:electricity_consumption_cost`" in statistics
    assert "`bright_meter:gas_consumption`" in statistics
    assert "`bright_meter:gas_consumption_cost`" in statistics
    assert energy["description_placeholders"]["existing"] == ""
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    await _settle(hass, entry)


async def test_the_dashboard_page_warns_about_sources_already_there(
    bright_env, hass: HomeAssistant
) -> None:
    """Sources already feeding the dashboard are named, to be removed first."""
    from homeassistant.components.energy.data import async_get_manager

    assert await async_setup_component(hass, "energy", {})
    manager = await async_get_manager(hass)
    await manager.async_update(
        {
            "energy_sources": [
                {
                    "type": "grid",
                    "stat_energy_from": "octopus_energy:electricity_import",
                    "stat_energy_to": None,
                    "stat_cost": None,
                    "entity_energy_price": None,
                    "number_energy_price": None,
                    "entity_energy_price_export": None,
                    "number_energy_price_export": None,
                    "cost_adjustment_day": 0.0,
                },
                {
                    "type": "gas",
                    "stat_energy_from": "octopus_energy:gas_import",
                    "stat_cost": None,
                    "entity_energy_price": None,
                    "number_energy_price": None,
                },
            ]
        }
    )

    client = FakeGlowmarktClient()
    meters = await _sign_in(hass, client)
    energy, _ = await _finish(hass, meters, client)

    existing = energy["description_placeholders"]["existing"]
    assert "octopus_energy:electricity_import" in existing
    assert "octopus_energy:gas_import" in existing
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    await _settle(hass, entry)


async def test_an_account_without_supported_meters_is_refused(
    bright_env, hass: HomeAssistant
) -> None:
    """Signed in, but nothing electricity or gas: say so on the sign-in page."""
    client = FakeGlowmarktClient(resources=[])
    result = await _sign_in(hass, client)

    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "no_meters"}


async def test_spaces_around_the_email_are_ignored(
    bright_env, hass: HomeAssistant
) -> None:
    """Autofill and pasting add spaces; an email never contains one."""
    seen: list[str] = []

    def make_client(session, username, password):
        seen.append(username)
        return FakeGlowmarktClient()

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(FLOW_CLIENT, side_effect=make_client):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: f"  {USERNAME} ", CONF_PASSWORD: PASSWORD},
        )

    assert result["step_id"] == "meters"
    assert seen == [USERNAME]
