"""Realtime coordinator for Ting."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import TingApi, TingDevice, extract_device_diagnostics
from .auth import TingAuth
from .const import REALTIME_PUBLISH_INTERVAL
from .exceptions import TingAuthError, TingConnectionError, TingResponseError
from .signalr import TingSignalRClient
from .throttle import LatestValueThrottle

_LOGGER = logging.getLogger(__name__)


class TingRealtimeCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Push coordinator for one Ting device."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, auth: TingAuth, device: TingDevice
    ) -> None:
        super().__init__(hass, _LOGGER, name=f"Ting {device.serial_number}")
        self._entry = entry
        self.device = device
        self._client = TingSignalRClient(
            auth,
            station_id=device.serial_number,
            callback=self._async_handle_update,
            stale_callback=self._async_handle_stale,
        )
        self._realtime_throttle = LatestValueThrottle(
            REALTIME_PUBLISH_INTERVAL, self.async_set_updated_data
        )
        # No data yet: leave entities unavailable until the stream delivers.
        self.last_update_success = False

    async def async_start(self) -> None:
        """Start the realtime stream."""
        try:
            await self._client.async_run()
        except TingAuthError as err:
            await self._async_handle_stale(err)
            self._entry.async_start_reauth(self.hass)

    async def async_stop(self) -> None:
        """Stop the realtime stream."""
        try:
            await self._client.async_stop()
        finally:
            self._realtime_throttle.cancel()

    async def _async_handle_update(self, data: dict[str, Any]) -> None:
        self._realtime_throttle.submit(data)

    async def _async_handle_stale(self, err: Exception) -> None:
        """Mark entities unavailable while the stream is down.

        Without this, entities hold their last value indefinitely during an
        outage and history renders a fake flat line instead of a gap.
        """
        self._realtime_throttle.cancel()
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
        self.async_set_updated_data(extract_device_diagnostics(initial_user_data))

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        try:
            return extract_device_diagnostics(await self._api.async_get_user())
        except TingAuthError as err:
            raise ConfigEntryAuthFailed("Ting authentication failed") from err
        except (TingConnectionError, TingResponseError) as err:
            raise UpdateFailed(f"Could not update Ting profile: {err}") from err
