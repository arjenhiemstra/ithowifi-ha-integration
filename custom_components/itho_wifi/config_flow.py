"""Config flow for IthoWiFi integration."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import IthoWiFiApi, IthoWiFiApiError, IthoWiFiConnectionError
from .const import CONF_DIAGNOSTICS, CONF_RF_SOURCE, CONF_SENSORS, DIAGNOSTIC_KEYS, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_USERNAME): str,
        vol.Optional(CONF_PASSWORD): str,
    }
)


class IthoWiFiConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for IthoWiFi."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._host: str = ""
        self._username: str | None = None
        self._password: str | None = None
        self._deviceinfo: dict[str, Any] = {}
        self._available_sensors: list[str] = []
        self._available_diagnostics: list[str] = []
        self._rf_standalone: bool = False
        self._rf_sources: list[str] = []

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle the initial step: connection details."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._host = user_input[CONF_HOST].strip()
            self._username = user_input.get(CONF_USERNAME)
            self._password = user_input.get(CONF_PASSWORD)

            try:
                session = async_get_clientsession(self.hass)
                api = IthoWiFiApi(
                    self._host, session, self._username, self._password
                )
                self._deviceinfo = await api.get_deviceinfo()
                status = await api.get_status()
            except IthoWiFiConnectionError:
                errors["base"] = "cannot_connect"
            except IthoWiFiApiError:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected error during config flow")
                errors["base"] = "unknown"

            if not errors:
                device_id = self._deviceinfo.get("add-on_hwid", self._host)
                await self.async_set_unique_id(device_id)
                self._abort_if_unique_id_configured()

                self._rf_standalone = (
                    self._deviceinfo.get("itho_rf_standalone", 0) == 1
                    or self._deviceinfo.get("itho_devtype") in ("Unknown", "Unkown device type", "Generic Itho device")
                )

                # In standalone/unknown mode, get sensor keys from RF status
                if self._rf_standalone:
                    try:
                        rfdata = await api.get_rfstatus()
                        sources = rfdata.get("sources", [])
                        for src in sources:
                            name = src.get("name", "")
                            if name:
                                self._rf_sources.append(name)
                            # Use first source's data for sensor keys
                            if not self._available_sensors:
                                for key, value in src.get("data", {}).items():
                                    if value == "not available":
                                        continue
                                    if key in DIAGNOSTIC_KEYS:
                                        self._available_diagnostics.append(key)
                                    else:
                                        self._available_sensors.append(key)
                    except Exception as ex:
                        _LOGGER.warning("RF status fetch failed: %s", ex)

                    if self._rf_sources:
                        return await self.async_step_rf_source()
                    return await self.async_step_sensors()
                else:
                    # Normal I2C mode
                    for key, value in status.items():
                        if key == "timestamp" or value == "not available":
                            continue
                        if key in DIAGNOSTIC_KEYS:
                            self._available_diagnostics.append(key)
                        else:
                            self._available_sensors.append(key)

                    return await self.async_step_sensors()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_rf_source(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle RF source selection for standalone mode."""
        if user_input is not None:
            self._selected_rf_source = user_input.get(CONF_RF_SOURCE, "")
            return await self.async_step_sensors()

        source_opts = [
            SelectOptionDict(value=name, label=name)
            for name in self._rf_sources
        ]

        return self.async_show_form(
            step_id="rf_source",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_RF_SOURCE,
                        default=self._rf_sources[0] if self._rf_sources else "",
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=source_opts,
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
        )

    async def async_step_sensors(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle the sensor selection step."""
        if user_input is not None:
            title = f"Itho {self._deviceinfo.get('itho_devtype', 'WiFi')}"
            return self.async_create_entry(
                title=title,
                data={
                    CONF_HOST: self._host,
                    CONF_USERNAME: self._username,
                    CONF_PASSWORD: self._password,
                },
                options={
                    CONF_SENSORS: user_input.get(CONF_SENSORS, []),
                    CONF_DIAGNOSTICS: user_input.get(CONF_DIAGNOSTICS, []),
                    CONF_RF_SOURCE: getattr(self, "_selected_rf_source", ""),
                },
            )

        sensor_opts = [
            SelectOptionDict(value=k, label=k)
            for k in self._available_sensors
        ]
        diag_opts = [
            SelectOptionDict(value=k, label=k)
            for k in self._available_diagnostics
        ]

        schema_dict: dict[Any, Any] = {}
        if sensor_opts:
            schema_dict[
                vol.Optional(CONF_SENSORS, default=self._available_sensors)
            ] = SelectSelector(
                SelectSelectorConfig(
                    options=sensor_opts,
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                )
            )
        if diag_opts:
            schema_dict[
                vol.Optional(CONF_DIAGNOSTICS, default=[])
            ] = SelectSelector(
                SelectSelectorConfig(
                    options=diag_opts,
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                )
            )

        return self.async_show_form(
            step_id="sensors",
            data_schema=vol.Schema(schema_dict),
            description_placeholders={
                "device_type": self._deviceinfo.get("itho_devtype", "Unknown"),
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Get the options flow handler."""
        return IthoWiFiOptionsFlow(config_entry)


class IthoWiFiOptionsFlow(OptionsFlow):
    """Handle options for IthoWiFi."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize the options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle options flow."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        # Fetch current status to get available keys
        host = self._config_entry.data[CONF_HOST]
        username = self._config_entry.data.get(CONF_USERNAME)
        password = self._config_entry.data.get(CONF_PASSWORD)

        available_sensors: list[str] = []
        available_diagnostics: list[str] = []

        rf_sources: list[str] = []

        try:
            session = async_get_clientsession(self.hass)
            api = IthoWiFiApi(host, session, username, password)
            deviceinfo = await api.get_deviceinfo()
            rf_standalone = (
                deviceinfo.get("itho_rf_standalone", 0) == 1
                or deviceinfo.get("itho_devtype") in ("Unknown", "Unkown device type", "Generic Itho device")
            )

            if rf_standalone:
                rfdata = await api.get_rfstatus()
                for src in rfdata.get("sources", []):
                    name = src.get("name", "")
                    if name:
                        rf_sources.append(name)
                    if not available_sensors:
                        for key, value in src.get("data", {}).items():
                            if value == "not available":
                                continue
                            if key in DIAGNOSTIC_KEYS:
                                available_diagnostics.append(key)
                            else:
                                available_sensors.append(key)
            else:
                status = await api.get_status()
                for key, value in status.items():
                    if key == "timestamp" or value == "not available":
                        continue
                    if key in DIAGNOSTIC_KEYS:
                        available_diagnostics.append(key)
                    else:
                        available_sensors.append(key)
        except Exception:
            _LOGGER.exception("Failed to fetch status for options")
            available_sensors = self._config_entry.options.get(CONF_SENSORS, [])
            available_diagnostics = self._config_entry.options.get(
                CONF_DIAGNOSTICS, []
            )

        current_sensors = self._config_entry.options.get(CONF_SENSORS, [])
        current_diagnostics = self._config_entry.options.get(
            CONF_DIAGNOSTICS, []
        )

        sensor_opts = [
            SelectOptionDict(value=k, label=k)
            for k in available_sensors
        ]
        diag_opts = [
            SelectOptionDict(value=k, label=k)
            for k in available_diagnostics
        ]

        schema_dict: dict[Any, Any] = {}
        if sensor_opts:
            schema_dict[
                vol.Optional(
                    CONF_SENSORS,
                    default=[s for s in current_sensors if s in {o["value"] for o in sensor_opts}],
                )
            ] = SelectSelector(
                SelectSelectorConfig(
                    options=sensor_opts,
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                )
            )
        if diag_opts:
            schema_dict[
                vol.Optional(
                    CONF_DIAGNOSTICS,
                    default=[d for d in current_diagnostics if d in {o["value"] for o in diag_opts}],
                )
            ] = SelectSelector(
                SelectSelectorConfig(
                    options=diag_opts,
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                )
            )

        if rf_sources:
            current_rf_source = self._config_entry.options.get(CONF_RF_SOURCE, "")
            source_opts = [
                SelectOptionDict(value=name, label=name)
                for name in rf_sources
            ]
            schema_dict[
                vol.Optional(
                    CONF_RF_SOURCE,
                    default=current_rf_source if current_rf_source in rf_sources else (rf_sources[0] if rf_sources else ""),
                )
            ] = SelectSelector(
                SelectSelectorConfig(
                    options=source_opts,
                    mode=SelectSelectorMode.LIST,
                )
            )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema_dict),
        )
