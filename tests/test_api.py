"""Tests for the Glowmarkt HTTP client."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.bright_meter.api import (
    GlowmarktAuthError,
    GlowmarktClient,
    GlowmarktConnectionError,
    Tariff,
)
from custom_components.bright_meter.const import BASE_URL

from .const import AUTH_OK, AUTH_REJECTED, PASSWORD, USERNAME


def _client(hass: HomeAssistant) -> GlowmarktClient:
    return GlowmarktClient(async_get_clientsession(hass), USERNAME, PASSWORD)


async def test_login_success(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A valid response yields a usable token."""
    aioclient_mock.post(f"{BASE_URL}/auth", json=AUTH_OK)

    client = _client(hass)
    await client.async_login()

    assert aioclient_mock.call_count == 1
    # applicationId identifies the Bright app and must always be sent.
    assert aioclient_mock.mock_calls[0][3]["applicationId"]


async def test_login_rejected_returns_http_200(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A bad password comes back as 200 with valid=false, not as a 401."""
    aioclient_mock.post(f"{BASE_URL}/auth", json=AUTH_REJECTED)

    with pytest.raises(GlowmarktAuthError):
        await _client(hass).async_login()


async def test_login_unauthorised_status(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An explicit 401 is also an auth error."""
    aioclient_mock.post(f"{BASE_URL}/auth", status=401)

    with pytest.raises(GlowmarktAuthError):
        await _client(hass).async_login()


async def test_login_server_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 500 is a connection problem, not a credential problem."""
    aioclient_mock.post(f"{BASE_URL}/auth", status=500)

    with pytest.raises(GlowmarktConnectionError):
        await _client(hass).async_login()


async def test_get_virtual_entities(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Virtual entities are returned and the token header is sent."""
    aioclient_mock.post(f"{BASE_URL}/auth", json=AUTH_OK)
    aioclient_mock.get(
        f"{BASE_URL}/virtualentity", json=[{"name": "Home", "veId": "ve-1"}]
    )

    result = await _client(hass).async_get_virtual_entities()

    assert [ve["veId"] for ve in result] == ["ve-1"]
    assert aioclient_mock.mock_calls[-1][3]["token"] == AUTH_OK["token"]


async def test_get_resources_unwraps_the_document(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The resources endpoint returns a VE document, not a bare list."""
    aioclient_mock.post(f"{BASE_URL}/auth", json=AUTH_OK)
    aioclient_mock.get(
        f"{BASE_URL}/virtualentity/ve-1/resources",
        json={"veId": "ve-1", "resources": [{"resourceId": "r1"}]},
    )

    result = await _client(hass).async_get_resources("ve-1")

    assert result == [{"resourceId": "r1"}]


async def test_readings_ask_for_nulls_and_keep_them_as_gaps(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A half-hour Glowmarkt has not collected is a gap, never a zero.

    Without `nulls=1` the API answers 0 for an hour it has no data for, which
    reads exactly like a house that used nothing. With it, the gap comes back as
    null, and the client hands it on as None so the importer can tell the two
    apart. Malformed rows are still dropped.
    """
    aioclient_mock.post(f"{BASE_URL}/auth", json=AUTH_OK)
    aioclient_mock.get(
        f"{BASE_URL}/resource/r1/readings",
        json={
            "status": "OK",
            "data": [
                [1523318400, 48.79],
                [1523322000, None],
                [1523325600, 0],
                ["bad", "row"],
                [1523329200],
            ],
        },
    )

    result = await _client(hass).async_get_readings(
        "r1", datetime(2018, 4, 10), datetime(2018, 4, 11), "PT1H"
    )

    assert result == [(1523318400, 48.79), (1523322000, None), (1523325600, 0.0)]
    query = aioclient_mock.mock_calls[-1][1].query
    assert query["nulls"] == "1"
    assert query["offset"] == "0"


async def test_expired_token_triggers_one_retry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 401 mid-session re-authenticates and retries the request once."""
    aioclient_mock.post(f"{BASE_URL}/auth", json=AUTH_OK)
    aioclient_mock.get(f"{BASE_URL}/virtualentity", status=401)

    with pytest.raises(GlowmarktAuthError):
        await _client(hass).async_get_virtual_entities()

    # Two auth attempts: the initial login, then the forced retry.
    auth_calls = [c for c in aioclient_mock.mock_calls if c[1].path.endswith("/auth")]
    assert len(auth_calls) == 2


async def test_last_time_is_the_newest_collected_reading(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """`last-time` says how far the data reaches, as an aware UTC datetime."""
    aioclient_mock.post(f"{BASE_URL}/auth", json=AUTH_OK)
    aioclient_mock.get(
        f"{BASE_URL}/resource/r1/last-time", json={"data": {"lastTs": 1613124000}}
    )

    result = await _client(hass).async_get_last_time("r1")

    assert result == datetime(2021, 2, 12, 10, 0, tzinfo=UTC)


async def test_last_time_missing_is_none(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A resource with no data yet has no last time, and that is not an error."""
    aioclient_mock.post(f"{BASE_URL}/auth", json=AUTH_OK)
    aioclient_mock.get(f"{BASE_URL}/resource/r1/last-time", json={"data": {}})

    assert await _client(hass).async_get_last_time("r1") is None


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        # The shape the Swagger spec documents.
        (
            {
                "data": [
                    {
                        "plan": [{"planDetail": [{"rate": 24.46}, {"standing": 49.66}]}],
                        "from": "2026-10-12 00:00:00",
                        "name": "TariffFromMeter",
                    }
                ]
            },
            Tariff(unit_rate_p=24.46, standing_charge_p=49.66),
        ),
        # The shape pyglowmarkt reads in the wild: scalar currentRates.
        (
            {"data": [{"currentRates": {"rate": 8.62, "standingCharge": 12.53}}]},
            Tariff(unit_rate_p=8.62, standing_charge_p=12.53),
        ),
        # The same, with each figure wrapped as {"value": x}.
        (
            {
                "data": [
                    {
                        "currentRates": {
                            "rate": {"value": "24.46"},
                            "standingCharge": {"value": "49.66"},
                        }
                    }
                ]
            },
            Tariff(unit_rate_p=24.46, standing_charge_p=49.66),
        ),
    ],
)
async def test_tariff_shapes(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    payload: dict,
    expected: Tariff,
) -> None:
    """Both documented tariff shapes parse to the same rates, in pence."""
    aioclient_mock.post(f"{BASE_URL}/auth", json=AUTH_OK)
    aioclient_mock.get(f"{BASE_URL}/resource/r1/tariff", json=payload)

    assert await _client(hass).async_get_tariff("r1") == expected


@pytest.mark.parametrize(
    "payload",
    [
        {"data": []},
        {"data": [{"plan": []}]},
        {"data": [{"currentRates": {}}]},
        {"error": "no tariff"},
    ],
)
async def test_tariff_absent_is_none(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, payload: dict
) -> None:
    """A supplier that sends no rate over the DCC leaves the tariff unknown."""
    aioclient_mock.post(f"{BASE_URL}/auth", json=AUTH_OK)
    aioclient_mock.get(f"{BASE_URL}/resource/r1/tariff", json=payload)

    assert await _client(hass).async_get_tariff("r1") is None


async def test_tariff_with_only_one_figure_keeps_it(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A rate without a standing charge is still worth reporting."""
    aioclient_mock.post(f"{BASE_URL}/auth", json=AUTH_OK)
    aioclient_mock.get(
        f"{BASE_URL}/resource/r1/tariff",
        json={"data": [{"plan": [{"planDetail": [{"rate": 24.46}]}]}]},
    )

    assert await _client(hass).async_get_tariff("r1") == Tariff(
        unit_rate_p=24.46, standing_charge_p=None
    )
