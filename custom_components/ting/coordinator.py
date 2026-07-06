"""Realtime coordinator for Ting."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

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
            auth,
            station_id=device.serial_number,
            callback=self._async_handle_update,
            stale_callback=self._async_handle_stale,
        )
        # No data yet: leave entities unavailable until the stream delivers.
        self.last_update_success = False

    async def async_start(self) -> None:
        """Start the realtime stream."""
        await self._client.async_run()

    async def async_stop(self) -> None:
        """Stop the realtime stream."""
        await self._client.async_stop()

    async def _async_handle_update(self, data: dict[str, Any]) -> None:
        self.async_set_updated_data(data)

    async def _async_handle_stale(self, err: Exception) -> None:
        """Mark entities unavailable while the stream is down.

        Without this, entities hold their last value indefinitely during an
        outage and history renders a fake flat line instead of a gap.
        """
        self.async_set_update_error(UpdateFailed(str(err)))


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
