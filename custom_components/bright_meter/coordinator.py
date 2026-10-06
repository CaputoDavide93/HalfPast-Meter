"""Coordinator and statistics backfill for Glowmarkt."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, tzinfo
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util, slugify
from homeassistant.util.unit_conversion import EnergyConverter

from .api import (
    GlowmarktAuthError,
    GlowmarktClient,
    GlowmarktConnectionError,
    GlowmarktError,
    Tariff,
)
from .const import (
    ANCHOR_LOOKBACK_DAYS,
    BACKFILL_SHUTDOWN_TIMEOUT,
    CATCHUP_INTERVAL,
    CONF_BACKFILL_DAYS,
    CURRENCY_GBP,
    DEFAULT_BACKFILL_DAYS,
    DOMAIN,
    HOURLY_CHUNK_DAYS,
    PENCE_PER_POUND,
    PERIOD_HOURLY,
    REFRESH_WINDOW_HOURS,
    SUPPORTED_CLASSIFIERS,
    TARIFF_INTERVAL,
    UPDATE_INTERVAL,
    ClassifierInfo,
)

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

# Be polite between chunked history requests; a deep backfill is dozens of calls.
CHUNK_DELAY_SECONDS = 0.2


@dataclass(frozen=True, kw_only=True)
class GlowmarktResource:
    """One Glowmarkt data stream, resolved to the things HA needs."""

    resource_id: str
    ve_id: str
    ve_name: str
    classifier: str
    info: ClassifierInfo
    statistic_id: str


@dataclass(frozen=True, kw_only=True)
class DayTotal:
    """One whole local day of a stream, every hour of it collected."""

    day: date
    value: float


@dataclass(frozen=True, kw_only=True)
class BrightData:
    """What the entities show, keyed by resource id.

    Nothing here is "today so far": the DCC feed is at least half an hour late
    and often a day, so a running total for today is wrong more often than
    right. The entities say instead how far the data reaches, the last whole
    day, and the rates the meter holds.
    """

    data_until: dict[str, datetime | None] = field(default_factory=dict)
    last_day: dict[str, DayTotal | None] = field(default_factory=dict)
    tariffs: dict[str, Tariff | None] = field(default_factory=dict)


def last_full_day(
    hourly: dict[datetime, float | None],
    data_until: datetime | None,
    time_zone: tzinfo,
    now: datetime,
) -> DayTotal | None:
    """The newest local day whose every hour is in `hourly`, summed.

    Local days are 23, 24 or 25 hours long across the clock changes, so they
    are built from local midnights rather than by counting 24 hours. A day with
    a gap anywhere in it is skipped rather than reported short.
    """
    if data_until is None or not hourly:
        return None
    day = now.astimezone(time_zone).date()
    earliest = min(hourly).astimezone(time_zone).date()
    while day >= earliest:
        day -= timedelta(days=1)
        start = dt_util.as_utc(datetime.combine(day, datetime.min.time(), time_zone))
        end = dt_util.as_utc(
            datetime.combine(day + timedelta(days=1), datetime.min.time(), time_zone)
        )
        if end > data_until:
            continue
        hours = []
        moment = start
        while moment < end:
            hours.append(hourly.get(moment))
            moment += timedelta(hours=1)
        if hours and all(value is not None for value in hours):
            return DayTotal(day=day, value=round(sum(hours), 6))
    return None


class GlowmarktCoordinator(DataUpdateCoordinator[BrightData]):
    """Keeps long-term statistics backfilled and reports the meter's tariff."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: GlowmarktClient,
    ) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
            config_entry=entry,
        )
        self.client = client
        self.resources: list[GlowmarktResource] = []
        self._backfill_lock = asyncio.Lock()

        # Remembers how many days of history have actually been imported for
        # each statistic. Without this, raising the backfill option after the
        # first run would do nothing, because the incremental path only ever
        # walks forward from the newest statistic it already holds.
        self._store: Store[dict[str, int]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.backfill"
        )
        self._depth_applied: dict[str, int] | None = None
        self._last_catchup: datetime | None = None
        self._backfill_task: asyncio.Task[None] | None = None
        # Per resource: the newest half-hour Glowmarkt holds, and the hourly
        # values of the refresh window (None for a gap), from the last backfill.
        self._last_times: dict[str, datetime | None] = {}
        self._recent: dict[str, dict[datetime, float | None]] = {}
        self._data_until: dict[str, datetime | None] = {}
        self._tariffs: dict[str, Tariff | None] = {}
        self._tariffs_read: datetime | None = None

    @property
    def _backfill_days(self) -> int:
        """How far back to reach on the very first backfill."""
        return int(
            self.config_entry.options.get(CONF_BACKFILL_DAYS, DEFAULT_BACKFILL_DAYS)
        )

    async def _async_setup(self) -> None:
        """Discover meters once, before the first refresh."""
        try:
            virtual_entities = await self.client.async_get_virtual_entities()
        except GlowmarktAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except GlowmarktConnectionError as err:
            raise UpdateFailed(str(err)) from err

        discovered: list[dict[str, Any]] = []
        for entity in virtual_entities:
            ve_id = entity.get("veId")
            if not ve_id:
                continue
            ve_name = entity.get("name") or "Glowmarkt"
            try:
                resources = await self.client.async_get_resources(ve_id)
            except GlowmarktConnectionError as err:
                raise UpdateFailed(str(err)) from err

            for resource in resources:
                classifier = resource.get("classifier")
                info = SUPPORTED_CLASSIFIERS.get(classifier or "")
                resource_id = resource.get("resourceId")
                if info is None or not resource_id:
                    continue
                if resource.get("active") is False:
                    continue
                discovered.append(
                    {
                        "resource_id": resource_id,
                        "ve_id": ve_id,
                        "ve_name": ve_name,
                        "classifier": classifier,
                        "info": info,
                    }
                )

        # Keep statistic IDs short and readable in the single-installation case,
        # which is nearly everyone, but disambiguate by installation name when
        # the same classifier shows up more than once.
        classifier_counts: dict[str, int] = {}
        for item in discovered:
            classifier_counts[item["classifier"]] = (
                classifier_counts.get(item["classifier"], 0) + 1
            )

        self.resources = []
        for item in discovered:
            slug = slugify(item["classifier"])
            if classifier_counts[item["classifier"]] > 1:
                slug = f"{slugify(item['ve_name'])}_{slug}"
            self.resources.append(
                GlowmarktResource(
                    resource_id=item["resource_id"],
                    ve_id=item["ve_id"],
                    ve_name=item["ve_name"],
                    classifier=item["classifier"],
                    info=item["info"],
                    statistic_id=f"{DOMAIN}:{slug}",
                )
            )

        if not self.resources:
            raise UpdateFailed(
                "No supported Glowmarkt resources found on this account"
            )

        _LOGGER.debug(
            "Discovered %d Glowmarkt resources: %s",
            len(self.resources),
            ", ".join(r.statistic_id for r in self.resources),
        )

    async def _async_update_data(self) -> BrightData:
        """Ask how far each stream's data reaches, and start the import.

        The `last-time` calls are cheap, one per stream, and they are what
        surfaces a rejected password or an unreachable API on every poll.
        """
        await self._async_catchup_if_due()

        try:
            for resource in self.resources:
                self._last_times[resource.resource_id] = (
                    await self.client.async_get_last_time(resource.resource_id)
                )
            await self._async_read_tariffs_if_due()
        except GlowmarktAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except GlowmarktConnectionError as err:
            raise UpdateFailed(str(err)) from err

        # Statistics run separately so a slow first backfill never delays or
        # fails the entity update; it publishes its results when it finishes.
        self._schedule_backfill()

        return self._snapshot()

    def _snapshot(self) -> BrightData:
        """The entity view of what the last import and tariff read found."""
        time_zone = dt_util.get_default_time_zone()
        now = dt_util.utcnow()
        return BrightData(
            data_until=dict(self._data_until),
            last_day={
                resource.resource_id: last_full_day(
                    self._recent.get(resource.resource_id, {}),
                    self._data_until.get(resource.resource_id),
                    time_zone,
                    now,
                )
                for resource in self.resources
            },
            tariffs=dict(self._tariffs),
        )

    async def _async_read_tariffs_if_due(self) -> None:
        """Read the rates the meter holds for each fuel, a few times a day.

        Asked of the consumption stream first and the cost stream second: which
        of the two answers is not documented. A read that fails keeps the last
        known rates rather than blanking them.
        """
        now = dt_util.utcnow()
        if (
            self._tariffs_read is not None
            and now - self._tariffs_read < TARIFF_INTERVAL
        ):
            return
        self._tariffs_read = now

        for consumption in self.resources:
            if consumption.info.is_cost or consumption.info.fuel is None:
                continue
            candidates = [consumption] + [
                r
                for r in self.resources
                if r.info.is_cost
                and r.info.fuel == consumption.info.fuel
                and r.ve_id == consumption.ve_id
            ]
            tariff: Tariff | None = None
            for candidate in candidates:
                try:
                    tariff = await self.client.async_get_tariff(candidate.resource_id)
                except GlowmarktAuthError:
                    raise
                except GlowmarktError as err:
                    _LOGGER.debug(
                        "Tariff read for %s failed: %s", candidate.statistic_id, err
                    )
                    tariff = self._tariffs.get(consumption.resource_id)
                    break
                if tariff is not None:
                    break
            self._tariffs[consumption.resource_id] = tariff

    def _schedule_backfill(self) -> None:
        """Start a backfill unless one is running or Home Assistant is stopping."""
        if self.hass.is_stopping:
            return
        if self._backfill_task is not None and not self._backfill_task.done():
            return
        self._backfill_task = self.config_entry.async_create_background_task(
            self.hass, self._async_backfill_guarded(), "bright_meter_backfill"
        )

    async def async_wait_for_backfill(self) -> None:
        """Let an in-flight backfill finish before the entry is torn down.

        Cancelling is not enough. The recorder lookups run in an executor
        thread, and abandoning the awaiting task leaves that thread querying a
        database the recorder may already be disposing of — which segfaults
        rather than raising.
        """
        task = self._backfill_task
        if task is None or task.done():
            return
        with contextlib.suppress(TimeoutError, asyncio.CancelledError):
            async with asyncio.timeout(BACKFILL_SHUTDOWN_TIMEOUT):
                await task

    async def _async_catchup_if_due(self) -> None:
        """Prompt Glowmarkt to pull fresh DCC readings, at most every 2 hours.

        Nothing else triggers this for an API-only consumer. Skip it and the
        platform simply stops collecting, and the readings endpoint answers 0
        for every hour it has no data for — which looks exactly like a house
        that has stopped using electricity.
        """
        now = dt_util.utcnow()
        if (
            self._last_catchup is not None
            and now - self._last_catchup < CATCHUP_INTERVAL
        ):
            return

        self._last_catchup = now
        for resource in self.resources:
            await self.client.async_catchup(resource.resource_id)

    async def _async_sum_before(
        self, statistic_id: str, moment: datetime
    ) -> float | None:
        """Return the cumulative sum immediately before `moment`, if known."""
        rows = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period,
            self.hass,
            moment - timedelta(days=ANCHOR_LOOKBACK_DAYS),
            moment,
            {statistic_id},
            "hour",
            None,
            {"sum"},
        )
        series = rows.get(statistic_id) or []
        if not series:
            return None
        return float(series[-1].get("sum") or 0.0)

    async def _async_hours_with_statistics(
        self, statistic_id: str, start: datetime, end: datetime
    ) -> list[datetime]:
        """Return the hours in [start, end) that already hold a statistic."""
        rows = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period,
            self.hass,
            start,
            end,
            {statistic_id},
            "hour",
            None,
            {"sum"},
        )
        return [
            dt_util.utc_from_timestamp(row["start"])
            for row in rows.get(statistic_id) or []
        ]

    async def _async_flatten_unbacked_rows(
        self,
        statistic_id: str,
        metadata: StatisticMetaData,
        start: datetime,
        end: datetime,
        written: list[StatisticData],
        anchor_sum: float,
    ) -> int:
        """Rewrite stored rows in [start, end) that this pass had no reading for.

        Every hour this pass wrote carries a freshly computed running total. A
        row it did not write, because the hour is now a gap or lies past the
        data, still carries whatever total it was stored with, and the series
        would step backwards there, reading as a large negative hour. Each such
        row is rewritten as zero with the total carried forward from the last
        hour before it, so no consumption is invented; the real figure replaces
        it when the reading lands.
        """
        rewritten = {row["start"] for row in written}
        stale = [
            moment
            for moment in await self._async_hours_with_statistics(
                statistic_id, start, end
            )
            if moment not in rewritten
        ]
        if not stale:
            return 0

        totals = [(row["start"], row["sum"] or 0.0) for row in written]
        rows: list[StatisticData] = []
        index = 0
        carried = anchor_sum
        for moment in sorted(stale):
            while index < len(totals) and totals[index][0] < moment:
                carried = totals[index][1]
                index += 1
            rows.append(StatisticData(start=moment, state=0.0, sum=carried))

        async_add_external_statistics(self.hass, metadata, rows)
        _LOGGER.debug(
            "Flattened %d hour(s) without a reading for %s", len(rows), statistic_id
        )
        return len(rows)

    async def _async_backfill_guarded(self) -> None:
        """Run the backfill, swallowing errors so the task never explodes."""
        try:
            await self.async_backfill()
        except GlowmarktAuthError:
            # The poll would raise this too, but say so now rather than in
            # thirty minutes: it starts the reauthentication flow.
            self.config_entry.async_start_reauth(self.hass)
        except GlowmarktConnectionError as err:
            _LOGGER.warning("Glowmarkt statistics import stopped: %s", err)
        except Exception:  # noqa: BLE001 - background task of last resort
            _LOGGER.exception("Glowmarkt statistics backfill failed")
        finally:
            if not self.hass.is_stopping:
                self.async_set_updated_data(self._snapshot())

    async def async_backfill(self) -> None:
        """Bring long-term statistics up to date for every resource."""
        if self._backfill_lock.locked():
            _LOGGER.debug("Backfill already running, skipping this pass")
            return

        async with self._backfill_lock:
            if self._depth_applied is None:
                self._depth_applied = await self._store.async_load() or {}

            for resource in self.resources:
                await self._async_backfill_resource(resource)

            await self._store.async_save(self._depth_applied)

    async def _async_backfill_resource(self, resource: GlowmarktResource) -> None:
        """Import the hourly statistics this resource has data for.

        The edge of the data is `last-time`, the newest half-hour Glowmarkt has
        collected: an hour is imported only once both its half-hours are in.
        Before that edge a null reading is a gap, which is left out rather than
        stored as zero, and a zero is a real zero.

        A trailing window is re-imported on every pass, because readings arrive
        late and get revised. Statistics are keyed by hour, so re-importing
        replaces them in place; the running sum is re-anchored to the last
        statistic before the window so the cumulative series stays consistent.

        If the configured history depth has been increased since the last run,
        the series is instead cleared and rebuilt from the deeper start: rows
        cannot simply be prepended, because `sum` is cumulative and every later
        row would need recomputing.
        """
        assert self._depth_applied is not None
        statistic_id = resource.statistic_id

        now = dt_util.utcnow()
        # Never import the hour in progress: it is still accumulating.
        current_hour = now.replace(minute=0, second=0, microsecond=0)

        # Asked again rather than reusing the poll's answer: a backfill can be
        # started on its own, and the edge may have moved since.
        last_time = await self.client.async_get_last_time(resource.resource_id)
        self._last_times[resource.resource_id] = last_time
        if last_time is None:
            _LOGGER.debug("No readings collected yet for %s", statistic_id)
            return
        # The last hour whose second half-hour has been collected, exclusive.
        data_until = min(
            current_hour,
            (last_time - timedelta(minutes=30)).replace(
                minute=0, second=0, microsecond=0
            )
            + timedelta(hours=1),
        )
        self._data_until[resource.resource_id] = data_until

        requested_days = self._backfill_days
        applied_days = self._depth_applied.get(statistic_id, 0)
        rebuild = requested_days > applied_days
        history_start = current_hour - timedelta(days=requested_days)

        if rebuild:
            if applied_days:
                _LOGGER.info(
                    "History depth for %s increased from %d to %d days, "
                    "rebuilding the series",
                    statistic_id,
                    applied_days,
                    requested_days,
                )
                # Queued on the recorder's FIFO queue, so it is guaranteed to
                # run before the imports below.
                get_instance(self.hass).async_clear_statistics([statistic_id])
            anchor_sum = 0.0
            cursor = history_start
        else:
            last_stats = await get_instance(self.hass).async_add_executor_job(
                get_last_statistics, self.hass, 1, statistic_id, True, {"sum"}
            )
            if last_stats and last_stats.get(statistic_id):
                row = last_stats[statistic_id][0]
                next_new_hour = dt_util.utc_from_timestamp(row["start"]) + timedelta(
                    hours=1
                )
                # Reach back over the recent window so revised readings, and
                # gaps that have since filled, replace what was stored.
                cursor = max(
                    history_start,
                    min(
                        next_new_hour,
                        current_hour - timedelta(hours=REFRESH_WINDOW_HOURS),
                    ),
                )
                found = await self._async_sum_before(statistic_id, cursor)
                if found is None:
                    # No stored row before the window to anchor the running
                    # total to: rebuild from the start of the configured history.
                    cursor = history_start
                    anchor_sum = 0.0
                else:
                    anchor_sum = found
            else:
                anchor_sum = 0.0
                cursor = history_start

        metadata = StatisticMetaData(
            mean_type=StatisticMeanType.NONE,
            has_sum=True,
            name=f"{resource.ve_name} {resource.info.label}",
            source=DOMAIN,
            statistic_id=statistic_id,
            unit_class=None if resource.info.is_cost else EnergyConverter.UNIT_CLASS,
            unit_of_measurement=(
                CURRENCY_GBP
                if resource.info.is_cost
                else UnitOfEnergy.KILO_WATT_HOUR
            ),
        )

        window_start = cursor
        running_sum = anchor_sum
        written: list[StatisticData] = []
        recent: dict[datetime, float | None] = {}
        recent_from = current_hour - timedelta(hours=REFRESH_WINDOW_HOURS)
        while cursor < data_until:
            if self.hass.is_stopping:
                _LOGGER.debug("Home Assistant is stopping, abandoning backfill")
                return

            chunk_end = min(cursor + timedelta(days=HOURLY_CHUNK_DAYS), data_until)
            readings = await self.client.async_get_readings(
                resource.resource_id,
                cursor.replace(tzinfo=None),
                (chunk_end - timedelta(seconds=1)).replace(tzinfo=None),
                PERIOD_HOURLY,
                offset_minutes=0,
            )

            batch: list[StatisticData] = []
            for timestamp, raw in readings:
                moment = dt_util.utc_from_timestamp(timestamp)
                if moment < cursor or moment >= chunk_end:
                    continue
                value = (
                    None
                    if raw is None
                    else raw / PENCE_PER_POUND if resource.info.is_cost else raw
                )
                if moment >= recent_from:
                    recent[moment] = value
                if value is None:
                    # A gap: no row, so it cannot pose as zero use.
                    continue
                running_sum += value
                batch.append(StatisticData(start=moment, state=value, sum=running_sum))

            # Import per chunk rather than accumulating the lot: a deep backfill
            # is tens of thousands of rows, and this way a failure part-way
            # leaves the depth marker unset so the next run simply starts over.
            if batch:
                async_add_external_statistics(self.hass, metadata, batch)
                written.extend(batch)

            cursor = chunk_end
            if cursor < data_until:
                await asyncio.sleep(CHUNK_DELAY_SECONDS)

        self._recent[resource.resource_id] = recent

        # Rows this pass did not write would drag the cumulative total
        # backwards: gaps that were stored before, and anything past the data.
        await self._async_flatten_unbacked_rows(
            statistic_id, metadata, window_start, current_hour, written, anchor_sum
        )

        # Recorded even when nothing came back, so a window reaching further
        # back than the meter's own history does not rebuild on every poll.
        self._depth_applied[statistic_id] = max(applied_days, requested_days)

        if written:
            _LOGGER.debug(
                "Imported %d hourly statistics for %s", len(written), statistic_id
            )
