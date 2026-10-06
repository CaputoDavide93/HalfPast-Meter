"""Tests for the Glowmarkt config and reauth flows."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bright_meter.api import (
    GlowmarktAuthError,
    GlowmarktConnectionError,
)
from custom_components.bright_meter.const import DOMAIN

from .const import PASSWORD, USERNAME, VIRTUAL_ENTITIES

CLIENT_PATH = "custom_components.bright_meter.config_flow.GlowmarktClient"


def _mock_client(virtual_entities=VIRTUAL_ENTITIES, login_error=None):
    client = AsyncMock()
    if login_error is not None:
        client.async_login.side_effect = login_error
    client.async_get_virtual_entities.return_value = virtual_entities
    return client


async def test_user_flow_creates_entry(
    bright_env, hass: HomeAssistant
) -> None:
    """Valid credentials with at least one meter create an entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    with (
        patch(CLIENT_PATH, return_value=_mock_client()),
        patch("custom_components.bright_meter.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == USERNAME
    assert result["data"] == {CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD}


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (GlowmarktAuthError("nope"), "invalid_auth"),
        (GlowmarktConnectionError("down"), "cannot_connect"),
        (RuntimeError("boom"), "unknown"),
    ],
)
async def test_user_flow_errors(
    bright_env, hass: HomeAssistant, error: Exception, expected: str
) -> None:
    """Each failure mode maps to its own message rather than a generic one."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch(CLIENT_PATH, return_value=_mock_client(login_error=error)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}


async def test_user_flow_no_meters(
    bright_env, hass: HomeAssistant
) -> None:
    """Signing in successfully but with no meters is its own error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch(CLIENT_PATH, return_value=_mock_client(virtual_entities=[])):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_meters"}


async def test_duplicate_account_aborts(
    bright_env, hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """The same Bright account cannot be added twice."""
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(CLIENT_PATH, return_value=_mock_client()):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_updates_password(
    bright_env, hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Reauth replaces the stored password without creating a second entry."""
    config_entry.add_to_hass(hass)

    result = await config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    with (
        patch(CLIENT_PATH, return_value=_mock_client()),
        patch("custom_components.bright_meter.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "new-password"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_PASSWORD] == "new-password"
    assert config_entry.data[CONF_USERNAME] == USERNAME


async def test_reauth_rejects_bad_password(
    bright_env, hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """A still-wrong password keeps the form open."""
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)

    with patch(
        CLIENT_PATH, return_value=_mock_client(login_error=GlowmarktAuthError("nope"))
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "still-wrong"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert config_entry.data[CONF_PASSWORD] == PASSWORD


async def test_reauth_on_a_loaded_entry_uses_no_update_listener(
    bright_env, hass: HomeAssistant, config_entry: MockConfigEntry, caplog
) -> None:
    """Reauth reloads the entry itself, so no update listener may also do it.

    The pair was deprecated in HA 2026.6 and stops working in 2026.12: the
    options reload now comes from OptionsFlowWithReload instead.
    """
    from .const import FakeGlowmarktClient

    config_entry.add_to_hass(hass)
    with patch(
        "custom_components.bright_meter.GlowmarktClient",
        return_value=FakeGlowmarktClient(),
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
        await config_entry.runtime_data.async_wait_for_backfill()

        assert not config_entry.update_listeners

        result = await config_entry.start_reauth_flow(hass)
        with patch(CLIENT_PATH, return_value=_mock_client()):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONF_PASSWORD: "new-password"}
            )
        await hass.async_block_till_done()
        await config_entry.runtime_data.async_wait_for_backfill()
        await hass.async_block_till_done()

    assert result["reason"] == "reauth_successful"
    assert "update listener" not in caplog.text
