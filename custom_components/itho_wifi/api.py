"""API client for IthoWiFi add-on."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from .const import (
    API_COMMAND,
    API_DEBUG,
    API_DEVICEINFO,
    API_ITHOSTATUS,
    API_LASTCMD,
    API_QUEUE,
    API_RF_CO2,
    API_RF_COMMAND,
    API_RF_DEMAND,
    API_RFSTATUS,
    API_SETTINGS,
    API_SPEED,
    API_VREMOTE,
)

_LOGGER = logging.getLogger(__name__)


class IthoWiFiApiError(Exception):
    """Exception for API errors."""


class IthoWiFiConnectionError(IthoWiFiApiError):
    """Exception for connection errors."""


class IthoWiFiApi:
    """API client for IthoWiFi add-on."""

    def __init__(
        self,
        host: str,
        session: aiohttp.ClientSession,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        """Initialize the API client."""
        self._host = host
        self._session = session
        self._auth: aiohttp.BasicAuth | None = None
        if username and password:
            self._auth = aiohttp.BasicAuth(username, password)

    @property
    def base_url(self) -> str:
        """Return the base URL."""
        return f"http://{self._host}"

    async def _request(
        self,
        method: str,
        path: str,
        json_data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make an API request."""
        url = f"{self.base_url}{path}"
        try:
            async with self._session.request(
                method,
                url,
                json=json_data,
                params=params,
                auth=self._auth,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 401:
                    raise IthoWiFiApiError("Authentication failed")
                if resp.status != 200:
                    raise IthoWiFiApiError(
                        f"API request failed: {resp.status}"
                    )
                data = await resp.json()
                if data.get("status") == "error":
                    raise IthoWiFiApiError(
                        data.get("message", "Unknown error")
                    )
                return data.get("data", {})
        except aiohttp.ClientError as err:
            raise IthoWiFiConnectionError(
                f"Connection to {self._host} failed: {err}"
            ) from err
        except asyncio.TimeoutError as err:
            raise IthoWiFiConnectionError(
                f"Timeout connecting to {self._host}"
            ) from err

    async def get_speed(self) -> dict[str, Any]:
        """Get current fan speed."""
        data = await self._request("GET", API_SPEED)
        return {"currentspeed": data.get("currentspeed", 0)}

    async def get_status(self) -> dict[str, Any]:
        """Get Itho device status and measurements."""
        data = await self._request("GET", API_ITHOSTATUS)
        return data.get("ithostatus", data)

    async def get_deviceinfo(self) -> dict[str, Any]:
        """Get device information."""
        data = await self._request("GET", API_DEVICEINFO)
        return data.get("deviceinfo", data)

    async def get_rfstatus(self, name: str | None = None) -> dict[str, Any]:
        """Get RF status data from tracked sources."""
        params = {"name": name} if name else None
        data = await self._request("GET", API_RFSTATUS, params=params)
        rfstatus = data.get("rfstatus", data)
        # Single source query returns flat dict, multi returns sources array
        if name and "data" in rfstatus:
            return rfstatus.get("data", {})
        return rfstatus

    async def get_lastcmd(self) -> dict[str, Any]:
        """Get last executed command."""
        data = await self._request("GET", API_LASTCMD)
        return data.get("lastcmd", data)

    async def get_queue(self) -> dict[str, Any]:
        """Get command queue status."""
        data = await self._request("GET", API_QUEUE)
        return data.get("queue", data)

    async def get_setting(self, index: int) -> dict[str, Any]:
        """Read a device setting by index."""
        return await self._request(
            "GET", API_SETTINGS, params={"index": index}
        )

    async def set_setting(self, index: int, value: float) -> dict[str, Any]:
        """Write a device setting by index."""
        return await self._request(
            "PUT", API_SETTINGS, json_data={"index": index, "value": value}
        )

    async def send_command(self, command: str) -> dict[str, Any]:
        """Send a named fan command."""
        return await self._request(
            "POST", API_COMMAND, json_data={"command": command}
        )

    async def set_speed(
        self, speed: int, timer: int | None = None
    ) -> dict[str, Any]:
        """Set fan speed (0-255), optionally with timer."""
        data: dict[str, Any] = {"speed": speed}
        if timer is not None:
            data["timer"] = timer
        return await self._request("POST", API_COMMAND, json_data=data)

    async def set_percentage(self, percentage: int) -> dict[str, Any]:
        """Set fan percentage (0-100)."""
        return await self._request(
            "POST", API_COMMAND, json_data={"percentage": percentage}
        )

    async def send_vremote_command(
        self, command: str, index: int = 0
    ) -> dict[str, Any]:
        """Send virtual remote command."""
        return await self._request(
            "POST",
            API_VREMOTE,
            json_data={"command": command, "index": index},
        )

    async def send_rf_command(
        self, command: str, index: int = 0
    ) -> dict[str, Any]:
        """Send RF remote command."""
        return await self._request(
            "POST",
            API_RF_COMMAND,
            json_data={"command": command, "index": index},
        )

    async def send_rf_co2(
        self, co2: int, index: int = 0
    ) -> dict[str, Any]:
        """Send CO2 value via RF."""
        return await self._request(
            "POST",
            API_RF_CO2,
            json_data={"co2": co2, "index": index},
        )

    async def send_rf_demand(
        self, demand: int, zone: int = 0, index: int = 0
    ) -> dict[str, Any]:
        """Send ventilation demand via RF."""
        return await self._request(
            "POST",
            API_RF_DEMAND,
            json_data={"demand": demand, "zone": zone, "index": index},
        )

    async def reboot(self) -> dict[str, Any]:
        """Reboot the device."""
        return await self._request(
            "POST", API_DEBUG, json_data={"action": "reboot"}
        )
