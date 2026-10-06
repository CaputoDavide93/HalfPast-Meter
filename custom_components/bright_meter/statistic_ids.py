"""Statistic ids: given once per Glowmarkt resource, kept for good.

An external statistic's id is where its whole history lives. If it changed,
for instance because a second site joined the account, every hour imported so
far would be orphaned and the Energy dashboard would start again from nothing.
So each resource is given an id the first time it is seen, the assignment is
stored, and it is never recomputed.

Ids are unique across every config entry: two Bright accounts must not write
into one series. The first stream of a kind gets the readable id
(`bright_meter:electricity_consumption`); any later stream of the same kind
gets a suffix derived from its resource id, never from the site name, which is
often an address and would otherwise surface in diagnostics.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import slugify

from .const import DOMAIN

STORAGE_KEY = f"{DOMAIN}.statistic_ids"
STORAGE_VERSION = 1

_DATA_LOCK = "statistic_ids_lock"
_DATA_STORE = "statistic_ids_store"
_DATA_IDS = "statistic_ids"


def _suffix(resource_id: str) -> str:
    return hashlib.sha256(resource_id.encode()).hexdigest()[:8]


async def async_assign_statistic_ids(
    hass: HomeAssistant, resources: list[tuple[str, str]]
) -> dict[str, str]:
    """Return {resource_id: statistic_id} for (resource_id, classifier) pairs.

    Resources already assigned keep their id. New ones are taken in resource
    id order, so a first assignment does not depend on the order the API lists
    sites in.
    """
    data: dict[str, Any] = hass.data.setdefault(DOMAIN, {})
    lock: asyncio.Lock = data.setdefault(_DATA_LOCK, asyncio.Lock())
    async with lock:
        store: Store[dict[str, dict[str, str]]] = data.setdefault(
            _DATA_STORE, Store(hass, STORAGE_VERSION, STORAGE_KEY)
        )
        if _DATA_IDS not in data:
            stored = await store.async_load() or {}
            data[_DATA_IDS] = dict(stored.get("ids", {}))
        ids: dict[str, str] = data[_DATA_IDS]

        used = set(ids.values())
        changed = False
        for resource_id, classifier in sorted(resources):
            if resource_id in ids:
                continue
            base = f"{DOMAIN}:{slugify(classifier)}"
            statistic_id = (
                base if base not in used else f"{base}_{_suffix(resource_id)}"
            )
            ids[resource_id] = statistic_id
            used.add(statistic_id)
            changed = True

        if changed:
            await store.async_save({"ids": ids})
        return {resource_id: ids[resource_id] for resource_id, _ in resources}
