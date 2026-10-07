"""Config and options flow for the Bright smart meter integration.

Setup is guided, one page at a time:

1. **Sign in** with the Bright app's email and password.
2. **Your meters**: every electricity and gas stream found, and how far its
   data reaches, so a meter with nothing yet is visible before anything is
   imported. With more than one site, choose which to bring in.
3. **History**: how many days to import on the first pass.
4. **Energy dashboard**: the exact statistics to choose in each field of the
   dashboard, and the sources it already has (an Octopus integration, say)
   that would double-count the same meters if left in place.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
from homeassistant.util import dt as dt_util

from .api import GlowmarktAuthError, GlowmarktClient, GlowmarktConnectionError
from .const import (
    CONF_BACKFILL_DAYS,
    CONF_SITES,
    DEFAULT_BACKFILL_DAYS,
    DOMAIN,
    MAX_BACKFILL_DAYS,
    SUPPORTED_CLASSIFIERS,
    ClassifierInfo,
)
from .statistic_ids import async_assign_statistic_ids

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

# Where each stream goes in Home Assistant's Energy dashboard.
DASHBOARD_FIELD = {
    "electricity.consumption": "Electricity grid, *Grid consumption*",
    "electricity.consumption.cost": (
        "Electricity grid, *Use an entity tracking the total costs*"
    ),
    "electricity.export": "Electricity grid, *Return to grid*",
    "gas.consumption": "Gas, *Gas consumption*",
    "gas.consumption.cost": "Gas, *Use an entity tracking the total costs*",
}


@dataclass(frozen=True, kw_only=True)
class _Stream:
    resource_id: str
    classifier: str
    info: ClassifierInfo
    last_time: datetime | None


@dataclass(frozen=True, kw_only=True)
class _Site:
    ve_id: str
    name: str
    streams: list[_Stream]


async def _async_discover(client: GlowmarktClient) -> list[_Site]:
    """Sign in, then list each site's supported streams and how far they reach."""
    await client.async_login()
    sites: list[_Site] = []
    for entity in await client.async_get_virtual_entities():
        ve_id = entity.get("veId")
        if not ve_id:
            continue
        streams: list[_Stream] = []
        for resource in await client.async_get_resources(ve_id):
            classifier = resource.get("classifier") or ""
            info = SUPPORTED_CLASSIFIERS.get(classifier)
            resource_id = resource.get("resourceId")
            if info is None or not resource_id or resource.get("active") is False:
                continue
            streams.append(
                _Stream(
                    resource_id=resource_id,
                    classifier=classifier,
                    info=info,
                    last_time=await client.async_get_last_time(resource_id),
                )
            )
        if streams:
            sites.append(
                _Site(ve_id=ve_id, name=entity.get("name") or "Bright", streams=streams)
            )
    return sites


def _reach(last_time: datetime | None) -> str:
    """How far a stream's data reaches, in local time, for a person to read."""
    if last_time is None:
        return "no readings yet (normal for a day or two after setting up Bright)"
    until = dt_util.as_local(last_time + timedelta(minutes=30))
    return f"data until {until.strftime('%Y-%m-%d %H:%M')}"


def _meters_text(sites: list[_Site]) -> str:
    lines: list[str] = []
    for site in sites:
        lines.append(f"**{site.name}**")
        lines += [
            f"- {stream.info.label}: {_reach(stream.last_time)}"
            for stream in site.streams
            if not stream.info.is_cost
        ]
        lines.append("")
    return "\n".join(lines).strip()


def _statistics_text(sites: list[_Site], ids: dict[str, str]) -> str:
    lines: list[str] = []
    for site in sites:
        if len(sites) > 1:
            lines.append(f"**{site.name}**")
        lines += [
            f"- {DASHBOARD_FIELD[stream.classifier]}: `{ids[stream.resource_id]}`"
            for stream in site.streams
        ]
    return "\n".join(lines)


