"""The IthoWiFi integration."""

from __future__ import annotations

import logging

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import IthoWiFiApi
from .const import CONF_RF_SOURCE, DOMAIN
from .coordinator import (
    IthoDeviceInfoCoordinator,
    IthoRemotesCoordinator,
    IthoStatusCoordinator,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.FAN,
    Platform.SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.UPDATE,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up IthoWiFi from a config entry."""
    host = entry.data[CONF_HOST]
    username = entry.data.get(CONF_USERNAME)
    password = entry.data.get(CONF_PASSWORD)

    session = async_get_clientsession(hass)
    api = IthoWiFiApi(host, session, username, password)

    # Create coordinators — device info first to detect standalone mode
    device_coordinator = IthoDeviceInfoCoordinator(hass, api)
    await device_coordinator.async_config_entry_first_refresh()

    rf_standalone = (
        device_coordinator.data.get("itho_rf_standalone", 0) == 1
        or device_coordinator.data.get("itho_devtype") in ("Unknown", "Unkown device type", "Generic Itho device")
    )
    rf_source_name = entry.options.get(CONF_RF_SOURCE)

    # Use RF commands when in standalone mode or RF CO2 control interface
    use_rf_commands = (
        rf_standalone
        or device_coordinator.data.get("itho_control_interface", 0) == 1
    )

    _LOGGER.warning(
        "IthoWiFi setup: use_rf_commands=%s rf_standalone=%s "
        "itho_devtype=%r itho_rf_standalone=%r itho_control_interface=%r "
        "deviceinfo=%r",
        use_rf_commands,
        rf_standalone,
        device_coordinator.data.get("itho_devtype"),
        device_coordinator.data.get("itho_rf_standalone"),
        device_coordinator.data.get("itho_control_interface"),
        device_coordinator.data,
    )

    status_coordinator = IthoStatusCoordinator(
        hass, api, rf_standalone=rf_standalone, rf_source_name=rf_source_name
    )
    status_coordinator.use_rf_commands = use_rf_commands
    await status_coordinator.async_config_entry_first_refresh()

    # Remotes coordinator drives per-remote fan entities. First refresh is
    # allowed to fail (empty data) — this is an additive feature and the
    # main fan / sensors / update entity should still work even if the
    # remotes endpoint is unreachable.
    remotes_coordinator = IthoRemotesCoordinator(hass, api)
    try:
        await remotes_coordinator.async_config_entry_first_refresh()
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Initial remotes fetch failed: %s", err)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "api": api,
        "status_coordinator": status_coordinator,
        "device_coordinator": device_coordinator,
        "remotes_coordinator": remotes_coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def _async_update_listener(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Reload entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(
        entry, PLATFORMS
    ):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
