"""Tests for the long-term statistics backfill.

This is the part that has no equivalent in the older Glow integrations, and the
part most likely to break quietly, so it is tested against a real recorder.
"""

from __future__ import annotations

from datetime import timedelta
from itertools import pairwise
from unittest.mock import patch

from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.bright_meter.const import CONF_BACKFILL_DAYS, DOMAIN

from .const import HOURLY_ENERGY, FakeGlowmarktClient

STAT_ID = f"{DOMAIN}:electricity_consumption"


async def _settle(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Let the background backfill finish before asserting or tearing down.

    It queries the recorder from an executor thread; leaving it in flight while
    the test harness disposes of the database is a segfault, not a failure.
    """
    await entry.runtime_data.async_wait_for_backfill()
    await hass.async_block_till_done()
    await async_wait_recording_done(hass)


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> FakeGlowmarktClient:
    client = FakeGlowmarktClient()
    entry.add_to_hass(hass)
    with patch("custom_components.bright_meter.GlowmarktClient", return_value=client):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    await _settle(hass, entry)
    return client


async def _rows(hass: HomeAssistant, stat_id: str = STAT_ID) -> list[dict]:
    """Read back every imported statistic for a series."""
    start = dt_util.utcnow() - timedelta(days=400)
    stats = await hass.async_add_executor_job(
        statistics_during_period,
        hass,
        start,
        None,
        {stat_id},
        "hour",
        None,
        {"state", "sum"},
    )
    return stats.get(stat_id, [])


async def test_backfill_imports_hourly_statistics(
    bright_env, hass: HomeAssistant
) -> None:
    """Setting up imports history rather than starting from zero."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="a@b.com",
        data={"username": "a@b.com", "password": "pw"},
        options={CONF_BACKFILL_DAYS: 2},
    )
    await _setup(hass, entry)

    rows = await _rows(hass)
    assert rows, "no statistics were imported"

    # Two days of hourly data, minus the hour still in progress.
    assert 44 <= len(rows) <= 49

    earliest = dt_util.utc_from_timestamp(rows[0]["start"])
    assert earliest >= dt_util.utcnow() - timedelta(days=2, hours=1)

    # sum is cumulative, so the last row carries the running total.
    assert rows[0]["state"] == HOURLY_ENERGY
    assert rows[-1]["sum"] > rows[0]["sum"]


async def test_increasing_depth_rebuilds_the_series(
    bright_env, hass: HomeAssistant
) -> None:
    """Raising the history depth reaches further back instead of doing nothing.

    The incremental path only ever walks forward, so a deeper window requires
    the series to be cleared and rebuilt.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="a@b.com",
        data={"username": "a@b.com", "password": "pw"},
        options={CONF_BACKFILL_DAYS: 1},
    )
    await _setup(hass, entry)

    shallow = await _rows(hass)
    assert shallow
    shallow_earliest = dt_util.utc_from_timestamp(shallow[0]["start"])

    # Ask for more history through the options flow, which reloads the entry.
    with patch(
        "custom_components.bright_meter.GlowmarktClient",
        return_value=FakeGlowmarktClient(),
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_BACKFILL_DAYS: 4}
        )
        await hass.async_block_till_done()
        await _settle(hass, entry)

    deep = await _rows(hass)
    deep_earliest = dt_util.utc_from_timestamp(deep[0]["start"])

    assert deep_earliest < shallow_earliest
    assert len(deep) > len(shallow)
    # Rebuilt from scratch, so the running sum restarts rather than continuing
    # on from the shallow series.
    assert deep[0]["sum"] == HOURLY_ENERGY


async def test_second_pass_does_not_duplicate(
    bright_env, hass: HomeAssistant
) -> None:
    """Re-running the backfill at the same depth imports nothing new."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="a@b.com",
        data={"username": "a@b.com", "password": "pw"},
        options={CONF_BACKFILL_DAYS: 2},
    )
    await _setup(hass, entry)
    first = await _rows(hass)

    await entry.runtime_data.async_backfill()
    await _settle(hass, entry)

    # Same hours, same states, same running totals: not merely the same count.
    assert await _rows(hass) == first


