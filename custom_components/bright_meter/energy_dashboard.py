"""Read, explain and switch the Energy dashboard's grid and gas sources.

The guided setup's last page, and the options' "Energy dashboard" step, use
this to say which statistic goes in which field, to describe the sources the
dashboard already has in words rather than raw ids, and, when asked, to put
HalfPast Meter's statistics in place of the existing grid and gas sources.

The switch is deliberately narrow:

- only `grid` and `gas` sources are replaced; solar, batteries, water and
  devices are left exactly as they are;
- a fuel whose consumption stream has no data yet (a meter Bright is still
  fixing, say) is left on its current source, so it never goes blank;
- the dashboard as it was is stored first, and can be put back;
- the new list goes through Home Assistant's own source schema before it is
  saved, so it is exactly what the dashboard editor itself would accept.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.loader import async_get_integration

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

BACKUP_KEY = f"{DOMAIN}.energy_backup"
BACKUP_VERSION = 1

# Fields of the dashboard, in the order its editor asks for them.
FIELDS: list[tuple[str, str, str]] = [
    ("Electricity grid", "Grid consumption", "electricity.consumption"),
    (
        "Electricity grid",
        "Use an entity tracking the total costs",
        "electricity.consumption.cost",
    ),
    ("Electricity grid", "Return to grid", "electricity.export"),
    ("Gas", "Gas consumption", "gas.consumption"),
    ("Gas", "Use an entity tracking the total costs", "gas.consumption.cost"),
]

# Which stream decides whether a fuel has data to switch to.
_FUEL_STREAM = {"grid": "electricity.consumption", "gas": "gas.consumption"}
_FUEL_LABEL = {"grid": "electricity", "gas": "gas"}


@dataclass(frozen=True, kw_only=True)
class Stream:
    """One of this integration's streams, as the dashboard needs to know it."""

    classifier: str
    statistic_id: str
    has_data: bool


def statistics_text(streams: list[Stream]) -> str:
    """Which statistic goes in which field, grouped and in the editor's order."""
    by_classifier = {s.classifier: s for s in streams}
    lines: list[str] = []
    group = None
    for heading, field, classifier in FIELDS:
        stream = by_classifier.get(classifier)
        if stream is None:
            continue
        if heading != group:
            if lines:
                lines.append("")
            lines.append(f"**{heading}**")
            group = heading
        note = "" if stream.has_data else " (no data yet)"
        lines.append(f"- {field}: `{stream.statistic_id}`{note}")
    return "\n".join(lines)


async def _async_manager(hass: HomeAssistant) -> Any:
    from homeassistant.components.energy.data import async_get_manager

    return await async_get_manager(hass)


async def async_current_sources(hass: HomeAssistant) -> list[dict[str, Any]]:
    """The dashboard's sources, or [] if they cannot be read."""
    try:
        manager = await _async_manager(hass)
    except Exception:
        _LOGGER.debug("Could not read the Energy dashboard", exc_info=True)
        return []
    return [dict(s) for s in (manager.data or {}).get("energy_sources", [])]


async def _async_origin(hass: HomeAssistant, statistic_id: str) -> str:
    """The integration a statistic comes from, by its display name."""
    if ":" in statistic_id:
        domain = statistic_id.split(":", 1)[0]
    else:
        entry = er.async_get(hass).async_get(statistic_id)
        domain = entry.platform if entry else statistic_id.split(".", 1)[0]
    try:
        return (await async_get_integration(hass, domain)).name
    except Exception:  # noqa: BLE001 - any failure: show the domain instead
        return domain


async def async_existing_text(
    hass: HomeAssistant, sources: list[dict[str, Any]]
) -> str:
    """The grid and gas sources already there, in words, or "" if none."""
    parts: dict[str, list[str]] = {}
    for source in sources:
        if source.get("type") == "grid":
            fields = [
                ("stat_energy_from", "grid consumption"),
                ("stat_energy_to", "return to grid"),
            ]
        elif source.get("type") == "gas":
            fields = [("stat_energy_from", "gas")]
        else:
            continue
        for key, label in fields:
            statistic_id = source.get(key)
            if not statistic_id or statistic_id.startswith(f"{DOMAIN}:"):
                continue
            origin = await _async_origin(hass, statistic_id)
            parts.setdefault(origin, [])
            if label not in parts[origin]:
                parts[origin].append(label)
    if not parts:
        return ""
    named = "; ".join(
        f"**{origin}** ({', '.join(labels)})" for origin, labels in parts.items()
    )
    return (
        f"Your Energy dashboard already has {named}. Remove those for the same "
        "meters before adding the statistics above, or the energy is counted "
        "twice, or let the switch below do it."
    )


