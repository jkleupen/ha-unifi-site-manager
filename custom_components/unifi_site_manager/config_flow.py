"""Config flow for UniFi Site Manager Monitoring."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_API_KEY
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SiteManagerClient, UnifiAuthError, UnifiSiteManagerError
from .const import (
    CONF_CONFIRM_POLLS,
    CONF_DEVICE_ENTITIES,
    CONF_IGNORE_PREFIXES,
    CONF_OUTAGE_DEVICES,
    CONF_OUTAGE_MINUTES,
    CONF_SCAN_INTERVAL,
    CONF_STALE_UPDATE_DAYS,
    DEFAULT_CONFIRM_POLLS,
    DEFAULT_DEVICE_ENTITIES,
    DEFAULT_IGNORE_PREFIXES,
    DEFAULT_OUTAGE_DEVICES,
    DEFAULT_OUTAGE_MINUTES,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_STALE_UPDATE_DAYS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

API_KEY_SCHEMA = vol.Schema({vol.Required(CONF_API_KEY): str})


class UnifiSiteManagerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    VERSION = 1

    async def _validate(self, api_key: str) -> str | None:
        """Return an error key, or None if the key works."""
        client = SiteManagerClient(async_get_clientsession(self.hass), api_key.strip())
        try:
            await client.validate()
        except UnifiAuthError:
            return "invalid_auth"
        except UnifiSiteManagerError:
            return "cannot_connect"
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error validating the API key")
            return "unknown"
        return None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the API key."""
        errors: dict[str, str] = {}
        if user_input is not None:
            error = await self._validate(user_input[CONF_API_KEY])
            if error is None:
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="UniFi Site Manager",
                    data={CONF_API_KEY: user_input[CONF_API_KEY].strip()},
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="user", data_schema=API_KEY_SCHEMA, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start re-authentication when the key stops working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new API key."""
        errors: dict[str, str] = {}
        if user_input is not None:
            error = await self._validate(user_input[CONF_API_KEY])
            if error is None:
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(),
                    data_updates={CONF_API_KEY: user_input[CONF_API_KEY].strip()},
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=API_KEY_SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return UnifiOptionsFlow()


class UnifiOptionsFlow(OptionsFlow):
    """Options: polling interval, ignore list and alert thresholds."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        opts = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(vol.Coerce(int), vol.Range(min=5, max=1440)),
                vol.Optional(
                    CONF_IGNORE_PREFIXES,
                    default=opts.get(CONF_IGNORE_PREFIXES, DEFAULT_IGNORE_PREFIXES),
                ): str,
                vol.Required(
                    CONF_CONFIRM_POLLS,
                    default=opts.get(CONF_CONFIRM_POLLS, DEFAULT_CONFIRM_POLLS),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=10)),
                vol.Required(
                    CONF_OUTAGE_DEVICES,
                    default=opts.get(CONF_OUTAGE_DEVICES, DEFAULT_OUTAGE_DEVICES),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=100)),
                vol.Required(
                    CONF_OUTAGE_MINUTES,
                    default=opts.get(CONF_OUTAGE_MINUTES, DEFAULT_OUTAGE_MINUTES),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=1440)),
                vol.Required(
                    CONF_STALE_UPDATE_DAYS,
                    default=opts.get(CONF_STALE_UPDATE_DAYS, DEFAULT_STALE_UPDATE_DAYS),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=365)),
                vol.Required(
                    CONF_DEVICE_ENTITIES,
                    default=opts.get(CONF_DEVICE_ENTITIES, DEFAULT_DEVICE_ENTITIES),
                ): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