async def test_uncollected_zero_tail_is_not_imported(
    bright_env, hass: HomeAssistant
) -> None:
    """Trailing zeroes mean "not collected yet", so they must not be stored.

    Importing them bakes a zero into the cumulative sum, and because the
    incremental pass only walks forward the real reading would never replace
    it — the meter would appear to stop, permanently.
    """
    zero_from = dt_util.utcnow().replace(
        minute=0, second=0, microsecond=0
    ) - timedelta(hours=5)

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="a@b.com",
        data={"username": "a@b.com", "password": "pw"},
        options={CONF_BACKFILL_DAYS: 2},
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.bright_meter.GlowmarktClient",
        return_value=FakeGlowmarktClient(zero_from=zero_from),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    await _settle(hass, entry)

    rows = await _rows(hass)
    assert rows

    assert not any(row["state"] == 0 for row in rows), "stored an uncollected hour"
    latest = dt_util.utc_from_timestamp(rows[-1]["start"])
    assert latest < zero_from


async def test_late_data_replaces_previously_zero_hours(
    bright_env, hass: HomeAssistant
) -> None:
    """Readings that arrive late overwrite what was already stored.

    The trailing window is re-imported on every pass precisely so a stalled
    feed repairs itself once the data lands.
    """
    now_hour = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    zero_from = now_hour - timedelta(hours=5)

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="a@b.com",
        data={"username": "a@b.com", "password": "pw"},
        options={CONF_BACKFILL_DAYS: 2},
    )
    entry.add_to_hass(hass)
    stalled = FakeGlowmarktClient(zero_from=zero_from)
    with patch("custom_components.bright_meter.GlowmarktClient", return_value=stalled):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    await _settle(hass, entry)

    before = await _rows(hass)
    stalled_latest = dt_util.utc_from_timestamp(before[-1]["start"])

    # The DCC catches up and the missing hours now return real values.
    stalled.zero_from = None
    await entry.runtime_data.async_backfill()
    await _settle(hass, entry)

    after = await _rows(hass)
    assert dt_util.utc_from_timestamp(after[-1]["start"]) > stalled_latest
    assert all(row["state"] == HOURLY_ENERGY for row in after)
    # Sums stay monotonic across the repaired window.
    sums = [row["sum"] for row in after]
    assert sums == sorted(sums)


