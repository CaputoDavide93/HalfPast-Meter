"""Switching the Energy dashboard to HalfPast Meter, and putting it back.

Only grid and gas sources change; a fuel with no data yet stays where it is;
solar and the rest are untouched; the previous dashboard can be restored.
"""

from __future__ import annotations

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component

from custom_components.bright_meter import energy_dashboard
from custom_components.bright_meter.const import CONF_BACKFILL_DAYS, DOMAIN

from .const import PASSWORD, RES_GAS, RES_GAS_COST, USERNAME, FakeGlowmarktClient
from .test_statistics import _settle

FLOW_CLIENT = "custom_components.bright_meter.config_flow.GlowmarktClient"
SETUP_CLIENT = "custom_components.bright_meter.GlowmarktClient"

OCTOPUS_GRID = {
    "type": "grid",
    "stat_energy_from": "octopus_energy:electricity_import",
    "stat_energy_to": "octopus_energy:electricity_export",
    "stat_cost": None,
    "entity_energy_price": None,
    "number_energy_price": None,
    "stat_compensation": None,
    "entity_energy_price_export": None,
    "number_energy_price_export": None,
    "cost_adjustment_day": 0.0,
}
OCTOPUS_GAS = {
    "type": "gas",
    "stat_energy_from": "octopus_energy:gas_import",
    "stat_cost": None,
}
SOLAR = {"type": "solar", "stat_energy_from": "sensor.solar_energy"}


async def _dashboard(hass: HomeAssistant, sources: list[dict]):
    from homeassistant.components.energy.data import async_get_manager

    assert await async_setup_component(hass, "energy", {})
    manager = await async_get_manager(hass)
    await manager.async_update({"energy_sources": sources})
    return manager


def _by_type(manager) -> dict[str, dict]:
    return {s["type"]: s for s in manager.data["energy_sources"]}


async def _walk_setup(hass: HomeAssistant, client: FakeGlowmarktClient, switch: bool):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(FLOW_CLIENT, return_value=client), patch(
        SETUP_CLIENT, return_value=client
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD}
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_BACKFILL_DAYS: 2}
        )
        energy = result
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"switch_dashboard": switch}
        )
        await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    await _settle(hass, entry)
    return energy, result, entry


def test_statistics_are_listed_in_the_editor_order() -> None:
    """Consumption before its cost, grouped by dashboard section."""
    streams = [
        energy_dashboard.Stream(
            classifier=c, statistic_id=f"{DOMAIN}:{c.replace('.', '_')}", has_data=True
        )
        for c in (
            "electricity.consumption.cost",
            "gas.consumption.cost",
            "electricity.export",
            "gas.consumption",
            "electricity.consumption",
        )
    ]
    text = energy_dashboard.statistics_text(streams).splitlines()

    assert text[0] == "**Electricity grid**"
    assert "Grid consumption" in text[1]
    assert "total costs" in text[2]
    assert "Return to grid" in text[3]
    assert text[5] == "**Gas**"
    assert "Gas consumption" in text[6]


async def test_setup_without_the_switch_leaves_the_dashboard_alone(
    bright_env, hass: HomeAssistant
) -> None:
    """The default: the dashboard is not touched."""
    manager = await _dashboard(hass, [OCTOPUS_GRID, OCTOPUS_GAS, SOLAR])

    _, result, _ = await _walk_setup(hass, FakeGlowmarktClient(), switch=False)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert _by_type(manager)["grid"]["stat_energy_from"].startswith("octopus_energy:")


async def test_setup_switch_replaces_grid_and_keeps_gas_without_data(
    bright_env, hass: HomeAssistant
) -> None:
    """Gas refused by Bright: electricity switches, gas stays on Octopus."""
    manager = await _dashboard(hass, [OCTOPUS_GRID, OCTOPUS_GAS, SOLAR])
    client = FakeGlowmarktClient()
    client.refused = {RES_GAS, RES_GAS_COST}

    energy, result, _ = await _walk_setup(hass, client, switch=True)

    plan = energy["description_placeholders"]["plan"]
    assert "Gas:** left as it is" in plan
    assert result["type"] is FlowResultType.CREATE_ENTRY
    sources = _by_type(manager)
    assert sources["grid"]["stat_energy_from"] == f"{DOMAIN}:electricity_consumption"
    assert sources["grid"]["stat_cost"] == f"{DOMAIN}:electricity_consumption_cost"
    assert sources["gas"]["stat_energy_from"] == "octopus_energy:gas_import"
    assert sources["solar"] == SOLAR
    assert await energy_dashboard.async_has_backup(hass)


async def test_options_switch_needs_confirming_then_can_be_restored(
    bright_env, hass: HomeAssistant
) -> None:
    """Configure: nothing without the tick; switch; restore puts it back."""
    manager = await _dashboard(hass, [OCTOPUS_GRID, OCTOPUS_GAS, SOLAR])
    _, _, entry = await _walk_setup(hass, FakeGlowmarktClient(), switch=False)
    before = [dict(s) for s in manager.data["energy_sources"]]

    async def menu(step: str):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] is FlowResultType.MENU
        return result, await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": step}
        )

    _, page = await menu("energy_dashboard")
    assert "replaced by HalfPast Meter" in page["description_placeholders"]["plan"]
    done = await hass.config_entries.options.async_configure(
        page["flow_id"], {"confirm": False}
    )
    assert done["reason"] == "dashboard_unchanged"
    assert manager.data["energy_sources"] == before

    _, page = await menu("energy_dashboard")
    done = await hass.config_entries.options.async_configure(
        page["flow_id"], {"confirm": True}
    )
    assert done["reason"] == "dashboard_switched"
    sources = _by_type(manager)
    assert sources["grid"]["stat_energy_from"] == f"{DOMAIN}:electricity_consumption"
    assert sources["gas"]["stat_energy_from"] == f"{DOMAIN}:gas_consumption"
    assert sources["solar"] == SOLAR

    menu_page = await hass.config_entries.options.async_init(entry.entry_id)
    assert "restore_dashboard" in menu_page["menu_options"]
    page = await hass.config_entries.options.async_configure(
        menu_page["flow_id"], {"next_step_id": "restore_dashboard"}
    )
    done = await hass.config_entries.options.async_configure(page["flow_id"], {})
    assert done["reason"] == "dashboard_restored"
    restored = _by_type(manager)
    assert restored["grid"]["stat_energy_from"] == "octopus_energy:electricity_import"
    assert restored["gas"]["stat_energy_from"] == "octopus_energy:gas_import"
    assert not await energy_dashboard.async_has_backup(hass)
