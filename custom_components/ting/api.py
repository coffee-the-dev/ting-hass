"""Ting cloud REST API helpers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import json
import logging
from typing import Any

from aiohttp import ClientError

from .auth import TingAuth
from .const import TING_API_BASE
from .exceptions import TingConnectionError, TingResponseError

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class TingDevice:
    """A Ting device discovered from the user profile."""

    serial_number: str
    name: str
    model: str | None = None
    firmware: str | None = None
    site_name: str | None = None


class TingApi:
    """Small wrapper around Ting's REST API."""

    def __init__(self, auth: TingAuth) -> None:
        self._auth = auth

    async def async_get_user(self) -> dict[str, Any]:
        """Fetch the Ting user profile and devices."""
        await self._auth.async_ensure_tokens()
        url = f"{TING_API_BASE}/api/v1/Users/{self._auth.user_id}"
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._auth.id_token}",
            "x-wl-api-key": self._auth.api_key,
        }
        try:
            async with self._auth.session.get(url, headers=headers) as response:
                text = await response.text()
        except ClientError as err:
            raise TingConnectionError("Could not connect to Ting API") from err

        if response.status >= 400:
            _LOGGER.debug("Ting API error %s: %s", response.status, text)
            raise TingResponseError(f"Ting API returned HTTP {response.status}")

        try:
            data = json.loads(text)
        except json.JSONDecodeError as err:
            raise TingResponseError("Ting API returned a non-JSON response") from err
        if not isinstance(data, dict):
            raise TingResponseError("Ting user response was not an object")
        return data


def extract_devices(user_data: Mapping[str, Any], default_serial: str | None = None) -> list[TingDevice]:
    """Extract devices from Ting's profile response.

    The mobile app has used several nested shapes over time. This intentionally
    walks the response and accepts any object carrying a serial number.
    """
    found: dict[str, TingDevice] = {}

    for item, parents in _walk_dicts(user_data):
        serial = _first_str(
            item,
            "serialNumber",
            "SerialNumber",
            "serial_number",
            "stationId",
            "StationId",
        )
        if not serial:
            continue

        name = (
            _first_str(item, "name", "Name", "displayName", "DisplayName", "nickname", "Nickname")
            or _site_name(parents)
            or f"Ting {serial}"
        )
        model = _first_str(item, "type", "Type", "deviceType", "DeviceType", "model", "Model")
        firmware = _first_str(item, "version", "Version", "firmware", "Firmware", "firmwareVersion")
        found[serial] = TingDevice(
            serial_number=serial,
            name=name,
            model=model,
            firmware=firmware,
            site_name=_site_name(parents),
        )

    if default_serial and default_serial not in found:
        found[default_serial] = TingDevice(
            serial_number=default_serial,
            name=f"Ting {default_serial}",
        )

    return list(found.values())


def extract_device_diagnostics(user_data: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Return normalized, non-personal diagnostics keyed by device serial."""
    sites: dict[str, bool] = {}
    for item, _parents in _walk_dicts(user_data):
        site_id = _identifier(item.get("id"))
        power_quality_hazard = item.get("isPowerQualityHazard")
        if site_id is not None and isinstance(power_quality_hazard, bool):
            sites[site_id] = power_quality_hazard

    diagnostics: dict[str, dict[str, Any]] = {}
    for item, _parents in _walk_dicts(user_data):
        serial = _first_str(
            item,
            "serialNumber",
            "SerialNumber",
            "serial_number",
            "stationId",
            "StationId",
        )
        if not serial:
            continue

        device_diagnostics = diagnostics.setdefault(serial, {})

        fire_hazard = item.get("isFire")
        if isinstance(fire_hazard, bool):
            device_diagnostics["fire_hazard"] = fire_hazard

        fire_hazard_status = item.get("fireHazardStatus")
        if isinstance(fire_hazard_status, Mapping):
            learning_mode = fire_hazard_status.get("learningMode")
            if isinstance(learning_mode, bool):
                device_diagnostics["learning_mode"] = learning_mode

            hazard_message = fire_hazard_status.get("message")
            if isinstance(hazard_message, str):
                device_diagnostics["hazard_message"] = hazard_message

        site_id = _identifier(item.get("siteId"))
        if site_id is not None and site_id in sites:
            device_diagnostics["power_quality_hazard"] = sites[site_id]

    return diagnostics


def _walk_dicts(value: Any, parents: tuple[Mapping[str, Any], ...] = ()) -> Iterable[tuple[Mapping[str, Any], tuple[Mapping[str, Any], ...]]]:
    if isinstance(value, Mapping):
        yield value, parents
        next_parents = (*parents, value)
        for child in value.values():
            yield from _walk_dicts(child, next_parents)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child, parents)


def _first_str(item: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
    return None


def _identifier(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return None


def _site_name(parents: tuple[Mapping[str, Any], ...]) -> str | None:
    for parent in reversed(parents):
        value = _first_str(parent, "siteName", "SiteName", "locationName", "LocationName", "address1")
        if value:
            return value
    return None