async def test_catchup_is_requested_and_rate_limited(
    bright_env, hass: HomeAssistant
) -> None:
    """Each resource gets a DCC catchup, but not on every single poll."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="a@b.com",
        data={"username": "a@b.com", "password": "pw"},
        options={CONF_BACKFILL_DAYS: 1},
    )
    client = await _setup(hass, entry)

    resource_count = len(entry.runtime_data.resources)
    assert sorted(client.catchup_calls) == sorted(
        r.resource_id for r in entry.runtime_data.resources
    )

    # A refresh straight afterwards is inside the two-hour limit.
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert len(client.catchup_calls) == resource_count


async def test_stale_rows_past_the_data_are_flattened(
    bright_env, hass: HomeAssistant
) -> None:
    """Rows left beyond the readings we hold must not drag the total backwards.

    An earlier version stored uncollected hours as zeroes carrying a much older
    cumulative total. Trimming stops us overwriting them, so the series steps
    down and that hour reads as a large negative figure.
    """
    from homeassistant.components.recorder.models import (
        StatisticData,
        StatisticMeanType,
        StatisticMetaData,
    )
    from homeassistant.components.recorder.statistics import (
        async_add_external_statistics,
    )
    from homeassistant.const import UnitOfEnergy

    now_hour = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    zero_from = now_hour - timedelta(hours=6)

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="a@b.com",
        data={"username": "a@b.com", "password": "pw"},
        options={CONF_BACKFILL_DAYS: 2},
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.bright_meter.GlowmarktClient",
        return_value=FakeGlowmarktClient(zero_from=zero_from),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    await _settle(hass, entry)

    # Plant the damage an older version would have left behind: zero readings
    # past the real data, carrying a stale cumulative total.
    async_add_external_statistics(
        hass,
        StatisticMetaData(
            mean_type=StatisticMeanType.NONE,
            has_sum=True,
            name="stale",
            source=DOMAIN,
            statistic_id=STAT_ID,
            unit_class=None,
            unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        ),
        [
            StatisticData(start=zero_from + timedelta(hours=i), state=0.0, sum=1.0)
            for i in range(5)
        ],
    )
    await async_wait_recording_done(hass)

    def deltas(rows: list[dict]) -> list[float]:
        return [b["sum"] - a["sum"] for a, b in pairwise(rows)]

    assert min(deltas(await _rows(hass))) < 0, "test did not reproduce the fault"

    await entry.runtime_data.async_backfill()
    await _settle(hass, entry)

    repaired = await _rows(hass)
    assert min(deltas(repaired)) >= 0, "cumulative total still steps backwards"


async def _setup_with(
    hass: HomeAssistant, client: FakeGlowmarktClient, days: int = 2
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="a@b.com",
        data={"username": "a@b.com", "password": "pw"},
        options={CONF_BACKFILL_DAYS: days},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.bright_meter.GlowmarktClient", return_value=client):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    await _settle(hass, entry)
    return entry


def _starts(rows: list[dict]) -> list:
    return [dt_util.utc_from_timestamp(row["start"]) for row in rows]


async def test_a_real_zero_at_the_end_is_imported(
    bright_env, hass: HomeAssistant
) -> None:
    """Nothing used is a reading, and `last-time` says it was collected.

    Export is genuinely 0 for most of a winter. The old guess held any trailing
    run of zeroes back for 48 hours as "probably not collected"; with `nulls=1`
    and `last-time` there is nothing to guess.
    """
    now_hour = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    client = FakeGlowmarktClient()
    client.zeros = {now_hour - timedelta(hours=h) for h in range(1, 4)}

    await _setup_with(hass, client)

    rows = await _rows(hass)
    assert _starts(rows)[-1] == now_hour - timedelta(hours=1)
    assert [row["state"] for row in rows[-3:]] == [0.0, 0.0, 0.0]


async def test_hours_past_last_time_are_never_imported(
    bright_env, hass: HomeAssistant
) -> None:
    """Even if the API answers 0 instead of null, `last-time` is the edge."""
    now_hour = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    collected_until = now_hour - timedelta(hours=5)
    client = FakeGlowmarktClient(zero_from=collected_until)
    client.values_past_last_time = True

    await _setup_with(hass, client)

    rows = await _rows(hass)
    assert _starts(rows)[-1] == collected_until - timedelta(hours=1)
    assert all(row["state"] == HOURLY_ENERGY for row in rows)


async def test_a_gap_is_left_out_then_filled_days_later(
    bright_env, hass: HomeAssistant
) -> None:
    """A missing stretch is not stored as zero, and is repaired when it lands.

    Readings can arrive days late. The gap is 5 days old when it fills, beyond
    the old 72-hour refresh window, and the running total must come out right.
    """
    now_hour = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    gap = {now_hour - timedelta(days=5, hours=h) for h in range(3)}
    client = FakeGlowmarktClient()
    client.gaps = set(gap)

    entry = await _setup_with(hass, client, days=8)

    rows = await _rows(hass)
    assert not gap & set(_starts(rows)), "a missing hour was stored"
    sums = [row["sum"] for row in rows]
    assert sums == sorted(sums)

    client.gaps = set()
    await entry.runtime_data.async_backfill()
    await _settle(hass, entry)

    repaired = await _rows(hass)
    assert gap <= set(_starts(repaired))
    assert all(row["state"] == HOURLY_ENERGY for row in repaired)
    assert repaired[-1]["sum"] == len(repaired) * HOURLY_ENERGY


async def test_an_hour_that_goes_missing_again_does_not_step_backwards(
    bright_env, hass: HomeAssistant
) -> None:
    """A stored hour the API later reports as null is rewritten flat."""
    now_hour = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    client = FakeGlowmarktClient()
    entry = await _setup_with(hass, client)

    client.gaps = {now_hour - timedelta(hours=10)}
    await entry.runtime_data.async_backfill()
    await _settle(hass, entry)

    rows = await _rows(hass)
    deltas = [b["sum"] - a["sum"] for a, b in pairwise(rows)]
    assert min(deltas) >= 0
    assert rows[-1]["sum"] == (len(rows) - 1) * HOURLY_ENERGY


async def test_energy_statistics_declare_their_unit_class(
    bright_env, hass: HomeAssistant
) -> None:
    """kWh series are `energy`, so HA can convert them; money has no class."""
    from homeassistant.components.recorder.statistics import get_metadata

    await _setup_with(hass, FakeGlowmarktClient())

    metadata = await hass.async_add_executor_job(
        get_metadata,
        hass,
    )
    by_id = {sid: meta for sid, (_, meta) in metadata.items()}
    assert by_id[STAT_ID]["unit_class"] == "energy"
    assert by_id[f"{DOMAIN}:gas_consumption"]["unit_class"] == "energy"
    assert by_id[f"{DOMAIN}:electricity_consumption_cost"]["unit_class"] is None
    assert by_id[f"{DOMAIN}:electricity_consumption_cost"]["unit_of_measurement"] == "GBP"
