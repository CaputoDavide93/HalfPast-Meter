"""Regressions for the defects an independent review found and reproduced.

Each test states the failure it pins. Most are about one promise: whatever
the API, the recorder or Home Assistant's lifecycle does, the cumulative
series never steps backwards and never counts a kWh twice.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from itertools import pairwise
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.bright_meter import coordinator as coordinator_module
from custom_components.bright_meter.api import GlowmarktAuthError
from custom_components.bright_meter.const import CONF_BACKFILL_DAYS, DOMAIN

from .const import (
    HOURLY_ENERGY,
    RES_ELEC,
    RES_ELEC_COST,
    RESOURCES_ELEC_ONLY,
    TARIFF_ELEC,
    VIRTUAL_ENTITIES,
    VIRTUAL_ENTITIES_TWO,
    FakeGlowmarktClient,
)
from .test_statistics import STAT_ID, _rows, _settle

CLIENT = "custom_components.bright_meter.GlowmarktClient"


def _entry(days: int = 2, user: str = "a@b.com") -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=user,
        data={"username": user, "password": "pw"},
        options={CONF_BACKFILL_DAYS: days},
    )


async def _setup(
    hass: HomeAssistant, entry: MockConfigEntry, client: FakeGlowmarktClient
) -> None:
    entry.add_to_hass(hass)
    with patch(CLIENT, return_value=client):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    if entry.state is ConfigEntryState.LOADED:
        await _settle(hass, entry)


def _deltas(rows: list[dict]) -> list[float]:
    return [b["sum"] - a["sum"] for a, b in pairwise(rows)]


def _now_hour():
    return dt_util.utcnow().replace(minute=0, second=0, microsecond=0)


# ── the cumulative series ─────────────────────────────────────────────────


async def test_an_anchor_beyond_30_days_is_still_found(
    bright_env, hass: HomeAssistant
) -> None:
    """A long gap before the window must not restart the total at zero.

    Sixty days imported, then nothing for five weeks, then the depth lowered to
    seven days (which keeps the old rows). The re-import window starts a week
    ago; the last stored row before it is 46 days old. Looking back only 30
    days, the importer found nothing, restarted the sum at zero and left the
    older rows in place: a step of minus several hundred kWh.
    """
    now_hour = _now_hour()
    client = FakeGlowmarktClient()
    # Nothing from 45 days ago until the first hour of the week-long window.
    client.gaps = {
        now_hour - timedelta(hours=h) for h in range(7 * 24 + 1, 45 * 24)
    }
    entry = _entry(days=60)
    await _setup(hass, entry, client)

    with patch(CLIENT, return_value=client):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_BACKFILL_DAYS: 7}
        )
        await hass.async_block_till_done()
        await _settle(hass, entry)

    rows = await _rows(hass)
    assert min(_deltas(rows)) >= 0
    assert rows[-1]["sum"] == len(rows) * HOURLY_ENERGY


async def test_a_repeated_hour_is_counted_once(
    bright_env, hass: HomeAssistant
) -> None:
    """The recorder keeps one row per hour; the sum must too."""
    client = FakeGlowmarktClient()
    client.duplicate_last = True
    await _setup(hass, _entry(), client)

    rows = await _rows(hass)
    assert rows[-1]["sum"] == len(rows) * HOURLY_ENERGY


async def test_readings_newest_first_still_sum_in_time_order(
    bright_env, hass: HomeAssistant
) -> None:
    """A running total is chronological whatever order the API answers in."""
    client = FakeGlowmarktClient()
    client.reverse = True
    await _setup(hass, _entry(), client)

    rows = await _rows(hass)
    assert min(_deltas(rows)) >= 0
    assert rows[-1]["sum"] == len(rows) * HOURLY_ENERGY


async def test_a_failed_chunk_leaves_the_stored_series_untouched(
    bright_env, hass: HomeAssistant
) -> None:
    """Nothing is written until every chunk of the pass has been read.

    Forty days is two 30-day chunks. Writing chunk by chunk, a failure on the
    second left the first rewritten with new totals beside old ones.
    """
    client = FakeGlowmarktClient()
    entry = _entry(days=40)
    await _setup(hass, entry, client)
    before = await _rows(hass)

    with patch(CLIENT, return_value=client):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        client.readings_calls.clear()
        # The rebuild reads electricity in two chunks; fail the second.
        client.fail_readings_call = 2
        await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_BACKFILL_DAYS: 45}
        )
        await hass.async_block_till_done()
        await _settle(hass, entry)

    assert await _rows(hass) == before


async def test_one_day_of_history_still_reports_the_last_full_day(
    bright_env, hass: HomeAssistant
) -> None:
    """The sensors read their own week, whatever the import depth is."""
    await _setup(hass, _entry(days=1), FakeGlowmarktClient())

    state = hass.states.get("sensor.smart_home_electricity_last_full_day")
    assert state.state not in ("unknown", "unavailable")


# ── lifecycle ─────────────────────────────────────────────────────────────


async def test_a_failed_import_does_not_report_success(
    bright_env, hass: HomeAssistant
) -> None:
    """A rejected password in the import must not mark the entry healthy."""
    client = FakeGlowmarktClient()
    entry = _entry()
    await _setup(hass, entry, client)
    coordinator = entry.runtime_data
    coordinator.last_update_success = False

    async def rejected(resource_id: str) -> None:
        raise GlowmarktAuthError("rejected")

    client.async_get_last_time = rejected
    await coordinator._async_backfill_guarded()
    await hass.async_block_till_done()

    assert coordinator.last_update_success is False


async def test_waiting_for_the_import_never_cancels_it(
    bright_env, hass: HomeAssistant
) -> None:
    """Unload waits for the import; a timeout gives up waiting, nothing more."""
    client = FakeGlowmarktClient()
    entry = _entry()
    await _setup(hass, entry, client)
    coordinator = entry.runtime_data

    client.readings_gate = asyncio.Event()
    coordinator._schedule_backfill()
    await asyncio.sleep(0)
    task = coordinator._backfill_task
    assert task is not None and not task.done()

    with patch.object(coordinator_module, "BACKFILL_SHUTDOWN_TIMEOUT", 0.05):
        await coordinator.async_wait_for_backfill()

    assert not task.cancelled()
    client.readings_gate.set()
    await task
    await _settle(hass, entry)


async def test_tariff_fallback_survives_an_error_on_the_first_stream(
    bright_env, hass: HomeAssistant
) -> None:
    """A 404 from the consumption stream must not skip the cost stream."""
    client = FakeGlowmarktClient()
    client.tariffs = {RES_ELEC_COST: TARIFF_ELEC}
    client.tariff_errors = {RES_ELEC}
    await _setup(hass, _entry(), client)

    state = hass.states.get("sensor.smart_home_electricity_unit_rate")
    assert float(state.state) == 0.2446


async def test_a_rejected_password_during_discovery_asks_for_it(
    bright_env, hass: HomeAssistant
) -> None:
    """Discovery is part of setup; its auth failure starts reauth too."""
    client = FakeGlowmarktClient()
    client.resources_error = GlowmarktAuthError("rejected")
    entry = _entry()
    await _setup(hass, entry, client)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


# ── statistic ids ─────────────────────────────────────────────────────────


async def test_two_accounts_never_share_a_statistic(
    bright_env, hass: HomeAssistant
) -> None:
    """A second Bright account gets its own ids instead of overwriting."""
    first, second = _entry(user="a@b.com"), _entry(user="c@d.com")
    # Glowmarkt resource ids are unique per account.
    other = [
        {**r, "resourceId": f"other-{r['resourceId']}"} for r in RESOURCES_ELEC_ONLY
    ]
    await _setup(hass, first, FakeGlowmarktClient(resources=RESOURCES_ELEC_ONLY))
    await _setup(hass, second, FakeGlowmarktClient(resources=other))

    ids_first = {r.statistic_id for r in first.runtime_data.resources}
    ids_second = {r.statistic_id for r in second.runtime_data.resources}
    assert STAT_ID in ids_first
    assert not ids_first & ids_second


async def test_a_new_site_never_renames_an_existing_statistic(
    bright_env, hass: HomeAssistant
) -> None:
    """Ids are kept once given: a renamed id orphans the imported history."""
    entry = _entry()
    client = FakeGlowmarktClient(
        virtual_entities=VIRTUAL_ENTITIES, resources=RESOURCES_ELEC_ONLY
    )
    await _setup(hass, entry, client)
    assert STAT_ID in {r.statistic_id for r in entry.runtime_data.resources}

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    # The account gains a second site with the same kind of meter.
    two_sites = FakeGlowmarktClient(
        virtual_entities=VIRTUAL_ENTITIES_TWO, resources=RESOURCES_ELEC_ONLY
    )
    with patch(CLIENT, return_value=two_sites):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    await _settle(hass, entry)

    ids = sorted(r.statistic_id for r in entry.runtime_data.resources)
    assert STAT_ID in ids
    assert len(set(ids)) == 4


async def test_statistic_ids_carry_no_site_name(
    bright_env, hass: HomeAssistant
) -> None:
    """Site names are often addresses, and ids appear in diagnostics."""
    entry = _entry()
    await _setup(
        hass,
        entry,
        FakeGlowmarktClient(
            virtual_entities=VIRTUAL_ENTITIES_TWO, resources=RESOURCES_ELEC_ONLY
        ),
    )

    for resource in entry.runtime_data.resources:
        assert "smart_home" not in resource.statistic_id
        assert "holiday_cottage" not in resource.statistic_id


async def test_ids_survive_a_restart(bright_env, hass: HomeAssistant) -> None:
    """The same resource gets the same id however the discovery is ordered."""
    entry = _entry()
    await _setup(
        hass,
        entry,
        FakeGlowmarktClient(
            virtual_entities=VIRTUAL_ENTITIES_TWO, resources=RESOURCES_ELEC_ONLY
        ),
    )
    first = {
        (r.ve_id, r.resource_id): r.statistic_id
        for r in entry.runtime_data.resources
    }
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    reordered = FakeGlowmarktClient(
        virtual_entities=list(reversed(VIRTUAL_ENTITIES_TWO)),
        resources=RESOURCES_ELEC_ONLY,
    )
    with patch(CLIENT, return_value=reordered):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    await _settle(hass, entry)

    again = {
        (r.ve_id, r.resource_id): r.statistic_id
        for r in entry.runtime_data.resources
    }
    assert again == first
    await async_wait_recording_done(hass)
