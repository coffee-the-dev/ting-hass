"""Sensors for Ting."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfElectricPotential
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import TingRuntimeData
from .api import TingDevice
from .const import DOMAIN
from .coordinator import TingProfileCoordinator, TingRealtimeCoordinator


@dataclass(frozen=True, kw_only=True)
class TingSensorEntityDescription(SensorEntityDescription):
    """Ting sensor description."""

    value_fn: Callable[[dict[str, Any]], Any]


REALTIME_SENSORS: tuple[TingSensorEntityDescription, ...] = (
    TingSensorEntityDescription(
        key="voltage",
        translation_key="voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda data: data.get("voltage"),
    ),
    TingSensorEntityDescription(
        key="hifi",
        translation_key="hifi",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda data: data.get("hifi"),
    ),
    TingSensorEntityDescription(
        key="voltage_high",
        translation_key="voltage_high",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda data: data.get("voltage_high"),
    ),
    TingSensorEntityDescription(
        key="voltage_low",
        translation_key="voltage_low",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda data: data.get("voltage_low"),
    ),
    TingSensorEntityDescription(
        key="last_update",
        translation_key="last_update",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: _parse_timestamp(data.get("last_update")),
    ),
)

PROFILE_SENSORS: tuple[TingSensorEntityDescription, ...] = (
    TingSensorEntityDescription(
        key="hazard_message",
        translation_key="hazard_message",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.get("hazard_message"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Ting sensors."""
    runtime: TingRuntimeData = hass.data[DOMAIN][entry.entry_id]
    entities: list[TingSensor] = []
    for device in runtime.devices:
        coordinator = runtime.coordinators[device.serial_number]
        entities.extend(TingRealtimeSensor(coordinator, description) for description in REALTIME_SENSORS)
        entities.extend(
            TingProfileSensor(runtime.profile_coordinator, device, description)
            for description in PROFILE_SENSORS
        )
    async_add_entities(entities)


class TingSensor(SensorEntity):
    """Base Ting sensor."""

    entity_description: TingSensorEntityDescription
    _attr_has_entity_name = True


class TingRealtimeSensor(CoordinatorEntity[TingRealtimeCoordinator], TingSensor):
    """A Ting realtime websocket sensor."""

    def __init__(
        self,
        coordinator: TingRealtimeCoordinator,
        description: TingSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.device.serial_number}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.device.serial_number)},
            manufacturer="Whisker Labs",
            name=coordinator.device.name,
            model=coordinator.device.model or "Ting",
            sw_version=coordinator.device.firmware,
            suggested_area=coordinator.device.site_name,
        )

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.last_update_success and self.coordinator.data is not None

    @property
    def native_value(self) -> Any:
        """Return the current sensor value."""
        return self.entity_description.value_fn(self.coordinator.data or {})

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return debug-friendly raw attributes on the primary voltage sensor."""
        if self.entity_description.key != "voltage":
            return None
        raw = (self.coordinator.data or {}).get("raw")
        if not isinstance(raw, dict):
            return None
        return {
            "station_id": self.coordinator.device.serial_number,
            "data_time_utc": raw.get("DataTimeUtc"),
        }


class TingProfileSensor(CoordinatorEntity[TingProfileCoordinator], TingSensor):
    """A Ting low-rate profile diagnostic sensor."""

    def __init__(
        self,
        coordinator: TingProfileCoordinator,
        device: TingDevice,
        description: TingSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self._device = device
        self.entity_description = description
        self._attr_unique_id = f"{device.serial_number}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.serial_number)},
            manufacturer="Whisker Labs",
            name=device.name,
            model=device.model or "Ting",
            sw_version=device.firmware,
            suggested_area=device.site_name,
        )

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        data = (self.coordinator.data or {}).get(self._device.serial_number)
        return (
            self.coordinator.last_update_success
            and isinstance(data, dict)
            and isinstance(data.get(self.entity_description.key), str)
        )

    @property
    def native_value(self) -> Any:
        """Return the current sensor value."""
        return self.entity_description.value_fn(
            (self.coordinator.data or {}).get(self._device.serial_number, {})
        )


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed
