"""Binary sensors for Ting."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import TingRuntimeData
from .api import TingDevice
from .const import DOMAIN
from .coordinator import TingProfileCoordinator


@dataclass(frozen=True, kw_only=True)
class TingBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Ting binary sensor description."""


PROFILE_BINARY_SENSORS: tuple[TingBinarySensorEntityDescription, ...] = (
    TingBinarySensorEntityDescription(
        key="fire_hazard",
        translation_key="fire_hazard",
        device_class=BinarySensorDeviceClass.SAFETY,
    ),
    TingBinarySensorEntityDescription(
        key="power_quality_hazard",
        translation_key="power_quality_hazard",
        device_class=BinarySensorDeviceClass.SAFETY,
    ),
    TingBinarySensorEntityDescription(
        key="learning_mode",
        translation_key="learning_mode",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Ting binary sensors."""
    runtime: TingRuntimeData = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        TingProfileBinarySensor(runtime.profile_coordinator, device, description)
        for device in runtime.devices
        for description in PROFILE_BINARY_SENSORS
    )


class TingProfileBinarySensor(
    CoordinatorEntity[TingProfileCoordinator], BinarySensorEntity
):
    """A Ting low-rate profile binary sensor."""

    entity_description: TingBinarySensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: TingProfileCoordinator,
        device: TingDevice,
        description: TingBinarySensorEntityDescription,
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
        value = self._device_data.get(self.entity_description.key)
        return self.coordinator.last_update_success and isinstance(value, bool)

    @property
    def is_on(self) -> bool | None:
        """Return the current binary sensor state."""
        value = self._device_data.get(self.entity_description.key)
        return value if isinstance(value, bool) else None

    @property
    def _device_data(self) -> dict[str, Any]:
        data = (self.coordinator.data or {}).get(self._device.serial_number)
        return data if isinstance(data, dict) else {}
