"""Number platform for IthoWiFi integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import IthoDeviceInfoCoordinator, IthoStatusCoordinator
from .entity import IthoEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up IthoWiFi number entities for fan demand."""
    data = hass.data[DOMAIN][entry.entry_id]
    status_coord: IthoStatusCoordinator = data["status_coordinator"]
    device_coord: IthoDeviceInfoCoordinator = data["device_coordinator"]

    entities: list[NumberEntity] = [
        IthoFanDemandNumber(status_coord, device_coord),
    ]

    async_add_entities(entities)


class IthoFanDemandNumber(IthoEntity, NumberEntity):
    """Number entity for setting fan demand percentage."""

    _attr_name = "Fan demand"
    _attr_icon = "mdi:fan"
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "%"
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self,
        coordinator: IthoStatusCoordinator,
        device_info_coordinator: IthoDeviceInfoCoordinator,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator, device_info_coordinator)
        info = device_info_coordinator.data or {}
        self._attr_unique_id = (
            f"{info.get('add-on_hwid', 'itho')}_fan_demand"
        )

    @property
    def native_value(self) -> float | None:
        """Return the current fan speed as percentage."""
        if self.coordinator.data is None:
            return None
        # Prefer Speed status from ithostatus (works when PWM2I2C is off)
        status = self.coordinator.data.get("status", {})
        val = status.get("Speed status")
        if val is not None and val != "not available":
            return min(round(float(val)), 100)
        # Fall back to currentspeed
        speed = self.coordinator.data.get("speed", {}).get("currentspeed")
        if speed is None:
            return None
        return min(round(speed / 2.55), 100)

    async def async_set_native_value(self, value: float) -> None:
        """Set the fan demand."""
        if self.coordinator.use_rf_commands:
            await self.coordinator.api.send_rf_command("auto")
            demand = int(value * 2)  # 0-100% → 0-200 demand
            await self.coordinator.api.send_rf_demand(demand)
        else:
            import math
            speed = math.ceil(value * 2.55)
            await self.coordinator.api.set_speed(speed)
        await self.coordinator.async_request_refresh()
