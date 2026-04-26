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
    API_OTA,
    API_QUEUE,
    API_REMOTES,
    API_RF_CO2,
    API_RF_COMMAND,
    API_RF_DEMAND,
    API_RFSTATUS,
    API_SETTINGS,
    API_SPEED,
    API_VREMOTE,
    API_VREMOTES,
)

_LOGGER = logging.getLogger(__name__)


class IthoWiFiApiError(Exception):
    """Exception for API errors."""


class IthoWiFiConnectionError(IthoWiFiApiError):
    """Exception for connection errors."""


class IthoWiFiNotFoundError(IthoWiFiApiError):
    """Exception raised when an endpoint is not available (HTTP 404)."""


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
                if resp.status == 404:
                    raise IthoWiFiNotFoundError(
                        f"Endpoint not found: {path}"
                    )
                if resp.status != 200:
                    try:
                        body = await resp.text()
                    except Exception:
                        body = "<unreadable>"
                    raise IthoWiFiApiError(
                        f"API request failed: {resp.status} — {body}"
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

    async def get_remotes(self) -> list[dict[str, Any]]:
        """Get RF remotes configuration as a list of per-slot dicts.

        Returns the `remotes` array added in firmware 3.1.0-beta1 (full
        config). Each entry is a dict with keys: index, id, name, remfunc,
        remfuncname, remtype, remtypename, bidirectional, tx_power, and
        optionally last_cmd and capabilities.
        """
        data = await self._request("GET", API_REMOTES)
        return data.get("remotes", [])

    async def get_vremotes(self) -> list[dict[str, Any]]:
        """Get virtual remotes configuration as a list of per-slot dicts.

        Returns the vremotesinfo array from /api/v2/vremotes. Each entry
        has the same shape as a `remotes` entry. last_cmd was added in
        firmware 3.1.0-beta3.
        """
        data = await self._request("GET", API_VREMOTES)
        return data.get("vremotesinfo", [])

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
        """Send a named fan command. Falls back to vremote on rejection.

        Only called when use_rf_commands=False (RF-CO2 / RF-standalone
        users go through send_rf_command directly). The /api/v2/command
        path requires a pwm2i2c-capable device (CVE/HRU200); other I2C
        devices like HRU-eco get a 400 with the firmware-supplied hint
        to use a virtual remote, which is what the fallback does.
        """
        try:
            return await self._request(
                "POST", API_COMMAND, json_data={"command": command}
            )
        except IthoWiFiApiError as err:
            _LOGGER.debug(
                "send_command(%r) direct path failed (%s), falling back to vremote",
                command, err,
            )
            return await self.send_vremote_command(command)

    async def set_speed(
        self, speed: int, timer: int | None = None
    ) -> dict[str, Any]:
        """Set fan speed (0-255), optionally with timer.

        Only called when use_rf_commands=False. On rejection, falls back
        to a vremote preset mapped from the requested speed — vremote
        commands are preset-only, so precision is approximate.
        """
        data: dict[str, Any] = {"speed": speed}
        if timer is not None:
            data["timer"] = timer
        try:
            return await self._request("POST", API_COMMAND, json_data=data)
        except IthoWiFiApiError as err:
            if speed < 1:
                preset = "away"
            elif speed < 86:
                preset = "low"
            elif speed < 171:
                preset = "medium"
            else:
                preset = "high"
            _LOGGER.debug(
                "set_speed(%r) direct path failed (%s), falling back to vremote preset %r",
                speed, err, preset,
            )
            return await self.send_vremote_command(preset)

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

    async def get_ota(self) -> dict[str, Any]:
        """Get firmware version info and OTA progress."""
        data = await self._request("GET", API_OTA)
        return data.get("ota", data)

    async def start_ota(self, channel: str) -> dict[str, Any]:
        """Start a firmware OTA install on the given channel ('stable' or 'beta')."""
        return await self._request(
            "POST", API_OTA, json_data={"channel": channel}
        )
