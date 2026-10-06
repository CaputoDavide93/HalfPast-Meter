"""Async client for the Glowmarkt platform API.

Deliberately thin: it knows about authentication, the endpoints this
integration needs, and nothing about Home Assistant.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from aiohttp import ClientError, ClientSession, ClientTimeout

from .const import APPLICATION_ID, BASE_URL, FUNCTION_SUM

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT: Final = ClientTimeout(total=45)

# Tokens are documented as lasting 7 days. Renew an hour early so a long-running
# request can never straddle the expiry.
TOKEN_LEEWAY_SECONDS = 3600

API_TIME_FORMAT = "%Y-%m-%dT%H:%M:%S"


class GlowmarktError(Exception):
    """Base error for the Glowmarkt API."""


class GlowmarktAuthError(GlowmarktError):
    """Credentials were rejected."""


class GlowmarktConnectionError(GlowmarktError):
    """The API could not be reached, or returned something unusable."""


@dataclass(frozen=True, kw_only=True)
class Tariff:
    """The rates the meter itself holds, as reported over the DCC, in pence.

    Either figure may be missing: a supplier does not always send both, and
    some send nothing at all.
    """

    unit_rate_p: float | None
    standing_charge_p: float | None


def _number(value: Any) -> float | None:
    """A rate as a float, whether sent bare or wrapped as {"value": x}."""
    if isinstance(value, dict):
        value = value.get("value")
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_tariff(payload: Any) -> Tariff | None:
    """Read either documented tariff shape; None when there is no rate at all.

    The Swagger spec documents `plan[].planDetail[]` with one `rate` and one
    `standing` entry. Live accounts have also been seen answering with
    `currentRates.rate` / `currentRates.standingCharge`, each bare or wrapped as
    {"value": x}. When several plans are listed the newest `from` wins.
    """
    if not isinstance(payload, dict):
        return None
    entries = [e for e in payload.get("data") or [] if isinstance(e, dict)]
    if not entries:
        return None
    entry = max(entries, key=lambda e: str(e.get("from") or ""))

    rate: float | None = None
    standing: float | None = None
    for plan in entry.get("plan") or []:
        if not isinstance(plan, dict):
            continue
        for detail in plan.get("planDetail") or []:
            if not isinstance(detail, dict):
                continue
            if rate is None and "rate" in detail:
                rate = _number(detail["rate"])
            if standing is None and "standing" in detail:
                standing = _number(detail["standing"])

    current = entry.get("currentRates")
    if isinstance(current, dict):
        if rate is None:
            rate = _number(current.get("rate"))
        if standing is None:
            standing = _number(current.get("standingCharge"))

    if rate is None and standing is None:
        return None
    return Tariff(unit_rate_p=rate, standing_charge_p=standing)


class GlowmarktClient:
    """Talks to https://api.glowmarkt.com."""

    def __init__(
        self, session: ClientSession, username: str, password: str
    ) -> None:
        """Initialise the client."""
        self._session = session
        self._username = username
        self._password = password
        self._token: str | None = None
        self._token_expires: float = 0.0
        self._auth_lock = asyncio.Lock()

    async def async_login(self) -> None:
        """Exchange username/password for a JWT."""
        async with self._auth_lock:
            await self._async_login_locked()

    async def _async_login_locked(self) -> None:
        headers = {
            "Content-Type": "application/json",
            "applicationId": APPLICATION_ID,
        }
        payload = {"username": self._username, "password": self._password}

        try:
            async with self._session.post(
                f"{BASE_URL}/auth",
                json=payload,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            ) as response:
                if response.status in (401, 403):
                    raise GlowmarktAuthError("Glowmarkt rejected the credentials")
                if response.status >= 400:
                    raise GlowmarktConnectionError(
                        f"Authentication failed with HTTP {response.status}"
                    )
                data = await response.json()
        except GlowmarktError:
            raise
        except (ClientError, TimeoutError, ValueError) as err:
            raise GlowmarktConnectionError(
                f"Could not reach the Glowmarkt API: {err}"
            ) from err

        # The API returns HTTP 200 with valid=false for a bad password, so the
        # status code alone is not enough to tell success from failure.
        if not data.get("valid") or not data.get("token"):
            raise GlowmarktAuthError("Glowmarkt rejected the credentials")

        self._token = data["token"]
        self._token_expires = float(data.get("exp") or 0)

    async def _async_valid_token(self) -> str:
        """Return a live token, logging in again if the current one is stale."""
        async with self._auth_lock:
            expired = (
                self._token is None
                or self._token_expires <= time.time() + TOKEN_LEEWAY_SECONDS
            )
            if expired:
                await self._async_login_locked()
            assert self._token is not None
            return self._token

    async def _async_get(
        self, path: str, params: dict[str, Any] | None = None
    ) -> Any:
        """GET a path, retrying once if the token turns out to be dead."""
        for attempt in (1, 2):
            token = await self._async_valid_token()
            headers = {
                "Content-Type": "application/json",
                "applicationId": APPLICATION_ID,
                "token": token,
            }
            try:
                async with self._session.get(
                    f"{BASE_URL}{path}",
                    params=params,
                    headers=headers,
                    timeout=REQUEST_TIMEOUT,
                ) as response:
                    if response.status in (401, 403):
                        # Force a fresh login and try once more before giving up.
                        self._token = None
                        self._token_expires = 0.0
                        if attempt == 1:
                            continue
                        raise GlowmarktAuthError(
                            "Glowmarkt rejected the session token"
                        )
                    if response.status >= 400:
                        raise GlowmarktConnectionError(
                            f"GET {path} failed with HTTP {response.status}"
                        )
                    return await response.json()
            except GlowmarktError:
                raise
            except (ClientError, TimeoutError, ValueError) as err:
                raise GlowmarktConnectionError(
                    f"Could not reach the Glowmarkt API: {err}"
                ) from err

        raise GlowmarktConnectionError(f"GET {path} failed")

    async def async_get_virtual_entities(self) -> list[dict[str, Any]]:
        """List the virtual entities (installations) on the account."""
        result = await self._async_get("/virtualentity")
        if not isinstance(result, list):
            raise GlowmarktConnectionError(
                "Unexpected response listing virtual entities"
            )
        return result

    async def async_get_resources(self, ve_id: str) -> list[dict[str, Any]]:
        """Return the fully described resources belonging to a virtual entity."""
        result = await self._async_get(f"/virtualentity/{ve_id}/resources")
        if not isinstance(result, dict):
            raise GlowmarktConnectionError(
                f"Unexpected response fetching resources for {ve_id}"
            )
        return result.get("resources") or []

    async def async_catchup(self, resource_id: str) -> bool:
        """Ask Glowmarkt to fetch this resource's latest readings from the DCC.

        Without this the platform only refreshes when something else prompts it
        — opening the Bright app, typically — and the readings endpoint keeps
        returning 0 for hours it has no data for. Rate limited upstream to once
        every two hours.

        Returns True if the request was accepted. Never raises: a failed catchup
        should degrade to stale data, not break the update.
        """
        try:
            await self._async_get(f"/resource/{resource_id}/catchup")
        except GlowmarktError as err:
            _LOGGER.debug("Catchup for %s failed: %s", resource_id, err)
            return False
        return True

    async def async_get_readings(
        self,
        resource_id: str,
        start: datetime,
        end: datetime,
        period: str,
        *,
        offset_minutes: int = 0,
        function: str = FUNCTION_SUM,
    ) -> list[tuple[int, float | None]]:
        """Return [(utc_timestamp, value)] for a resource over a time range.

        `start` and `end` must be naive local-wall-clock datetimes in whatever
        timezone `offset_minutes` describes. Per the API docs the offset is the
        minutes between the requested timezone and UTC, negated — so BST
        (UTC+1) is -60.

        A period Glowmarkt has not collected yet comes back as None, because
        the request sets `nulls=1`. Without it the API answers 0, which cannot
        be told apart from a period in which nothing was used.
        """
        params = {
            "from": start.strftime(API_TIME_FORMAT),
            "to": end.strftime(API_TIME_FORMAT),
            "period": period,
            "offset": str(offset_minutes),
            "function": function,
            "nulls": "1",
        }
        result = await self._async_get(f"/resource/{resource_id}/readings", params)

        if not isinstance(result, dict):
            raise GlowmarktConnectionError(
                f"Unexpected readings response for resource {resource_id}"
            )

        readings: list[tuple[int, float | None]] = []
        for row in result.get("data") or []:
            # Rows are [timestamp, value]; value is null for periods with no data.
            if not isinstance(row, (list, tuple)) or len(row) < 2:
                continue
            timestamp, value = row[0], row[1]
            if timestamp is None or isinstance(timestamp, bool):
                continue
            try:
                stamp = int(timestamp)
                readings.append((stamp, None if value is None else float(value)))
            except (TypeError, ValueError):
                continue
        return readings

    async def async_get_last_time(self, resource_id: str) -> datetime | None:
        """Return the start of the newest reading Glowmarkt holds, in UTC.

        Everything at or after this moment has not been collected yet, so the
        importer stops there whatever the readings endpoint says.
        """
        result = await self._async_get(f"/resource/{resource_id}/last-time")
        data = result.get("data") if isinstance(result, dict) else None
        stamp = data.get("lastTs") if isinstance(data, dict) else None
        if stamp is None or isinstance(stamp, bool):
            return None
        try:
            return datetime.fromtimestamp(int(stamp), tz=UTC)
        except (TypeError, ValueError, OverflowError):
            return None

    async def async_get_tariff(self, resource_id: str) -> Tariff | None:
        """Return the rates the meter holds for this resource, if it has any."""
        return _parse_tariff(
            await self._async_get(f"/resource/{resource_id}/tariff")
        )