def _new_sources(streams: list[Stream]) -> dict[str, dict[str, Any]]:
    """Our grid and gas sources, for each fuel that has data to show."""
    ids = {s.classifier: s.statistic_id for s in streams}
    ready = {s.classifier for s in streams if s.has_data}
    out: dict[str, dict[str, Any]] = {}
    if _FUEL_STREAM["grid"] in ready:
        out["grid"] = {
            "type": "grid",
            "name": "HalfPast Meter",
            "stat_energy_from": ids["electricity.consumption"],
            "stat_energy_to": ids.get("electricity.export"),
            "stat_cost": ids.get("electricity.consumption.cost"),
            "cost_adjustment_day": 0.0,
        }
    if _FUEL_STREAM["gas"] in ready:
        out["gas"] = {
            "type": "gas",
            "name": "HalfPast Meter",
            "stat_energy_from": ids["gas.consumption"],
            "stat_cost": ids.get("gas.consumption.cost"),
        }
    return out


def plan_text(streams: list[Stream]) -> str:
    """What the switch would do, fuel by fuel, for the confirm page."""
    ready = _new_sources(streams)
    lines = []
    for fuel in ("grid", "gas"):
        classifier = _FUEL_STREAM[fuel]
        if not any(s.classifier == classifier for s in streams):
            continue
        if fuel in ready:
            lines.append(
                f"- **{_FUEL_LABEL[fuel].capitalize()}:** the dashboard's "
                f"{_FUEL_LABEL[fuel]} source is replaced by HalfPast Meter's"
            )
        else:
            lines.append(
                f"- **{_FUEL_LABEL[fuel].capitalize()}:** left as it is, because "
                f"HalfPast Meter has no {_FUEL_LABEL[fuel]} data yet"
            )
    lines.append("- Solar, batteries, water and devices are not touched.")
    return "\n".join(lines)


async def async_switch(hass: HomeAssistant, streams: list[Stream]) -> list[str]:
    """Put our sources in place of grid and gas; returns the fuels switched.

    The dashboard as it was is stored first. Each replaced source keeps its
    position in the list; a fuel without data is left untouched.
    """
    new = _new_sources(streams)
    if not new:
        return []
    manager = await _async_manager(hass)
    current = [dict(s) for s in (manager.data or {}).get("energy_sources", [])]

    store: Store[dict[str, Any]] = Store(hass, BACKUP_VERSION, BACKUP_KEY)
    await store.async_save({"energy_sources": current})

    result: list[dict[str, Any]] = []
    placed: set[str] = set()
    for source in current:
        kind = source.get("type")
        if kind in new:
            if kind not in placed:
                result.append(new[kind])
                placed.add(kind)
            continue
        result.append(source)
    for kind, source in new.items():
        if kind not in placed:
            result.append(source)

    await manager.async_update({"energy_sources": _validated(result)})
    return [_FUEL_LABEL[k] for k in ("grid", "gas") if k in new]


async def async_has_backup(hass: HomeAssistant) -> bool:
    """Whether a dashboard from before a switch is stored."""
    store: Store[dict[str, Any]] = Store(hass, BACKUP_VERSION, BACKUP_KEY)
    return bool(await store.async_load())


async def async_restore(hass: HomeAssistant) -> bool:
    """Put the dashboard back as it was before the last switch."""
    store: Store[dict[str, Any]] = Store(hass, BACKUP_VERSION, BACKUP_KEY)
    saved = await store.async_load()
    if not saved:
        return False
    manager = await _async_manager(hass)
    await manager.async_update(
        {"energy_sources": _validated(saved.get("energy_sources", []))}
    )
    await store.async_remove()
    return True


def _validated(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Run the list through Home Assistant's own Energy source schema."""
    from homeassistant.components.energy import data as energy_data

    schema = getattr(energy_data, "ENERGY_SOURCE_SCHEMA", None)
    return list(schema(sources)) if schema is not None else sources
