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

from .api import IthoWiFiApi, IthoWiFiApiError, IthoWiFiConnectionError, IthoWiFiNotFoundError
from .const import (
    DOMAIN,
    UPDATE_INTERVAL_DEVICEINFO,
    UPDATE_INTERVAL_REMOTES,
    UPDATE_INTERVAL_STATUS,
)

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
        self.use_rf_commands = False  # set by __init__.py
        self.ota_in_progress = False

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch status data from the device."""
        if self.ota_in_progress:
            return self.data or {}
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
        self.ota_in_progress = False

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch device info from the device."""
        if self.ota_in_progress:
            return self.data or {}
        try:
            return await self.api.get_deviceinfo()
        except IthoWiFiConnectionError as err:
            raise UpdateFailed(f"Connection error: {err}") from err
        except IthoWiFiApiError as err:
            raise UpdateFailed(f"API error: {err}") from err


class IthoRemotesCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator for per-remote state (RF + virtual) used by per-remote fans.

    Polls /api/v2/remotes and /api/v2/vremotes and stores the result as
    {"rf": [...], "vr": [...]} with each entry being a dict with index,
    name, remtype, remtypename, remfunc, remfuncname, last_cmd, and
    isEmptySlot (computed locally).
    """

    def __init__(self, hass: HomeAssistant, api: IthoWiFiApi) -> None:
        """Initialize the remotes coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_remotes",
            update_interval=timedelta(seconds=UPDATE_INTERVAL_REMOTES),
        )
        self.api = api
        # True if the firmware exposes /api/v2/vremotes. Older firmware
        # (<3.1.0-beta3) exists but returned an empty object and doesn't
        # populate last_cmd. On 404, the coordinator keeps its last data
        # and stops polling the missing endpoint.
        self.vremotes_available: bool = True

    # Set to True by IthoFirmwareUpdate while an OTA install is in
    # progress. All coordinators check this and skip their update cycle
    # to avoid heap-exhaustion crashes on the device during download.
    ota_in_progress: bool = False

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch both remote lists from the device."""
        if self.ota_in_progress:
            return self.data or {"rf": [], "vr": []}

        rf_list: list[dict[str, Any]] = []
        vr_list: list[dict[str, Any]] = []

        try:
            rf_list = await self.api.get_remotes()
        except IthoWiFiNotFoundError:
            rf_list = []
        except IthoWiFiConnectionError as err:
            raise UpdateFailed(f"Connection error: {err}") from err
        except IthoWiFiApiError as err:
            raise UpdateFailed(f"API error: {err}") from err

        if self.vremotes_available:
            try:
                vr_list = await self.api.get_vremotes()
            except IthoWiFiNotFoundError:
                self.vremotes_available = False
            except IthoWiFiConnectionError as err:
                raise UpdateFailed(f"Connection error: {err}") from err
            except IthoWiFiApiError:
                # Tolerate a transient vremotes failure — keep rf data.
                pass

        return {"rf": rf_list, "vr": vr_list}
