"""Shared fixtures and canned API payloads for the Glowmarkt tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from custom_components.bright_meter.api import (
    GlowmarktAccessDeniedError,
    GlowmarktConnectionError,
    Tariff,
)

USERNAME = "meter@example.com"
PASSWORD = "hunter2"

# Well past any test run, so the client never decides the token is stale.
TOKEN_EXP = int(datetime(2099, 1, 1, tzinfo=UTC).timestamp())

AUTH_OK = {
    "valid": True,
    "name": "",
    "accountId": "acc-0001",
    "token": "jwt-token-0001",
    "exp": TOKEN_EXP,
}

# The API answers HTTP 200 with valid=false for a bad password, so this shape
# matters more than the status code.
AUTH_REJECTED = {"valid": False}

VE_ID = "ve-0001"
VE_ID_SECOND = "ve-0002"

RES_ELEC = "res-elec"
RES_ELEC_COST = "res-elec-cost"
RES_GAS = "res-gas"
RES_GAS_COST = "res-gas-cost"
RES_REACTIVE = "res-reactive"

VIRTUAL_ENTITIES = [{"name": "Smart Home", "veId": VE_ID, "resources": []}]

VIRTUAL_ENTITIES_TWO = [
    {"name": "Smart Home", "veId": VE_ID, "resources": []},
    {"name": "Holiday Cottage", "veId": VE_ID_SECOND, "resources": []},
]

# A dual-fuel account. The reactive resource is here on purpose: it must be
# ignored, since it is only meaningful for large non-domestic sites.
RESOURCES_DUAL_FUEL = [
    {
        "resourceId": RES_ELEC,
        "classifier": "electricity.consumption",
        "name": "electricity",
        "baseUnit": "kWh",
        "active": True,
    },
    {
        "resourceId": RES_ELEC_COST,
        "classifier": "electricity.consumption.cost",
        "name": "electricity cost",
        "baseUnit": "pence",
        "active": True,
    },
    {
        "resourceId": RES_GAS,
        "classifier": "gas.consumption",
        "name": "gas",
        "baseUnit": "kWh",
        "active": True,
    },
    {
        "resourceId": RES_GAS_COST,
        "classifier": "gas.consumption.cost",
        "name": "gas cost",
        "baseUnit": "pence",
        "active": True,
    },
    {
        "resourceId": RES_REACTIVE,
        "classifier": "electricity.import.reactive",
        "name": "reactive",
        "baseUnit": "kVARh",
        "active": True,
    },
]

RESOURCES_ELEC_ONLY = [RESOURCES_DUAL_FUEL[0], RESOURCES_DUAL_FUEL[1]]

# Today's totals the fake client hands back: kWh for energy, pence for cost.
DAILY_ENERGY = 12.5
DAILY_COST = 315.0
HOURLY_ENERGY = 0.5
HOURLY_COST = 12.0


TARIFF_ELEC = Tariff(unit_rate_p=24.46, standing_charge_p=49.66)
TARIFF_GAS = Tariff(unit_rate_p=8.62, standing_charge_p=12.53)


def _hour(moment: datetime) -> datetime:
    return moment.replace(minute=0, second=0, microsecond=0)


class FakeGlowmarktClient:
    """Stands in for GlowmarktClient so tests exercise our logic, not aiohttp.

    Generates one reading per hour across whatever range is asked for, which
    lets the backfill assertions be exact. It answers the way the real API does
    with `nulls=1`: an hour Glowmarkt has not collected is None, and
    `last-time` names the newest half-hour it holds.
    """

    def __init__(
        self,
        virtual_entities: list[dict] | None = None,
        resources: list[dict] | None = None,
        zero_from: datetime | None = None,
    ) -> None:
        """Set up the canned account shape.

        `zero_from` marks the first hour not collected from the DCC yet: hours
        at or after it come back as None, and `last-time` stops just before it.
        `gaps` are hours missing in the middle of the data; `zeros` are hours in
        which genuinely nothing was used. `values_past_last_time` makes the
        uncollected hours answer 0 instead of None, as the API does when it
        ignores `nulls=1`.
        """
        self.virtual_entities = (
            virtual_entities if virtual_entities is not None else VIRTUAL_ENTITIES
        )
        self.resources = (
            resources if resources is not None else RESOURCES_DUAL_FUEL
        )
        self.zero_from = zero_from
        self.gaps: set[datetime] = set()
        self.zeros: set[datetime] = set()
        self.values_past_last_time = False
        # Misbehaviour on demand: repeat the last row, answer newest-first,
        # fail the Nth readings call, fail tariff or resource reads, or block
        # every readings call on an event until a test releases it.
        self.duplicate_last = False
        self.reverse = False
        self.fail_readings_call: int | None = None
        self.tariff_errors: set[str] = set()
        self.resources_error: Exception | None = None
        self.readings_gate: asyncio.Event | None = None
        # Resource ids Bright refuses (401/403 even with a fresh session).
        self.refused: set[str] = set()
        self.tariffs: dict[str, Tariff | None] = {
            RES_ELEC: TARIFF_ELEC,
            RES_GAS: TARIFF_GAS,
        }
        self.readings_calls: list[tuple[str, str]] = []
        self.catchup_calls: list[str] = []
        self.tariff_calls: list[str] = []
        self.login_calls = 0

    def _uncollected_from(self) -> datetime:
        if self.zero_from is not None:
            return self.zero_from
        return _hour(datetime.now(UTC))

    async def async_login(self) -> None:
        """Pretend to authenticate."""
        self.login_calls += 1

    async def async_catchup(self, resource_id: str) -> bool:
        """Record that a DCC catchup was requested."""
        self.catchup_calls.append(resource_id)
        return True

    async def async_get_virtual_entities(self) -> list[dict]:
        """Return the canned virtual entities."""
        return self.virtual_entities

    async def async_get_resources(self, ve_id: str) -> list[dict]:
        """Return the canned resources for any virtual entity."""
        if self.resources_error is not None:
            raise self.resources_error
        if ve_id == VE_ID:
            return self.resources
        # Resource ids are unique across an account: a second site's meters
        # have ids of their own.
        return [{**r, "resourceId": f"{ve_id}-{r['resourceId']}"} for r in self.resources]

    async def async_get_last_time(self, resource_id: str) -> datetime | None:
        """The second half-hour of the last collected hour."""
        if resource_id in self.refused:
            raise GlowmarktAccessDeniedError(f"refused {resource_id}")
        return self._uncollected_from() - timedelta(minutes=30)

    async def async_get_tariff(self, resource_id: str) -> Tariff | None:
        """Return the canned rates the meter holds."""
        self.tariff_calls.append(resource_id)
        if resource_id in self.tariff_errors:
            raise GlowmarktConnectionError("tariff endpoint answered 404")
        return self.tariffs.get(resource_id)

    async def async_get_readings(
        self,
        resource_id: str,
        start: datetime,
        end: datetime,
        period: str,
        *,
        offset_minutes: int = 0,
        function: str = "sum",
    ) -> list[tuple[int, float | None]]:
        """Return canned readings for the requested window."""
        self.readings_calls.append((resource_id, period))
        if resource_id in self.refused:
            raise GlowmarktAccessDeniedError(f"refused {resource_id}")
        if self.readings_gate is not None:
            await self.readings_gate.wait()
        if self.fail_readings_call == len(self.readings_calls):
            raise GlowmarktConnectionError("readings answered 500")
        is_cost = resource_id.endswith("cost")

        if period == "P1D":
            value = DAILY_COST if is_cost else DAILY_ENERGY
            return [(int(start.replace(tzinfo=UTC).timestamp()), value)]

        value = HOURLY_COST if is_cost else HOURLY_ENERGY
        uncollected_from = self._uncollected_from()
        readings: list[tuple[int, float | None]] = []
        cursor = start.replace(tzinfo=UTC, minute=0, second=0, microsecond=0)
        limit = end.replace(tzinfo=UTC)
        while cursor <= limit:
            reading: float | None = value
            if cursor in self.zeros:
                reading = 0.0
            if cursor in self.gaps:
                reading = None
            if cursor >= uncollected_from:
                reading = 0.0 if self.values_past_last_time else None
            readings.append((int(cursor.timestamp()), reading))
            cursor += timedelta(hours=1)
        if self.duplicate_last and readings:
            readings.append(readings[-1])
        if self.reverse:
            readings.reverse()
        return readings
