"""Coordinator and statistics backfill for Glowmarkt."""

from __future__ import annotations

import asyncio
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
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import EnergyConverter

from .api import (
    GlowmarktAccessDeniedError,
    GlowmarktAuthError,
    GlowmarktClient,
    GlowmarktConnectionError,
    GlowmarktError,
    GlowmarktTimeoutError,
    Tariff,
)
from .const import (
    ANCHOR_LOOKBACK_STEPS_DAYS,
    BACKFILL_SHUTDOWN_TIMEOUT,
    CATCHUP_INTERVAL,
    CONF_BACKFILL_DAYS,
    CONF_SITES,
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
from .statistic_ids import async_assign_statistic_ids

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
        # Kept on disk: an in-memory timer restarted with every Home Assistant
        # restart and setup retry, so the two-hour rule was not a rule.
        self._catchup_store: Store[dict[str, str]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.catchup"
        )
        self._catchup_loaded = False
        self._backfill_task: asyncio.Task[None] | None = None
        # Per resource: the newest half-hour Glowmarkt holds, and the hourly
        # values of the refresh window (None for a gap), from the last backfill.
        self._last_times: dict[str, datetime | None] = {}
        self._recent: dict[str, dict[datetime, float | None]] = {}
        self._data_until: dict[str, datetime | None] = {}
        self._tariffs: dict[str, Tariff | None] = {}
        self._tariffs_read: datetime | None = None
        # Streams in trouble (refused or erroring), so each is warned about once
        # until it answers again.
        self._stream_trouble: set[str] = set()

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

        # Sites chosen in the guided setup; an entry without the key takes all.
        chosen = self.config_entry.data.get(CONF_SITES)
        discovered: list[dict[str, Any]] = []
        for entity in virtual_entities:
            ve_id = entity.get("veId")
            if not ve_id:
                continue
            if chosen is not None and ve_id not in chosen:
                continue
            ve_name = entity.get("name") or "Glowmarkt"
            try:
                resources = await self.client.async_get_resources(ve_id)
            except GlowmarktAuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
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

        # Each resource keeps the statistic id it was first given; see
        # statistic_ids.py for why an id must never change.
        statistic_ids = await async_assign_statistic_ids(
            self.hass,
            [(item["resource_id"], item["classifier"]) for item in discovered],
        )
        self.resources = [
            GlowmarktResource(
                resource_id=item["resource_id"],
                ve_id=item["ve_id"],
                ve_name=item["ve_name"],
                classifier=item["classifier"],
                info=item["info"],
                statistic_id=statistic_ids[item["resource_id"]],
            )
            for item in discovered
        ]

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
            failures: list[GlowmarktConnectionError] = []
            for resource in self.resources:
                try:
                    self._last_times[resource.resource_id] = (
                        await self.client.async_get_last_time(resource.resource_id)
                    )
                    self._stream_trouble.discard(resource.resource_id)
                except GlowmarktConnectionError as err:
                    # One stream Bright will not share or answers an error for
                    # (a meter it is still fixing, say) must not take the
                    # others down with it.
                    self._last_times[resource.resource_id] = None
                    self._note_stream_trouble(resource, err)
                    failures.append(err)
            if failures and len(failures) == len(self.resources):
                # Nothing answered: that is Bright being down, not one stream.
                raise failures[0]
            await self._async_read_tariffs_if_due()
        except GlowmarktAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except GlowmarktConnectionError as err:
            raise UpdateFailed(str(err)) from err

        # Statistics run separately so a slow first backfill never delays or
        # fails the entity update; it publishes its results when it finishes.
        self._schedule_backfill()

        return self._snapshot()

    def _note_stream_trouble(
        self, resource: GlowmarktResource, err: GlowmarktConnectionError
    ) -> None:
        """Warn once per stream, until it answers again, then keep quiet."""
        if resource.resource_id in self._stream_trouble:
            return
        self._stream_trouble.add(resource.resource_id)
        what = (
            "refuses to share"
            if isinstance(err, GlowmarktAccessDeniedError)
            else "answered an error for"
        )
        _LOGGER.warning(
            "Bright %s %s; the other streams carry on, and it is retried every "
            "pass (%s)",
            what,
            resource.statistic_id,
            err,
        )

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
            failed = False
            for candidate in candidates:
                try:
                    tariff = await self.client.async_get_tariff(candidate.resource_id)
                except GlowmarktAuthError:
                    raise
                except GlowmarktError as err:
                    # One stream failing says nothing about the other: try it.
                    _LOGGER.debug(
                        "Tariff read for %s failed: %s", candidate.statistic_id, err
                    )
                    failed = True
                    continue
                if tariff is not None:
                    break
            if tariff is None and failed:
                # Every stream that might know either failed or had nothing:
                # keep the last rates known rather than blank them.
                tariff = self._tariffs.get(consumption.resource_id)
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
        # asyncio.wait, not asyncio.timeout: a timeout cancels what it awaits,
        # which is exactly what this exists to avoid. When the wait gives up,
        # the import is still running and is left to finish on its own.
        _, pending = await asyncio.wait({task}, timeout=BACKFILL_SHUTDOWN_TIMEOUT)
        if pending:
            _LOGGER.warning(
                "Statistics import still running after %s s; leaving it to finish",
                BACKFILL_SHUTDOWN_TIMEOUT,
            )

    async def _async_catchup_if_due(self) -> None:
        """Prompt Glowmarkt to pull fresh DCC readings, at most every 2 hours.

        Nothing else triggers this for an API-only consumer. Skip it and the
        platform simply stops collecting, and the readings endpoint answers 0
        for every hour it has no data for — which looks exactly like a house
        that has stopped using electricity.
        """
        if not self._catchup_loaded:
            stored = await self._catchup_store.async_load() or {}
            self._last_catchup = dt_util.parse_datetime(stored.get("last", "") or "")
            self._catchup_loaded = True

        now = dt_util.utcnow()
        if (
            self._last_catchup is not None
            and now - self._last_catchup < CATCHUP_INTERVAL
        ):
            return

        self._last_catchup = now
        await self._catchup_store.async_save({"last": now.isoformat()})
        for resource in self.resources:
            await self.client.async_catchup(resource.resource_id)

    async def _async_sum_before(
        self, statistic_id: str, moment: datetime
    ) -> float | None:
        """Return the cumulative sum of the last stored hour before `moment`.

        Searched in widening steps, out to the deepest history the options
        allow, because a long gap before the window is legitimate: a total
        restarted at zero beside older rows that are kept is a step down of
        everything imported so far. None means there is no earlier row at all.
        """
        for days in ANCHOR_LOOKBACK_STEPS_DAYS:
            rows = await get_instance(self.hass).async_add_executor_job(
                statistics_during_period,
                self.hass,
                moment - timedelta(days=days),
                moment,
                {statistic_id},
                "hour",
                None,
                {"sum"},
            )
            series = rows.get(statistic_id) or []
            if series:
                return float(series[-1].get("sum") or 0.0)
        return None

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

    async def _async_backfill_guarded(self) -> None:
        """Run the import; publish the sensors only if it succeeded.

        A failed or cancelled import must not call async_set_updated_data:
        that marks the coordinator successful, so a rejected password would
        bring the entities back as available, showing stale figures.
        """
        try:
            await self.async_backfill()
        except GlowmarktAuthError:
            # The poll would raise this too, but say so now rather than in
            # thirty minutes: it starts the reauthentication flow.
            self.config_entry.async_start_reauth(self.hass)
        except GlowmarktConnectionError as err:
            _LOGGER.warning("Glowmarkt statistics import stopped: %s", err)
        except Exception:
            _LOGGER.exception("Glowmarkt statistics backfill failed")
        else:
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
                try:
                    await self._async_backfill_resource(resource)
                except GlowmarktConnectionError as err:
                    # This stream's import stops here, writing nothing; the
                    # others go ahead, and the next pass tries it again.
                    self._note_stream_trouble(resource, err)

            await self._store.async_save(self._depth_applied)

    @staticmethod
    def _merge_readings(
        into: dict[datetime, float | None],
        readings: list[tuple[int, float | None]],
        start: datetime,
        end: datetime,
    ) -> None:
        """Add one response's hours to `into`: aligned, in range, once each.

        Responses can repeat an hour or arrive in any order. Summing them as
        they come would count a repeated hour twice and build the running total
        out of time order. A repeated hour with a different value is not
        guessed at: the pass fails and the next one tries again.
        """
        for timestamp, value in readings:
            moment = dt_util.utc_from_timestamp(timestamp)
            if moment < start or moment >= end:
                continue
            if moment.minute or moment.second:
                raise GlowmarktConnectionError(
                    f"Reading at {moment.isoformat()} is not on the hour"
                )
            if moment in into and into[moment] != value:
                raise GlowmarktConnectionError(
                    f"Two different readings for {moment.isoformat()}"
                )
            into[moment] = value

    async def _async_read_span(
        self,
        resource: GlowmarktResource,
        hourly: dict[datetime, float | None],
        start: datetime,
        end: datetime,
    ) -> None:
        """Read [start, end) into `hourly`, halving the span if it times out.

        Glowmarkt can take longer than any sensible limit to assemble a month
        of hourly readings for some streams. A span that times out is split in
        two and each half read on its own, down to a single day; a day that
        still times out fails the pass, and the next one tries again.
        """
        try:
            readings = await self.client.async_get_readings(
                resource.resource_id,
                start.replace(tzinfo=None),
                (end - timedelta(seconds=1)).replace(tzinfo=None),
                PERIOD_HOURLY,
                offset_minutes=0,
            )
        except GlowmarktTimeoutError:
            if end - start <= timedelta(days=1):
                raise
            middle = start + (end - start) / 2
            middle = middle.replace(minute=0, second=0, microsecond=0)
            _LOGGER.debug(
                "Readings for %s timed out over %s to %s; reading it in halves",
                resource.statistic_id,
                start.isoformat(),
                end.isoformat(),
            )
            await self._async_read_span(resource, hourly, start, middle)
            await asyncio.sleep(CHUNK_DELAY_SECONDS)
            await self._async_read_span(resource, hourly, middle, end)
            return
        self._merge_readings(hourly, readings, start, end)

    async def _async_backfill_resource(self, resource: GlowmarktResource) -> None:
        """Import the hourly statistics this resource has data for.

        The edge of the data is `last-time`, the newest half-hour Glowmarkt has
        collected: an hour is imported only once both its half-hours are in.
        Before that edge a null reading is a gap, which gets no row, and a zero
        is a real zero.

        A trailing window is re-imported on every pass, because readings arrive
        late and get revised. Statistics are keyed by hour, so re-importing
        replaces them in place; the running sum is re-anchored to the last
        statistic before the window so the cumulative series stays consistent.
        A stored hour the pass has no reading for (it became a gap, or lies past
        the data) is rewritten as zero use carrying the total forward, so the
        series never steps back.

        Nothing is written until every read has succeeded: all chunks, the
        anchor and the stored hours. The rewrite then goes to the recorder as
        one batch, so a failure part-way leaves the stored series as it was.

        If the configured history depth has been increased since the last run,
        the series is instead cleared and rebuilt from the deeper start: rows
        cannot simply be prepended, because `sum` is cumulative and every later
        row would need recomputing. The clear is queued only after the reads,
        on the recorder's FIFO queue ahead of the import.
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
                    # No stored row anywhere before the window: the series
                    # starts inside it, so build it from the configured start.
                    cursor = history_start
                    anchor_sum = 0.0
                else:
                    anchor_sum = found
            else:
                anchor_sum = 0.0
                cursor = history_start

        # The sensors' week is read whatever the import depth, so a shallow
        # history still has a whole yesterday to report.
        recent_from = current_hour - timedelta(hours=REFRESH_WINDOW_HOURS)
        fetch_from = min(cursor, recent_from)

        hourly: dict[datetime, float | None] = {}
        chunk_start = fetch_from
        while chunk_start < data_until:
            if self.hass.is_stopping:
                _LOGGER.debug("Home Assistant is stopping, abandoning backfill")
                return
            chunk_end = min(chunk_start + timedelta(days=HOURLY_CHUNK_DAYS), data_until)
            await self._async_read_span(resource, hourly, chunk_start, chunk_end)
            chunk_start = chunk_end
            if chunk_start < data_until:
                await asyncio.sleep(CHUNK_DELAY_SECONDS)

        def scaled(value: float | None) -> float | None:
            if value is None or not resource.info.is_cost:
                return value
            return value / PENCE_PER_POUND

        self._recent[resource.resource_id] = {
            moment: scaled(value)
            for moment, value in hourly.items()
            if moment >= recent_from
        }

        # Every read is done; work out the whole rewrite before touching it.
        stored = (
            set()
            if rebuild
            else set(
                await self._async_hours_with_statistics(
                    statistic_id, cursor, current_hour
                )
            )
        )
        if self.hass.is_stopping:
            return

        rows: list[StatisticData] = []
        running_sum = anchor_sum
        imported = 0
        moment = cursor
        while moment < current_hour:
            value = scaled(hourly.get(moment)) if moment < data_until else None
            if value is not None:
                running_sum += value
                rows.append(StatisticData(start=moment, state=value, sum=running_sum))
                imported += 1
            elif moment in stored:
                # Stored before, no reading now: flat, total carried forward.
                rows.append(StatisticData(start=moment, state=0.0, sum=running_sum))
            moment += timedelta(hours=1)

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

        if rebuild and applied_days:
            _LOGGER.info(
                "History depth for %s increased from %d to %d days, "
                "rebuilding the series",
                statistic_id,
                applied_days,
                requested_days,
            )
            # Queued on the recorder's FIFO queue, so it runs before the import.
            get_instance(self.hass).async_clear_statistics([statistic_id])
        if rows:
            async_add_external_statistics(self.hass, metadata, rows)

        # Recorded even when nothing came back, so a window reaching further
        # back than the meter's own history does not rebuild on every poll.
        self._depth_applied[statistic_id] = max(applied_days, requested_days)

        if imported:
            _LOGGER.debug(
                "Imported %d hourly statistics for %s", imported, statistic_id
            )
