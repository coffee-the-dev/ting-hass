"""Home Assistant integration for Ting."""

from __future__ import annotations

from dataclasses import dataclass
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TingApi, TingDevice, extract_devices
from .auth import TingAuth
from .const import CONF_REFRESH_TOKEN, DOMAIN, PLATFORMS
from .coordinator import TingProfileCoordinator, TingRealtimeCoordinator
from .exceptions import TingAuthError, TingConnectionError, TingResponseError

_LOGGER = logging.getLogger(__name__)

_DEPRECATED_PROFILE_UNIQUE_ID_SUFFIXES = (
    "_fire_hazard_severity",
    "_electrical_fire_hazard_level",
    "_electrical_fire_hazard_status",
    "_utility_fire_hazard_level",
    "_utility_fire_hazard_status",
)


@dataclass
class TingRuntimeData:
    """Runtime data for one Ting config entry."""

    auth: TingAuth
    api: TingApi
    devices: list[TingDevice]
    profile_coordinator: TingProfileCoordinator
    coordinators: dict[str, TingRealtimeCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Ting from a config entry."""
    session = async_get_clientsession(hass)
    auth = TingAuth(session, refresh_token=entry.data[CONF_REFRESH_TOKEN])
    api = TingApi(auth)

    try:
        await auth.async_ensure_tokens()
        user_data = await api.async_get_user()
    except TingAuthError as err:
        raise ConfigEntryAuthFailed("Ting authentication failed") from err
    except (TingConnectionError, TingResponseError) as err:
        raise ConfigEntryNotReady("Ting API is not ready") from err

    devices = extract_devices(user_data, auth.default_device)
    if not devices:
        raise ConfigEntryNotReady(
            f"Ting account {entry.data.get(CONF_USERNAME)} did not return any devices"
        )

    coordinators = {
        device.serial_number: TingRealtimeCoordinator(hass, entry, auth, device)
        for device in devices
    }
    profile_coordinator = TingProfileCoordinator(hass, api, user_data)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = TingRuntimeData(
        auth=auth,
        api=api,
        devices=devices,
        profile_coordinator=profile_coordinator,
        coordinators=coordinators,
    )

    entity_registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        if entity.unique_id.endswith(_DEPRECATED_PROFILE_UNIQUE_ID_SUFFIXES):
            entity_registry.async_remove(entity.entity_id)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    for serial, coordinator in coordinators.items():
        entry.async_create_background_task(
            hass,
            coordinator.async_start(),
            f"ting_realtime_{serial}",
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Ting config entry."""
    runtime: TingRuntimeData | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if runtime is not None:
        for coordinator in runtime.coordinators.values():
            await coordinator.async_stop()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
