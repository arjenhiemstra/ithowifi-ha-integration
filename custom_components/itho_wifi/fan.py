"""Fan platform for IthoWiFi integration."""

from __future__ import annotations

import asyncio
import math
from typing import Any

from homeassistant.components.fan import (
    FanEntity,
    FanEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    PRESET_AUTO,
    PRESET_AUTONIGHT,
    PRESET_AWAY,
    PRESET_HIGH,
    PRESET_LOW,
    PRESET_MEDIUM,
)
from .coordinator import IthoDeviceInfoCoordinator, IthoStatusCoordinator
from .entity import IthoEntity

PRESET_MODES = [
    PRESET_LOW,
    PRESET_MEDIUM,
    PRESET_HIGH,
    PRESET_AUTO,
    PRESET_AUTONIGHT,
    PRESET_AWAY,
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the IthoWiFi fan."""
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [IthoFan(data["status_coordinator"], data["device_coordinator"])]
    )


class IthoFan(IthoEntity, FanEntity):
    """Representation of an Itho ventilation fan."""

    _attr_name = None  # Use device name
    _attr_unique_id_suffix = "fan"
    _attr_supported_features = (
        FanEntityFeature.SET_SPEED
        | FanEntityFeature.PRESET_MODE
        | FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF
    )
    _attr_speed_count = 100
    _attr_preset_modes = PRESET_MODES

    def __init__(
        self,
        coordinator: IthoStatusCoordinator,
        device_info_coordinator: IthoDeviceInfoCoordinator,
    ) -> None:
        """Initialize the fan."""
        super().__init__(coordinator, device_info_coordinator)
        info = device_info_coordinator.data or {}
        self._attr_unique_id = f"{info.get('add-on_hwid', 'itho')}_{self._attr_unique_id_suffix}"

    @property
    def is_on(self) -> bool:
        """Return true if the fan is on."""
        pct = self.percentage
        return pct is not None and pct > 0

    @property
    def percentage(self) -> int | None:
        """Return the current speed percentage."""
        if self.coordinator.data is None:
            return None
        if self.coordinator.rf_standalone:
            status = self.coordinator.data.get("status", {})
            val = status.get("Speed status")
            if val is None or val == "not available":
                return None
            return min(round(float(val)), 100)
        speed = self.coordinator.data.get("speed", {}).get("currentspeed")
        if speed is None:
            return None
        return min(round(speed / 2.55), 100)

    @property
    def _rf_standalone(self) -> bool:
        """Return True if in RF standalone mode."""
        return self.coordinator.rf_standalone

    async def _async_refresh(self) -> None:
        """Refresh after command — delay in RF mode for data to arrive."""
        if self._rf_standalone:
            await asyncio.sleep(5)
        await self.coordinator.async_request_refresh()

    async def async_set_percentage(self, percentage: int) -> None:
        """Set the speed percentage."""
        if self._rf_standalone:
            # RF demand only works in auto mode — send auto first
            await self.coordinator.api.send_rf_command("auto")
            demand = percentage * 2  # 0-100% → 0-200 demand
            await self.coordinator.api.send_rf_demand(demand)
        else:
            speed = math.ceil(percentage * 2.55)
            await self.coordinator.api.set_speed(speed)
        await self._async_refresh()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set the preset mode."""
        if self._rf_standalone:
            await self.coordinator.api.send_rf_command(preset_mode)
        else:
            await self.coordinator.api.send_command(preset_mode)
        await self._async_refresh()

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Turn on the fan."""
        if preset_mode:
            await self.async_set_preset_mode(preset_mode)
        elif percentage is not None:
            await self.async_set_percentage(percentage)
        else:
            if self._rf_standalone:
                await self.coordinator.api.send_rf_command("medium")
            else:
                await self.coordinator.api.send_command("medium")
            await self._async_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the fan."""
        if self._rf_standalone:
            await self.coordinator.api.send_rf_command("low")
        else:
            await self.coordinator.api.set_speed(0)
        await self._async_refresh()
