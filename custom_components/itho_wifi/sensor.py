"""Sensor platform for IthoWiFi integration."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    REVOLUTIONS_PER_MINUTE,
    EntityCategory,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_DIAGNOSTICS, CONF_SENSORS, DOMAIN
from .coordinator import IthoDeviceInfoCoordinator, IthoStatusCoordinator
from .entity import IthoEntity

_LOGGER = logging.getLogger(__name__)

# Sensor descriptions for known status keys.
# Sensors are created dynamically based on what keys the device actually reports.
KNOWN_SENSORS: dict[str, SensorEntityDescription] = {
    "temp": SensorEntityDescription(
        key="temp",
        translation_key="temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    "hum": SensorEntityDescription(
        key="hum",
        translation_key="humidity",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    "ppmw": SensorEntityDescription(
        key="ppmw",
        translation_key="ppmw",
        native_unit_of_measurement="ppmw",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:water-percent",
    ),
    "Speed status": SensorEntityDescription(
        key="Speed status",
        translation_key="speed_status",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    "ExhFanSpeed (%)": SensorEntityDescription(
        key="ExhFanSpeed (%)",
        translation_key="exhaust_fan_speed",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:fan",
    ),
    "InFanSpeed (%)": SensorEntityDescription(
        key="InFanSpeed (%)",
        translation_key="inlet_fan_speed",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:fan",
    ),
    "Fan speed (rpm)": SensorEntityDescription(
        key="Fan speed (rpm)",
        translation_key="fan_speed_rpm",
        native_unit_of_measurement=REVOLUTIONS_PER_MINUTE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:fan",
    ),
    "Fan setpoint (rpm)": SensorEntityDescription(
        key="Fan setpoint (rpm)",
        translation_key="fan_setpoint_rpm",
        native_unit_of_measurement=REVOLUTIONS_PER_MINUTE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:fan",
    ),
    "Ventilation setpoint (%)": SensorEntityDescription(
        key="Ventilation setpoint (%)",
        translation_key="ventilation_setpoint",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:fan",
    ),
    "RemainingTime (min)": SensorEntityDescription(
        key="RemainingTime (min)",
        translation_key="remaining_time",
        native_unit_of_measurement="min",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:timer-outline",
    ),
    "CO2level (ppm)": SensorEntityDescription(
        key="CO2level (ppm)",
        translation_key="co2_level",
        native_unit_of_measurement="ppm",
        device_class=SensorDeviceClass.CO2,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    "Indoorhumidity (%)": SensorEntityDescription(
        key="Indoorhumidity (%)",
        translation_key="indoor_humidity",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    "IndoorTemp (°C)": SensorEntityDescription(
        key="IndoorTemp (°C)",
        translation_key="indoor_temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    "OutdoorTemp (°C)": SensorEntityDescription(
        key="OutdoorTemp (°C)",
        translation_key="outdoor_temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    "Exhausttemp (°C)": SensorEntityDescription(
        key="Exhausttemp (°C)",
        translation_key="exhaust_temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    "SupplyTemp (°C)": SensorEntityDescription(
        key="SupplyTemp (°C)",
        translation_key="supply_temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    "BypassPos (%)": SensorEntityDescription(
        key="BypassPos (%)",
        translation_key="bypass_position",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:valve",
    ),
    "FanInfo": SensorEntityDescription(
        key="FanInfo",
        translation_key="fan_info",
        icon="mdi:information-outline",
    ),
    "Filter dirty": SensorEntityDescription(
        key="Filter dirty",
        translation_key="filter_dirty",
        icon="mdi:air-filter",
    ),
    "Internal fault": SensorEntityDescription(
        key="Internal fault",
        translation_key="internal_fault",
        icon="mdi:alert-circle-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    "Error": SensorEntityDescription(
        key="Error",
        translation_key="error_code",
        icon="mdi:alert-circle-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    "Total operation (hours)": SensorEntityDescription(
        key="Total operation (hours)",
        translation_key="total_operation_hours",
        native_unit_of_measurement="h",
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:clock-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    "Startup counter": SensorEntityDescription(
        key="Startup counter",
        translation_key="startup_counter",
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:counter",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    "PostHeat (%)": SensorEntityDescription(
        key="PostHeat (%)",
        translation_key="postheat",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:radiator",
    ),
    "PreHeat (%)": SensorEntityDescription(
        key="PreHeat (%)",
        translation_key="preheat",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:radiator",
    ),
    "InFlow (l sec)": SensorEntityDescription(
        key="InFlow (l sec)",
        translation_key="inflow",
        native_unit_of_measurement="l/s",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:weather-windy",
    ),
    "ExhFlow (l sec)": SensorEntityDescription(
        key="ExhFlow (l sec)",
        translation_key="exhaust_flow",
        native_unit_of_measurement="l/s",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:weather-windy",
    ),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up IthoWiFi sensors based on device status keys."""
    data = hass.data[DOMAIN][entry.entry_id]
    status_coord: IthoStatusCoordinator = data["status_coordinator"]
    device_coord: IthoDeviceInfoCoordinator = data["device_coordinator"]

    entities: list[SensorEntity] = []

    # Get user-selected sensors and diagnostics from options
    selected_sensors = set(entry.options.get(CONF_SENSORS, []))
    selected_diagnostics = set(entry.options.get(CONF_DIAGNOSTICS, []))
    selected_keys = selected_sensors | selected_diagnostics

    # Create sensors only for selected keys
    for key in selected_keys:
        if key in KNOWN_SENSORS:
            entities.append(
                IthoSensor(status_coord, device_coord, KNOWN_SENSORS[key])
            )
        else:
            entities.append(
                IthoSensor(
                    status_coord,
                    device_coord,
                    SensorEntityDescription(
                        key=key,
                        name=key,
                        has_entity_name=True,
                    ),
                )
            )

    # Always add last command sensor
    entities.append(IthoLastCommandSensor(status_coord, device_coord))

    # Device info diagnostic sensors
    entities.append(IthoDeviceInfoSensor(status_coord, device_coord))

    async_add_entities(entities)