def _collect_statistics(node: Any, into: list[str]) -> None:
    """Every statistic id named by a dashboard source, whatever its shape."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key.startswith("stat_energy_") and isinstance(value, str):
                into.append(value)
            else:
                _collect_statistics(value, into)
    elif isinstance(node, list):
        for value in node:
            _collect_statistics(value, into)


async def _async_existing_sources(hass: HomeAssistant) -> list[str]:
    """Statistics the Energy dashboard already uses that are not ours.

    Read-only, and best effort: the dashboard's storage belongs to Home
    Assistant, so anything unexpected means no warning rather than a failed
    setup.
    """
    try:
        from homeassistant.components.energy.data import async_get_manager

        manager = await async_get_manager(hass)
        sources = (manager.data or {}).get("energy_sources", [])
    except Exception:
        _LOGGER.debug("Could not read the Energy dashboard sources", exc_info=True)
        return []
    found: list[str] = []
    _collect_statistics(sources, found)
    return sorted({sid for sid in found if not sid.startswith(f"{DOMAIN}:")})


def _existing_text(existing: list[str]) -> str:
    if not existing:
        return ""
    named = ", ".join(f"`{sid}`" for sid in existing)
    return (
        f"Your Energy dashboard already uses {named}. Remove the ones that "
        "measure these same meters before adding the statistics above, or the "
        "energy is counted twice."
    )


async def _async_validate(hass: HomeAssistant, username: str, password: str) -> None:
    """Check the credentials work (reauthentication only)."""
    client = GlowmarktClient(async_get_clientsession(hass), username, password)
    await client.async_login()


class GlowmarktConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the guided setup and reauthentication."""

    VERSION = 1

    def __init__(self) -> None:
        """Start an empty flow."""
        self._credentials: dict[str, Any] = {}
        self._sites: list[_Site] = []
        self._chosen: list[_Site] = []
        self._backfill_days = DEFAULT_BACKFILL_DAYS

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Page 1: the Bright app's email and password."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Autofill and pasting add spaces; an email never contains one. The
            # password is left exactly as typed.
            username = user_input[CONF_USERNAME].strip()
            user_input = {**user_input, CONF_USERNAME: username}
            await self.async_set_unique_id(username.lower())
            self._abort_if_unique_id_configured()
            client = GlowmarktClient(
                async_get_clientsession(self.hass),
                username,
                user_input[CONF_PASSWORD],
            )
            try:
                sites = await _async_discover(client)
            except GlowmarktAuthError:
                errors["base"] = "invalid_auth"
            except GlowmarktConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error validating Glowmarkt credentials")
                errors["base"] = "unknown"
            else:
                if not sites:
                    errors["base"] = "no_meters"
                else:
                    self._credentials = dict(user_input)
                    self._sites = sites
                    return await self.async_step_meters()

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_meters(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Page 2: what was found, how far it reaches, and which sites to take."""
        errors: dict[str, str] = {}
        if user_input is not None:
            chosen_ids = user_input.get(
                CONF_SITES, [site.ve_id for site in self._sites]
            )
            chosen = [site for site in self._sites if site.ve_id in chosen_ids]
            if chosen:
                self._chosen = chosen
                return await self.async_step_history()
            errors["base"] = "no_site_selected"

        fields: dict[Any, Any] = {}
        if len(self._sites) > 1:
            fields[
                vol.Required(CONF_SITES, default=[site.ve_id for site in self._sites])
            ] = SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value=site.ve_id, label=site.name)
                        for site in self._sites
                    ],
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                )
            )
        return self.async_show_form(
            step_id="meters",
            data_schema=vol.Schema(fields),
            description_placeholders={"meters": _meters_text(self._sites)},
            errors=errors,
        )

    async def async_step_history(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Page 3: how many days to import on the first pass."""
        if user_input is not None:
            self._backfill_days = int(user_input[CONF_BACKFILL_DAYS])
            return await self.async_step_energy()

        return self.async_show_form(
            step_id="history",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_BACKFILL_DAYS, default=DEFAULT_BACKFILL_DAYS
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=1, max=MAX_BACKFILL_DAYS, mode=NumberSelectorMode.BOX
                        )
                    )
                }
            ),
        )

    async def async_step_energy(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Page 4: which statistic goes in which Energy dashboard field."""
        if user_input is not None:
            return self.async_create_entry(
                title=self._credentials[CONF_USERNAME],
                data={
                    **self._credentials,
                    CONF_SITES: [site.ve_id for site in self._chosen],
                },
                options={CONF_BACKFILL_DAYS: self._backfill_days},
            )

        # The ids the import will use. Assigning them here is what makes the
        # page exact: they are stored, so the import cannot pick different ones.
        ids = await async_assign_statistic_ids(
            self.hass,
            [
                (stream.resource_id, stream.classifier)
                for site in self._chosen
                for stream in site.streams
            ],
        )
        return self.async_show_form(
            step_id="energy",
            data_schema=vol.Schema({}),
            description_placeholders={
                "statistics": _statistics_text(self._chosen, ids),
                "existing": _existing_text(await _async_existing_sources(self.hass)),
            },
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication after the credentials stop working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect a fresh password for the existing account."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()

        if user_input is not None:
            username = reauth_entry.data[CONF_USERNAME]
            try:
                await _async_validate(self.hass, username, user_input[CONF_PASSWORD])
            except GlowmarktAuthError:
                errors["base"] = "invalid_auth"
            except GlowmarktConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during Glowmarkt reauth")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            description_placeholders={
                CONF_USERNAME: reauth_entry.data[CONF_USERNAME]
            },
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlowWithReload:
        """Return the options flow."""
        return GlowmarktOptionsFlow()


class GlowmarktOptionsFlow(OptionsFlowWithReload):
    """Lets the history depth be changed after setup; saving reloads the entry."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(
                data={CONF_BACKFILL_DAYS: int(user_input[CONF_BACKFILL_DAYS])}
            )

        current = self.config_entry.options.get(
            CONF_BACKFILL_DAYS, DEFAULT_BACKFILL_DAYS
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_BACKFILL_DAYS, default=current): NumberSelector(
                    NumberSelectorConfig(
                        min=1, max=MAX_BACKFILL_DAYS, mode=NumberSelectorMode.BOX
                    )
                )
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
