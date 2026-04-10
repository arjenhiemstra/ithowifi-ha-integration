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

# Config keys
CONF_SENSORS = "sensors"
CONF_DIAGNOSTICS = "diagnostics"
CONF_RF_SOURCE = "rf_source"

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
