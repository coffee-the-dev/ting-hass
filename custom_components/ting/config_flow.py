"""Config flow for Ting."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TingApi, extract_devices
from .auth import TingAuth
from .const import (
    CONF_REFRESH_TOKEN,
    CONF_POLL_INTERVAL,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    MIN_POLL_INTERVAL,
    MAX_POLL_INTERVAL,
)
from .exceptions import TingAuthError, TingConnectionError, TingResponseError

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Optional(
            CONF_POLL_INTERVAL,
            default=DEFAULT_POLL_INTERVAL,
            description={"suggested_value": DEFAULT_POLL_INTERVAL},
        ): vol.All(vol.Coerce(float), vol.Range(min=MIN_POLL_INTERVAL, max=MAX_POLL_INTERVAL)),
    }
)


async def _validate_input(hass: HomeAssistant, user_input: dict[str, str]) -> dict[str, Any]:
    """Validate credentials and return entry data."""
    auth = TingAuth(async_get_clientsession(hass))
    await auth.async_login(user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
    api = TingApi(auth)
    user_data = await api.async_get_user()
    devices = extract_devices(user_data, auth.default_device)
    return {
        CONF_USERNAME: user_input[CONF_USERNAME],
        CONF_REFRESH_TOKEN: auth.refresh_token,
        "user_id": auth.user_id,
        "default_device": auth.default_device,
        "device_count": len(devices),
    }


class TingConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a Ting config flow."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        """Return the options flow."""
        return TingOptionsFlow(config_entry)

    async def async_step_user(self, user_input: dict[str, str] | None = None) -> config_entries.FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                data = await _validate_input(self.hass, user_input)
            except TingAuthError as err:
                _LOGGER.warning("Ting authentication failed during setup: %s", err)
                errors["base"] = "invalid_auth"
            except TingConnectionError:
                errors["base"] = "cannot_connect"
            except TingResponseError:
                _LOGGER.exception("Unexpected Ting response during setup")
                errors["base"] = "unknown"
            except Exception:
                _LOGGER.exception("Unexpected error during Ting setup")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(str(data["user_id"]))
                self._abort_if_unique_id_configured()
                title = user_input[CONF_USERNAME]
                return self.async_create_entry(title=title, data=data)

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> config_entries.FlowResult:
        """Handle an expired refresh token."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self,
        user_input: dict[str, str] | None = None,
    ) -> config_entries.FlowResult:
        """Ask the user to sign in again."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                data = await _validate_input(self.hass, user_input)
            except TingAuthError as err:
                _LOGGER.warning("Ting authentication failed during reauth: %s", err)
                errors["base"] = "invalid_auth"
            except TingConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during Ting reauth")
                errors["base"] = "unknown"
            else:
                entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
                if entry is not None:
                    if entry.unique_id is not None and entry.unique_id != str(data["user_id"]):
                        errors["base"] = "invalid_auth"
                    else:
                        self.hass.config_entries.async_update_entry(entry, data=data)
                        await self.hass.config_entries.async_reload(entry.entry_id)
                        return self.async_abort(reason="reauth_successful")
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )


class TingOptionsFlow(config_entries.OptionsFlow):
    """Options flow for Ting."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}

        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                OPTIONS_SCHEMA, self.config_entry.options
            ),
            errors=errors,
        )