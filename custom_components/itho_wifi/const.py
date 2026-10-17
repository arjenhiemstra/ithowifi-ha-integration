"""Constants for the IthoWiFi integration."""

DOMAIN = "itho_wifi"
MANUFACTURER = "Arjen Hiemstra"

CONF_HOST = "host"

# API endpoints
API_SPEED = "/api/v2/speed"
API_ITHOSTATUS = "/api/v2/ithostatus"
API_DEVICEINFO = "/api/v2/deviceinfo"
API_LASTCMD = "/api/v2/lastcmd"
API_QUEUE = "/api/v2/queue"
API_REMOTES = "/api/v2/remotes"
API_VREMOTES = "/api/v2/vremotes"
API_RFSTATUS = "/api/v2/rfstatus"
API_SETTINGS = "/api/v2/settings"
API_COMMAND = "/api/v2/command"
API_VREMOTE = "/api/v2/vremote"
API_RF_COMMAND = "/api/v2/rfremote/command"
API_RF_CO2 = "/api/v2/rfremote/co2"
API_RF_DEMAND = "/api/v2/rfremote/demand"
API_RF_SETPOINT = "/api/v2/rfremote/setpoint"
API_DEBUG = "/api/v2/debug"
API_OTA = "/api/v2/ota"

# Fan preset modes
PRESET_LOW = "low"
PRESET_MEDIUM = "medium"
PRESET_HIGH = "high"
PRESET_AUTO = "auto"
PRESET_AUTONIGHT = "autonight"
PRESET_AWAY = "away"

# Timer presets (available on all devices)
TIMER_PRESETS = {
    "timer1": "Timer 1",
    "timer2": "Timer 2",
    "timer3": "Timer 3",
}

# Cook presets (only for QualityFlow / DemandFlow devices)
COOK_PRESETS = {
    "cook30": "Cook 30 min",
    "cook60": "Cook 60 min",
}

# Device types that support cook presets
COOK_DEVICE_TYPES = {"QualityFlow", "DemandFlow"}

# Device types that are NOT fans/ventilation units. For these, the integration
# should not register the main fan / preset-button / fan-demand entities.
# DemandFlow IS a fan-control device but uses a per-remote model — handled
# separately, not via this list. Matched with substring checks so "AutoTemp
# Basic" also matches "AutoTemp".
NON_FAN_DEVICE_TYPES = ("Heatpump", "AutoTemp")


def is_fan_device(itho_devtype: str | None) -> bool:
    """Return True if the given device type is a ventilation/fan unit."""
    if not itho_devtype:
        # Unknown / generic device — assume fan-like (RF standalone setups,
        # generic remotes, etc. all fall into this bucket).
        return True
    return not any(t in itho_devtype for t in NON_FAN_DEVICE_TYPES)

# Config keys
CONF_SENSORS = "sensors"
CONF_DIAGNOSTICS = "diagnostics"
CONF_RF_SOURCE = "rf_source"
# List of per-remote fan selections, each entry "vr:<index>" or "rf:<index>"
CONF_REMOTE_FANS = "remote_fans"
# List of per-remote sensor selections (remotes whose received data — CO2,
# temperature, last command, ... — is exposed as sensors), same "vr:<index>" /
# "rf:<index>" encoding.
CONF_REMOTE_SENSORS = "remote_sensors"
# Itho Spider thermostats exposed as climate entities, keyed by the Spider's
# RF id as 6 hex chars (e.g. "54D3FA") so the entity survives slot changes.
CONF_THERMOSTATS = "thermostats"


def remote_rf_uid(remote: dict) -> str:
    """Return a remote's RF id as 6 uppercase hex chars ("" if unset)."""
    rid = remote.get("id") or [0, 0, 0]
    if len(rid) < 3 or all(b == 0 for b in rid[:3]):
        return ""
    return "".join(f"{int(b):02X}" for b in rid[:3])


def format_rf_uid(uid: str) -> str:
    """Format "54D3FA" as "54:D3:FA"."""
    return ":".join(uid[i : i + 2] for i in range(0, len(uid), 2))


def is_spider_thermostat(remote: dict) -> bool:
    """True if the firmware reports this remote as an Itho Spider thermostat."""
    caps = remote.get("capabilities")
    return isinstance(caps, dict) and bool(caps.get("thermostat")) and bool(remote_rf_uid(remote))

# DemandFlow uses per-remote fans only (no main IthoFan). Detected by substring
# so "DemandFlow" alone matches.
DEMANDFLOW_DEVICE_TYPES = ("DemandFlow",)


def is_demandflow_device(itho_devtype: str | None) -> bool:
    """Return True if the device is a DemandFlow ventilation unit."""
    if not itho_devtype:
        return False
    return any(t in itho_devtype for t in DEMANDFLOW_DEVICE_TYPES)

# Diagnostic sensor keys (entity_category = DIAGNOSTIC)
DIAGNOSTIC_KEYS = {
    "Internal fault",
    "Error",
    "Total operation (hours)",
    "Startup counter",
    "Frost cycle",
    "SpeedCap",
    "Selection",
    "AirQbased on",
}

# Update intervals in seconds
UPDATE_INTERVAL_STATUS = 10
UPDATE_INTERVAL_DEVICEINFO = 300
UPDATE_INTERVAL_REMOTES = 30
