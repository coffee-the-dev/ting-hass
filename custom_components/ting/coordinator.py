"""Realtime coordinator for Ting."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import TingApi, TingDevice, extract_device_payloads
from .auth import TingAuth
from .signalr import TingSignalRClient

_LOGGER = logging.getLogger(__name__)


class TingRealtimeCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Push coordinator for one Ting device."""

    def __init__(self, hass: HomeAssistant, auth: TingAuth, device: TingDevice) -> None:
        super().__init__(hass, _LOGGER, name=f"Ting {device.serial_number}")
        self.device = device
        self._client = TingSignalRClient(
            async_get_clientsession(hass),
            auth,
            station_id=device.serial_number,
            callback=self._async_handle_update,
        )
        self.async_set_updated_data({})

    async def async_start(self) -> None:
        """Start the realtime stream."""
        await self._client.async_run()

    async def async_stop(self) -> None:
        """Stop the realtime stream."""
        await self._client.async_stop()

    async def _async_handle_update(self, data: dict[str, Any]) -> None:
        self.async_set_updated_data(data)


class TingProfileCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Polling coordinator for low-rate Ting profile diagnostics."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: TingApi,
        initial_user_data: dict[str, Any],
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="Ting profile",
            update_interval=timedelta(minutes=5),
        )
        self._api = api
        self.async_set_updated_data(extract_device_payloads(initial_user_data))

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        return extract_device_payloads(await self._api.async_get_user())
