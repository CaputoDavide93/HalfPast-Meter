"""The Glowmarkt (Bright) integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import GlowmarktClient
from .coordinator import GlowmarktCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]

type GlowmarktConfigEntry = ConfigEntry[GlowmarktCoordinator]


async def async_setup_entry(
    hass: HomeAssistant, entry: GlowmarktConfigEntry
) -> bool:
    """Set up Glowmarkt from a config entry."""
    client = GlowmarktClient(
        async_get_clientsession(hass),
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )

    coordinator = GlowmarktCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # No update listener: the options flow reloads the entry itself
    # (OptionsFlowWithReload), and so does reauth. HA deprecated having both in
    # 2026.6, and the pair stops working in 2026.12.
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: GlowmarktConfigEntry
) -> bool:
    """Unload a config entry."""
    # Wait for any running backfill first: its recorder queries run in an
    # executor thread, and tearing down around them races the recorder closing
    # its database connections.
    await entry.runtime_data.async_wait_for_backfill()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