class IthoSensor(IthoEntity, SensorEntity):
    """Representation of an Itho status sensor."""

    def __init__(
        self,
        coordinator: IthoStatusCoordinator,
        device_info_coordinator: IthoDeviceInfoCoordinator,
        description: SensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_info_coordinator)
        self.entity_description = description
        info = device_info_coordinator.data or {}
        hw_id = info.get("add-on_hwid", "itho")
        self._attr_unique_id = f"{hw_id}_{description.key}"

    @property
    def native_value(self) -> Any | None:
        """Return the sensor value."""
        if self.coordinator.data is None:
            return None
        status = self.coordinator.data.get("status", {})
        value = status.get(self.entity_description.key)
        if value == "not available":
            return None
        return value


class IthoLastCommandSensor(IthoEntity, SensorEntity):
    """Sensor showing the last executed command."""

    _attr_name = "Last command"
    _attr_icon = "mdi:console"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: IthoStatusCoordinator,
        device_info_coordinator: IthoDeviceInfoCoordinator,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_info_coordinator)
        info = device_info_coordinator.data or {}
        self._attr_unique_id = f"{info.get('add-on_hwid', 'itho')}_last_command"

    @property
    def native_value(self) -> str | None:
        """Return the last command."""
        if self.coordinator.data is None:
            return None
        lastcmd = self.coordinator.data.get("lastcmd", {})
        return lastcmd.get("command")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes."""
        if self.coordinator.data is None:
            return {}
        lastcmd = self.coordinator.data.get("lastcmd", {})
        attrs: dict[str, Any] = {}
        if "source" in lastcmd:
            attrs["source"] = lastcmd["source"]
        if "timestamp" in lastcmd:
            attrs["timestamp"] = datetime.fromtimestamp(
                lastcmd["timestamp"], tz=timezone.utc
            ).isoformat()
        return attrs


class IthoDeviceInfoSensor(IthoEntity, SensorEntity):
    """Diagnostic sensor showing Itho device details."""

    _attr_name = "Itho device"
    _attr_icon = "mdi:information-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: IthoStatusCoordinator,
        device_info_coordinator: IthoDeviceInfoCoordinator,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_info_coordinator)
        info = device_info_coordinator.data or {}
        self._attr_unique_id = f"{info.get('add-on_hwid', 'itho')}_itho_device"

    @property
    def native_value(self) -> str | None:
        """Return the Itho device type."""
        info = self._device_info_coordinator.data or {}
        return info.get("itho_devtype")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return Itho device details."""
        info = self._device_info_coordinator.data or {}
        attrs: dict[str, Any] = {}
        itho_fw = info.get("itho_fwversion")
        itho_hw = info.get("itho_hwversion")
        if itho_fw is not None:
            attrs["firmware"] = f"{itho_fw} (0x{itho_fw:02X})" if isinstance(itho_fw, int) else str(itho_fw)
        if itho_hw is not None:
            attrs["hardware"] = f"{itho_hw} (0x{itho_hw:02X})" if isinstance(itho_hw, int) else str(itho_hw)
        if "itho_deviceid" in info:
            attrs["device_id"] = info["itho_deviceid"]
        return attrs
