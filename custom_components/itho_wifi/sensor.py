"""Sensor platform for IthoWiFi integration."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import re
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
    UnitOfElectricCurrent,
    UnitOfEnergy,
    UnitOfPressure,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_DIAGNOSTICS, CONF_SENSORS, DOMAIN
from .coordinator import IthoDeviceInfoCoordinator, IthoStatusCoordinator
from .entity import IthoEntity

_LOGGER = logging.getLogger(__name__)


# Map normalized unit strings to sensor metadata.
# Each entry: (native_unit, device_class_or_None, state_class_or_None, icon_or_None)
# Keys are normalized via _normalize_unit() — lowercase, underscores→slashes, no whitespace.
_UNIT_MAP: dict[str, tuple[str | None, SensorDeviceClass | None, SensorStateClass | None, str | None]] = {
    # Temperature
    "°c": (UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT, None),
    "c": (UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT, None),
    # Temperature delta (Kelvin difference, used for hysteresis)
    "k": ("K", None, SensorStateClass.MEASUREMENT, None),
    "k/min": ("K/min", None, SensorStateClass.MEASUREMENT, None),
    # Percentage
    "%": (PERCENTAGE, None, SensorStateClass.MEASUREMENT, None),
    "%rh": (PERCENTAGE, SensorDeviceClass.HUMIDITY, SensorStateClass.MEASUREMENT, None),
    # CO2
    "ppm": ("ppm", SensorDeviceClass.CO2, SensorStateClass.MEASUREMENT, None),
    # Moisture (grams water per kg dry air)
    "ppmw": ("ppmw", None, SensorStateClass.MEASUREMENT, "mdi:water-percent"),
    # Fan speed
    "rpm": (REVOLUTIONS_PER_MINUTE, None, SensorStateClass.MEASUREMENT, "mdi:fan"),
    # Time / duration
    "sec": (UnitOfTime.SECONDS, SensorDeviceClass.DURATION, SensorStateClass.MEASUREMENT, "mdi:timer-outline"),
    "s": (UnitOfTime.SECONDS, SensorDeviceClass.DURATION, SensorStateClass.MEASUREMENT, "mdi:timer-outline"),
    "min": (UnitOfTime.MINUTES, SensorDeviceClass.DURATION, SensorStateClass.MEASUREMENT, "mdi:timer-outline"),
    "h": (UnitOfTime.HOURS, SensorDeviceClass.DURATION, SensorStateClass.MEASUREMENT, "mdi:clock-outline"),
    "hr": (UnitOfTime.HOURS, SensorDeviceClass.DURATION, SensorStateClass.MEASUREMENT, "mdi:clock-outline"),
    "hrs": (UnitOfTime.HOURS, SensorDeviceClass.DURATION, SensorStateClass.MEASUREMENT, "mdi:clock-outline"),
    "hour": (UnitOfTime.HOURS, SensorDeviceClass.DURATION, SensorStateClass.MEASUREMENT, "mdi:clock-outline"),
    "hours": (UnitOfTime.HOURS, SensorDeviceClass.DURATION, SensorStateClass.MEASUREMENT, "mdi:clock-outline"),
    "day": (UnitOfTime.DAYS, SensorDeviceClass.DURATION, SensorStateClass.MEASUREMENT, "mdi:calendar"),
    "days": (UnitOfTime.DAYS, SensorDeviceClass.DURATION, SensorStateClass.MEASUREMENT, "mdi:calendar"),
    # Volume flow rate
    "l/s": (UnitOfVolumeFlowRate.LITERS_PER_SECOND, SensorDeviceClass.VOLUME_FLOW_RATE, SensorStateClass.MEASUREMENT, "mdi:weather-windy"),
    "l/sec": (UnitOfVolumeFlowRate.LITERS_PER_SECOND, SensorDeviceClass.VOLUME_FLOW_RATE, SensorStateClass.MEASUREMENT, "mdi:weather-windy"),
    "l/h": ("L/h", SensorDeviceClass.VOLUME_FLOW_RATE, SensorStateClass.MEASUREMENT, "mdi:weather-windy"),
    "lt/hr": ("L/h", SensorDeviceClass.VOLUME_FLOW_RATE, SensorStateClass.MEASUREMENT, "mdi:weather-windy"),
    "m3/h": (UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR, SensorDeviceClass.VOLUME_FLOW_RATE, SensorStateClass.MEASUREMENT, "mdi:weather-windy"),
    "kg/h": ("kg/h", None, SensorStateClass.MEASUREMENT, "mdi:weather-windy"),
    # Electric current
    "a": (UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT, SensorStateClass.MEASUREMENT, None),
    "ma": (UnitOfElectricCurrent.MILLIAMPERE, SensorDeviceClass.CURRENT, SensorStateClass.MEASUREMENT, None),
    # Energy (cumulative)
    "kwh": (UnitOfEnergy.KILO_WATT_HOUR, SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING, None),
    "wh": (UnitOfEnergy.WATT_HOUR, SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING, None),
    # Pressure
    "bar": (UnitOfPressure.BAR, SensorDeviceClass.PRESSURE, SensorStateClass.MEASUREMENT, None),
    "pa": (UnitOfPressure.PA, SensorDeviceClass.PRESSURE, SensorStateClass.MEASUREMENT, None),
    "kpa": (UnitOfPressure.KPA, SensorDeviceClass.PRESSURE, SensorStateClass.MEASUREMENT, None),
    # Position (valve steps/pulses — no HA unit, keep as-is)
    "steps": ("steps", None, SensorStateClass.MEASUREMENT, None),
    "pulse": ("pulses", None, SensorStateClass.MEASUREMENT, None),
    "pls": ("pulses", None, SensorStateClass.MEASUREMENT, None),
    "puls": ("pulses", None, SensorStateClass.MEASUREMENT, None),
}


def _normalize_unit(unit_raw: str) -> str:
    """Normalize a unit string for matching against _UNIT_MAP.

    Handles Itho's inconsistent unit formatting:
    - "l sec", "l/sec", "l_sec" → "l/sec"
    - "m3/h", "m3_h", "M3/h" → "m3/h"
    - "l_h", "l/h", "Lt/hr" → "l/h"
    - "%RH", "%rh" → "%rh"
    - strips whitespace and lowercases
    """
    u = unit_raw.strip().lower()
    # Replace underscores with slashes for compound units
    u = u.replace("_", "/")
    # Collapse spaces inside compound units (e.g. "l sec" -> "l/sec")
    u = re.sub(r"\s*/\s*", "/", u)
    # Collapse remaining whitespace to a single slash for known space-separated units
    if " " in u:
        parts = u.split()
        if len(parts) == 2 and parts[0] in ("l", "m3", "kg", "lt"):
            u = f"{parts[0]}/{parts[1]}"
        else:
            u = u.replace(" ", "")
    return u


# Track which unrecognized units we've already logged to avoid log spam
_logged_unknown_units: set[str] = set()


def _looks_boolean(value: Any) -> bool:
    """Return True if value looks like a boolean/yes-no state."""
    if isinstance(value, bool):
        return True
    if isinstance(value, str):
        return value.lower() in ("yes", "no", "on", "off", "true", "false", "ok", "nok")
    return False


def _looks_numeric(value: Any) -> bool:
    """Return True if value is a number (or numeric string)."""
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        try:
            float(value)
            return True
        except (ValueError, TypeError):
            return False
    return False


def _keyword_hints(key: str) -> dict[str, Any]:
    """Infer sensor metadata from keywords in the key name."""
    lower = key.lower()
    # Strip unit part for cleaner keyword matching
    lower_nou = re.sub(r"\s*\([^)]*\)\s*$", "", lower)
    hints: dict[str, Any] = {}

    # Diagnostic keywords (error codes, faults, internal state bytes, dirty filters)
    diagnostic_kw = (
        "error", "fault", "alarm", "warning", "byte", "retry", "blocked",
        "spare input", "blockage", "task active", "internal fault", "dirty",
        "fault code",
    )
    is_diagnostic = any(w in lower_nou for w in diagnostic_kw)

    # Counter keywords (monotonically increasing)
    counter_kw = (
        "total operation", "startup counter", "start-up counter", "startupcounter",
        "filter use", "airfilter counter", "air filter counter", "pulse counter",
        "filterusage",
    )
    is_counter = any(w in lower_nou for w in counter_kw)

    # Status/mode/enum keywords (plain text sensors)
    enum_kw = (
        "status", "condition", "selection", "mode", "phase", "faninfo",
        "operating phase", "control mode", "operating mode", "actual mode",
        "selected mode", "measurement method", "speedcap", "sub_status",
    )
    is_enum = any(w in lower_nou for w in enum_kw)

    # Icon selection based on keyword
    if is_enum:
        hints["icon"] = "mdi:information-outline"
    elif "bypass" in lower_nou:
        hints["icon"] = "mdi:valve"
    elif any(w in lower_nou for w in ("fan", "speed", "ventilation")):
        hints["icon"] = "mdi:fan"
    elif "filter" in lower_nou:
        hints["icon"] = "mdi:air-filter"
    elif any(w in lower_nou for w in ("temp", "temperature")):
        hints["icon"] = "mdi:thermometer"
    elif "timer" in lower_nou or "time" in lower_nou:
        hints["icon"] = "mdi:timer-outline"
    elif "pump" in lower_nou:
        hints["icon"] = "mdi:pump"
    elif "valve" in lower_nou:
        hints["icon"] = "mdi:valve"
    elif "humidity" in lower_nou or lower_nou.startswith("rh "):
        hints["icon"] = "mdi:water-percent"
    elif "co2" in lower_nou:
        hints["icon"] = "mdi:molecule-co2"

    if is_diagnostic:
        hints["entity_category"] = EntityCategory.DIAGNOSTIC
        hints["icon"] = "mdi:alert-circle-outline"
    if is_counter:
        hints["state_class"] = SensorStateClass.TOTAL_INCREASING
        hints["icon"] = "mdi:counter"
    elif is_enum:
        # Plain text sensor — no state_class (avoids HA warnings)
        hints["state_class"] = None

    return hints


def _description_from_key(key: str, value: Any = None) -> SensorEntityDescription:
    """Build a SensorEntityDescription by inspecting the key and value.

    Strategy (in order):
    1. Parse and normalize unit from parentheses at end of key (e.g. "Foo (°C)").
    2. Merge keyword hints (counters, diagnostics, enums, icons).
    3. For keys without a unit, fall back to value type inference.
    Unknown units are logged once at WARNING level for future mapping.
    """
    kwargs: dict[str, Any] = {}
    hints = _keyword_hints(key)

    # 1. Unit from parentheses (with normalization)
    match = re.search(r"\(([^)]+)\)\s*$", key)
    unit_raw: str | None = None
    if match:
        unit_raw = match.group(1).strip()
        unit_lookup = _normalize_unit(unit_raw)
        if unit_lookup in _UNIT_MAP:
            native_unit, device_class, state_class, icon = _UNIT_MAP[unit_lookup]
            kwargs["native_unit_of_measurement"] = native_unit
            if device_class is not None:
                kwargs["device_class"] = device_class
            if state_class is not None:
                kwargs["state_class"] = state_class
            if icon is not None and "icon" not in hints:
                kwargs["icon"] = icon
        elif unit_lookup not in _logged_unknown_units:
            _logged_unknown_units.add(unit_lookup)
            _LOGGER.warning(
                "Unknown unit '%s' for sensor key '%s' — no unit/device_class set. "
                "Please report this so it can be added.",
                unit_raw, key,
            )

    # 2. Merge keyword hints — hints override unit defaults only for:
    #    - entity_category (diagnostic)
    #    - state_class = TOTAL_INCREASING (counter keyword wins)
    #    - state_class = None (enum keyword wins)
    #    - icon (only if not already set by unit map)
    if "entity_category" in hints:
        kwargs["entity_category"] = hints["entity_category"]
    if hints.get("state_class") is None and "state_class" in hints:
        # Enum-style field — strip numeric classification
        kwargs.pop("state_class", None)
        kwargs.pop("device_class", None)
    elif hints.get("state_class") == SensorStateClass.TOTAL_INCREASING:
        kwargs["state_class"] = SensorStateClass.TOTAL_INCREASING
    if "icon" in hints:
        kwargs["icon"] = hints["icon"]

    # 3. Value-type inference when no unit was present
    if unit_raw is None and value is not None and "state_class" not in kwargs:
        if _looks_boolean(value):
            pass  # keep as plain sensor
        elif _looks_numeric(value):
            kwargs["state_class"] = SensorStateClass.MEASUREMENT

    return SensorEntityDescription(
        key=key, name=key, has_entity_name=True, **kwargs
    )

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

    # Use current status data for value-type inference on unknown keys
    current_status = (status_coord.data or {}).get("status", {}) if status_coord.data else {}

    # Create sensors only for selected keys
    for key in selected_keys:
        if key in KNOWN_SENSORS:
            entities.append(
                IthoSensor(status_coord, device_coord, KNOWN_SENSORS[key])
            )
        else:
            value = current_status.get(key)
            entities.append(
                IthoSensor(status_coord, device_coord, _description_from_key(key, value))
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
