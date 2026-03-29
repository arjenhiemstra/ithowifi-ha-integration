"""Data coordinator for IthoWiFi integration."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import IthoWiFiApi, IthoWiFiApiError, IthoWiFiConnectionError
from .const import DOMAIN, UPDATE_INTERVAL_DEVICEINFO, UPDATE_INTERVAL_STATUS

_LOGGER = logging.getLogger(__name__)


class IthoStatusCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator for frequent status updates (speed, sensors)."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: IthoWiFiApi,
        rf_standalone: bool = False,
        rf_source_name: str | None = None,
    ) -> None:
        """Initialize the status coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_status",
            update_interval=timedelta(seconds=UPDATE_INTERVAL_STATUS),
        )
        self.api = api
        self.rf_standalone = rf_standalone
        self.rf_source_name = rf_source_name

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch status data from the device."""
        try:
            speed_data = await self.api.get_speed()
            lastcmd_data = await self.api.get_lastcmd()

            if self.rf_standalone and self.rf_source_name:
                status_data = await self.api.get_rfstatus(
                    name=self.rf_source_name
                )
            else:
                status_data = await self.api.get_status()

            return {
                "speed": speed_data,
                "status": status_data,
                "lastcmd": lastcmd_data,
            }
        except IthoWiFiConnectionError as err:
            raise UpdateFailed(f"Connection error: {err}") from err
        except IthoWiFiApiError as err:
            raise UpdateFailed(f"API error: {err}") from err


class IthoDeviceInfoCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator for infrequent device info updates."""

    def __init__(self, hass: HomeAssistant, api: IthoWiFiApi) -> None:
        """Initialize the device info coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_deviceinfo",
            update_interval=timedelta(seconds=UPDATE_INTERVAL_DEVICEINFO),
        )
        self.api = api

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch device info from the device."""
        try:
            return await self.api.get_deviceinfo()
        except IthoWiFiConnectionError as err:
            raise UpdateFailed(f"Connection error: {err}") from err
        except IthoWiFiApiError as err:
            raise UpdateFailed(f"API error: {err}") from err
