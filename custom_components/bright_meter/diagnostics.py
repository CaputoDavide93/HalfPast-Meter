"""Diagnostics for the Bright smart meter integration.

What helps debug a stalled or wrong feed: per stream, how far the data reaches,
the last whole day and the tariff the meter holds. Never the Bright email or
password, and never an identifier: the resource and site ids are long-lived
handles on the household's meters, and the site name is often an address.
Streams are told apart by classifier and statistic id instead.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.core import HomeAssistant

from . import GlowmarktConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: GlowmarktConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    data = coordinator.data

    streams: list[dict[str, Any]] = []
    for resource in coordinator.resources:
        rid = resource.resource_id
        data_until = data.data_until.get(rid) if data else None
        last_day = data.last_day.get(rid) if data else None
        tariff = data.tariffs.get(rid) if data else None
        streams.append(
            {
                "classifier": resource.classifier,
                "statistic_id": resource.statistic_id,
                "data_until": data_until.isoformat() if data_until else None,
                "last_full_day": (
                    {"day": last_day.day.isoformat(), "value": last_day.value}
                    if last_day
                    else None
                ),
                "tariff": asdict(tariff) if tariff else None,
            }
        )

    return {
        "entry": {"options": dict(entry.options)},
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
            "sites": len({r.ve_id for r in coordinator.resources}),
        },
        "streams": streams,
    }
