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
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .api import IthoWiFiApi, IthoWiFiApiError, IthoWiFiConnectionError, IthoWiFiNotFoundError
from .const import (
    CONF_DIAGNOSTICS,
    CONF_REMOTE_FANS,
    CONF_REMOTE_SENSORS,
    CONF_THERMOSTATS,
    format_rf_uid,
    is_spider_thermostat,
    remote_rf_uid,
    CONF_RF_SOURCE,
    CONF_RF_SOURCE_ID,
    CONF_SENSORS,
    DIAGNOSTIC_KEYS,
    DOMAIN,
    is_demandflow_device,
)

_LOGGER = logging.getLogger(__name__)


def _build_remote_fan_options(
    rf_list: list[dict[str, Any]],
    vr_list: list[dict[str, Any]],
) -> tuple[list[SelectOptionDict], list[str]]:
    """Build selector options + DemandFlow-default list from remote data.

    Returns (options, demandflow_default). Each option's value is
    "vr:<index>" or "rf:<index>" and the label combines the kind, name,
    and remote type for easy identification.

    Only configured (non-empty) slots are emitted. RF remotes are
    included only when their function is SEND (remfunc == 5).
    """
    options: list[SelectOptionDict] = []
    df_default: list[str] = []

    def _is_empty(r: dict[str, Any]) -> bool:
        rid = r.get("id") or [0, 0, 0]
        return all(b == 0 for b in rid[:3])

    for vr in vr_list:
        if _is_empty(vr):
            continue
        idx = vr.get("index", 0)
        label = f"Virtual Remote {idx} — {vr.get('name') or '(unnamed)'} ({vr.get('remtypename') or 'unknown'})"
        value = f"vr:{idx}"
        options.append(SelectOptionDict(value=value, label=label))
        df_default.append(value)

    for rf in rf_list:
        if _is_empty(rf):
            continue
        if rf.get("remfunc") != 5:  # SEND only
            continue
        idx = rf.get("index", 0)
        label = f"RF Remote {idx} — {rf.get('name') or '(unnamed)'} ({rf.get('remtypename') or 'unknown'})"
        value = f"rf:{idx}"
        options.append(SelectOptionDict(value=value, label=label))
        df_default.append(value)

    return options, df_default


def _build_remote_sensor_options(
    rf_list: list[dict[str, Any]],
    vr_list: list[dict[str, Any]],
) -> list[SelectOptionDict]:
    """Build selector options for per-remote sensors.

    Offers every configured (non-empty) remote that currently reports a
    `capabilities` object — i.e. a remote the add-on has received live data
    from (CO2, temperature, last command, ...). Value is "vr:<index>" /
    "rf:<index>", matching the fan selector encoding.
    """
    options: list[SelectOptionDict] = []

    def _is_empty(r: dict[str, Any]) -> bool:
        rid = r.get("id") or [0, 0, 0]
        return all(b == 0 for b in rid[:3])

    for kind, lst in (("rf", rf_list), ("vr", vr_list)):
        label_kind = "RF Remote" if kind == "rf" else "Virtual Remote"
        for r in lst:
            if _is_empty(r):
                continue
            if not isinstance(r.get("capabilities"), dict):
                continue
            idx = r.get("index", 0)
            label = f"{label_kind} {idx} — {r.get('name') or '(unnamed)'} ({r.get('remtypename') or 'unknown'})"
            options.append(SelectOptionDict(value=f"{kind}:{idx}", label=label))

    return options


def _build_thermostat_options(rf_list: list[dict[str, Any]]) -> list[SelectOptionDict]:
    """Build selector options for Itho Spider thermostats, keyed by the Spider's RF id."""
    return [
        SelectOptionDict(
            value=remote_rf_uid(r),
            label=f"{r.get('name') or 'Spider'} {format_rf_uid(remote_rf_uid(r))} (RF Remote {r.get('index', 0)})",
        )
        for r in rf_list
        if is_spider_thermostat(r)
    ]


def _rf_source_id(src: dict[str, Any]) -> str:
    """Return the RF id ("AA:BB:CC") of a tracked rfstatus source."""
    return str(src.get("id", "")).upper()


def _sorted_rf_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sources with an id, the ones that have been heard first."""
    return sorted(
        (s for s in sources if _rf_source_id(s)),
        key=lambda s: not s.get("lastSeen"),
    )


def _rf_source_options(sources: list[dict[str, Any]]) -> list[SelectOptionDict]:
    """Selector options keyed by RF id, so equally named sources stay apart."""
    options: list[SelectOptionDict] = []
    for src in sources:
        label = f"{src.get('name') or '(unnamed)'} ({_rf_source_id(src)})"
        if not src.get("lastSeen"):
            label += ", no data yet"
        options.append(SelectOptionDict(value=_rf_source_id(src), label=label))
    return options


def _rf_source_keys(
    sources: list[dict[str, Any]], source_id: str
) -> tuple[list[str], list[str]]:
    """Sensor and diagnostic keys of the chosen source.

    Falls back to the keys of all sources while the chosen one has not
    reported yet, so sensors can still be picked.
    """
    chosen = [
        s for s in sources if _rf_source_id(s) == source_id and s.get("data")
    ]
    sensors: list[str] = []
    diagnostics: list[str] = []
    for src in chosen or sources:
        for key, value in (src.get("data") or {}).items():
            if value == "not available":
                continue
            target = diagnostics if key in DIAGNOSTIC_KEYS else sensors
            if key not in target:
                target.append(key)
    return sensors, diagnostics


def _current_rf_source_id(
    options: dict[str, Any], sources: list[dict[str, Any]]
) -> str:
    """Saved source id; entries from before rf_source_id are matched by name."""
    ids = [_rf_source_id(s) for s in sources]
    if (saved := options.get(CONF_RF_SOURCE_ID)) in ids:
        return saved
    name = options.get(CONF_RF_SOURCE)
    for src in sources:
        if name and src.get("name") == name:
            return _rf_source_id(src)
    return ids[0] if ids else ""


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
        self._discovered_name: str = ""
        self._username: str | None = None
        self._password: str | None = None
        self._deviceinfo: dict[str, Any] = {}
        self._available_sensors: list[str] = []
        self._available_diagnostics: list[str] = []
        self._rf_standalone: bool = False
        self._rf_sources: list[dict[str, Any]] = []
        # Accumulated across steps so the final create_entry sees
        # everything the user picked in earlier screens.
        self._pending_sensors: list[str] = []
        self._pending_diagnostics: list[str] = []
        self._remote_fan_opts: list[SelectOptionDict] = []
        self._remote_fan_default: list[str] = []
        self._selected_remote_fans: list[str] = []
        self._remote_sensor_opts: list[SelectOptionDict] = []
        self._selected_remote_sensors: list[str] = []
        self._thermostat_opts: list[SelectOptionDict] | None = None

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
            if (result := await self._async_connect(errors)) is not None:
                return result

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Handle an add-on found via mDNS.

        Firmware with TXT records advertises its hwid, which is the entry's
        unique_id; older firmware is matched on the default hostname and the
        hwid is fetched from /api/v2/deviceinfo when no auth is required.
        """
        if discovery_info.ip_address.version != 4:
            return self.async_abort(reason="not_ipv4_address")

        host = str(discovery_info.ip_address)
        hwid = discovery_info.properties.get("hwid")
        if not hwid:
            self._async_abort_entries_match({CONF_HOST: host})
            api = IthoWiFiApi(host, async_get_clientsession(self.hass))
            try:
                hwid = (await api.get_deviceinfo()).get("add-on_hwid")
            except IthoWiFiConnectionError:
                return self.async_abort(reason="cannot_connect")
            except IthoWiFiNotFoundError:
                return self.async_abort(reason="not_supported")
            except IthoWiFiApiError:
                hwid = None  # API security on; unique_id is set after login

        if hwid:
            await self.async_set_unique_id(hwid)
            self._abort_if_unique_id_configured(updates={CONF_HOST: host})

        self._host = host
        self._discovered_name = discovery_info.name.split(".")[0].removesuffix(
            " Web Interface"
        )
        self.context["title_placeholders"] = {"name": self._discovered_name}
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Confirm a discovered add-on; credentials only if API security is on."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._username = user_input.get(CONF_USERNAME)
            self._password = user_input.get(CONF_PASSWORD)
            if (result := await self._async_connect(errors)) is not None:
                return result

        return self.async_show_form(
            step_id="zeroconf_confirm",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_USERNAME): str,
                    vol.Optional(CONF_PASSWORD): str,
                }
            ),
            description_placeholders={
                "name": self._discovered_name,
                "host": self._host,
            },
            errors=errors,
        )

    async def _async_connect(
        self, errors: dict[str, str]
    ) -> ConfigFlowResult | None:
        """Connect to self._host and continue the flow; fills errors on failure."""
        try:
            session = async_get_clientsession(self.hass)
            api = IthoWiFiApi(
                self._host, session, self._username, self._password
            )
            self._deviceinfo = await api.get_deviceinfo()
            status = await api.get_status()
        except IthoWiFiConnectionError:
            errors["base"] = "cannot_connect"
            return None
        except IthoWiFiApiError:
            errors["base"] = "invalid_auth"
            return None
        except Exception:
            _LOGGER.exception("Unexpected error during config flow")
            errors["base"] = "unknown"
            return None

        device_id = self._deviceinfo.get("add-on_hwid", self._host)
        await self.async_set_unique_id(device_id)
        self._abort_if_unique_id_configured()

        self._rf_standalone = (
            self._deviceinfo.get("itho_rf_standalone", 0) == 1
            or self._deviceinfo.get("itho_devtype") in ("Unknown", "Unkown device type", "Generic Itho device")
        )

        # In standalone/unknown mode the sensor keys come from the RF source
        # picked in the next step
        if self._rf_standalone:
            try:
                rfdata = await api.get_rfstatus()
                self._rf_sources = _sorted_rf_sources(rfdata.get("sources", []))
            except Exception as ex:
                _LOGGER.warning("RF status fetch failed: %s", ex)

            if self._rf_sources:
                return await self.async_step_rf_source()
            return await self.async_step_sensors()

        # Normal I2C mode
        for key, value in status.items():
            if key == "timestamp" or value == "not available":
                continue
            if key in DIAGNOSTIC_KEYS:
                self._available_diagnostics.append(key)
            else:
                self._available_sensors.append(key)

        return await self.async_step_sensors()

    async def async_step_rf_source(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle RF source selection for standalone mode."""
        if user_input is not None:
            self._selected_rf_source_id = user_input.get(CONF_RF_SOURCE, "")
            self._selected_rf_source = next(
                (
                    s.get("name", "")
                    for s in self._rf_sources
                    if _rf_source_id(s) == self._selected_rf_source_id
                ),
                "",
            )
            (
                self._available_sensors,
                self._available_diagnostics,
            ) = _rf_source_keys(self._rf_sources, self._selected_rf_source_id)
            return await self.async_step_sensors()

        return self.async_show_form(
            step_id="rf_source",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_RF_SOURCE,
                        default=_rf_source_id(self._rf_sources[0]),
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=_rf_source_options(self._rf_sources),
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
            # Stash the sensor/diag picks and pivot to the per-remote
            # fans step. The entry is created at the end of that step.
            self._pending_sensors = user_input.get(CONF_SENSORS, [])
            self._pending_diagnostics = user_input.get(CONF_DIAGNOSTICS, [])
            return await self.async_step_remote_fans()

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

    async def async_step_remote_fans(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Offer a per-remote fan selection during initial setup.

        DemandFlow devices get every configured remote pre-checked.
        Other fan-capable devices show the same list unchecked so the
        user can opt in. If no configured remotes are found at all (or
        the firmware is too old to expose the list), skip this step
        silently and finalize the entry.
        """
        if user_input is None and not self._remote_fan_opts:
            # Fetch remote lists once. Tolerate all failures — this
            # step is additive; on any error fall straight through to
            # entry creation with no per-remote fans selected.
            try:
                session = async_get_clientsession(self.hass)
                api = IthoWiFiApi(
                    self._host, session, self._username, self._password
                )
                try:
                    rf_list = await api.get_remotes()
                except (IthoWiFiApiError, IthoWiFiConnectionError, IthoWiFiNotFoundError):
                    rf_list = []
                try:
                    vr_list = await api.get_vremotes()
                except (IthoWiFiApiError, IthoWiFiConnectionError, IthoWiFiNotFoundError):
                    vr_list = []
                self._remote_fan_opts, df_default = _build_remote_fan_options(
                    rf_list, vr_list
                )
                if is_demandflow_device(self._deviceinfo.get("itho_devtype")):
                    self._remote_fan_default = df_default
                else:
                    self._remote_fan_default = []
            except Exception as ex:  # noqa: BLE001
                _LOGGER.warning(
                    "Could not fetch remotes for setup wizard: %s", ex
                )
                self._remote_fan_opts = []
                self._remote_fan_default = []

        # Handle submission — or skip the step if there's nothing to show —
        # then continue to the per-remote sensor selection.
        if user_input is not None or not self._remote_fan_opts:
            self._selected_remote_fans = (
                user_input.get(CONF_REMOTE_FANS, []) if user_input else []
            )
            return await self.async_step_remote_sensors()

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_REMOTE_FANS, default=self._remote_fan_default
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=self._remote_fan_opts,
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                )
            }
        )

        return self.async_show_form(
            step_id="remote_fans",
            data_schema=schema,
            description_placeholders={
                "device_type": self._deviceinfo.get("itho_devtype", "Unknown"),
            },
        )

    async def async_step_remote_sensors(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Offer a per-remote sensor selection during initial setup.

        Lists every configured remote that reports live data (CO2,
        temperature, last command, ...), unchecked by default so the user
        opts in — same UX as the per-remote fan selection. Skipped silently
        when no such remotes exist.
        """
        if user_input is None and not self._remote_sensor_opts:
            try:
                session = async_get_clientsession(self.hass)
                api = IthoWiFiApi(
                    self._host, session, self._username, self._password
                )
                try:
                    rf_list = await api.get_remotes()
                except (IthoWiFiApiError, IthoWiFiConnectionError, IthoWiFiNotFoundError):
                    rf_list = []
                try:
                    vr_list = await api.get_vremotes()
                except (IthoWiFiApiError, IthoWiFiConnectionError, IthoWiFiNotFoundError):
                    vr_list = []
                self._remote_sensor_opts = _build_remote_sensor_options(rf_list, vr_list)
            except Exception as ex:  # noqa: BLE001
                _LOGGER.warning(
                    "Could not fetch remotes for sensor setup step: %s", ex
                )
                self._remote_sensor_opts = []

        if user_input is not None or not self._remote_sensor_opts:
            self._selected_remote_sensors = (
                user_input.get(CONF_REMOTE_SENSORS, []) if user_input else []
            )
            return await self.async_step_thermostats()

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_REMOTE_SENSORS, default=[]
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=self._remote_sensor_opts,
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                )
            }
        )

        return self.async_show_form(
            step_id="remote_sensors",
            data_schema=schema,
            description_placeholders={
                "device_type": self._deviceinfo.get("itho_devtype", "Unknown"),
            },
        )

    async def async_step_thermostats(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Offer the Itho Spider thermostats the add-on has discovered as climate entities.

        Pre-checked: a Spider is only on the add-on after the user ran its discovery.
        Skipped silently when there are none (or the firmware is too old).
        """
        if self._thermostat_opts is None:
            try:
                session = async_get_clientsession(self.hass)
                api = IthoWiFiApi(
                    self._host, session, self._username, self._password
                )
                rf_list = await api.get_remotes()
                self._thermostat_opts = _build_thermostat_options(rf_list)
            except Exception as ex:  # noqa: BLE001
                _LOGGER.warning(
                    "Could not fetch Spider thermostats for setup: %s", ex
                )
                self._thermostat_opts = []

        if user_input is not None or not self._thermostat_opts:
            selected = user_input.get(CONF_THERMOSTATS, []) if user_input else []
            return self._finalize_entry(
                self._selected_remote_fans, self._selected_remote_sensors, selected
            )

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_THERMOSTATS,
                    default=[o["value"] for o in self._thermostat_opts],
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=self._thermostat_opts,
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                )
            }
        )

        return self.async_show_form(
            step_id="thermostats",
            data_schema=schema,
            description_placeholders={
                "device_type": self._deviceinfo.get("itho_devtype", "Unknown"),
            },
        )

    def _finalize_entry(
        self,
        remote_fans: list[str],
        remote_sensors: list[str] | None = None,
        thermostats: list[str] | None = None,
    ) -> ConfigFlowResult:
        """Create the config entry with everything gathered across steps."""
        title = f"Itho {self._deviceinfo.get('itho_devtype', 'WiFi')}"
        return self.async_create_entry(
            title=title,
            data={
                CONF_HOST: self._host,
                CONF_USERNAME: self._username,
                CONF_PASSWORD: self._password,
            },
            options={
                CONF_SENSORS: self._pending_sensors,
                CONF_DIAGNOSTICS: self._pending_diagnostics,
                CONF_RF_SOURCE: getattr(self, "_selected_rf_source", ""),
                CONF_RF_SOURCE_ID: getattr(self, "_selected_rf_source_id", ""),
                CONF_REMOTE_FANS: remote_fans,
                CONF_REMOTE_SENSORS: remote_sensors or [],
                CONF_THERMOSTATS: thermostats or [],
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
        self._rf_sources: list[dict[str, Any]] = []

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle options flow."""
        if user_input is not None:
            # The RF source selector is keyed by id; keep the name alongside
            if (source_id := user_input.get(CONF_RF_SOURCE)) is not None:
                user_input = {
                    **user_input,
                    CONF_RF_SOURCE_ID: source_id,
                    CONF_RF_SOURCE: next(
                        (
                            s.get("name", "")
                            for s in self._rf_sources
                            if _rf_source_id(s) == source_id
                        ),
                        "",
                    ),
                }
            # Keep the thermostat selection when that field was not shown
            # (no Spiders listed, or the add-on was briefly unreachable).
            if CONF_THERMOSTATS not in user_input and CONF_THERMOSTATS in self._config_entry.options:
                user_input = {**user_input, CONF_THERMOSTATS: self._config_entry.options[CONF_THERMOSTATS]}
            return self.async_create_entry(data=user_input)

        # Fetch current status to get available keys
        host = self._config_entry.data[CONF_HOST]
        username = self._config_entry.data.get(CONF_USERNAME)
        password = self._config_entry.data.get(CONF_PASSWORD)

        available_sensors: list[str] = []
        available_diagnostics: list[str] = []

        remote_fan_opts: list[SelectOptionDict] = []
        remote_sensor_opts: list[SelectOptionDict] = []
        thermostat_opts: list[SelectOptionDict] = []
        demandflow_default_remote_fans: list[str] = []
        is_df = False

        try:
            session = async_get_clientsession(self.hass)
            api = IthoWiFiApi(host, session, username, password)
            deviceinfo = await api.get_deviceinfo()
            is_df = is_demandflow_device(deviceinfo.get("itho_devtype"))
            rf_standalone = (
                deviceinfo.get("itho_rf_standalone", 0) == 1
                or deviceinfo.get("itho_devtype") in ("Unknown", "Unkown device type", "Generic Itho device")
            )

            # Build per-remote fan options from live /api/v2/remotes and
            # /api/v2/vremotes. Failures are non-fatal — a missing endpoint
            # (older firmware) just means no per-remote fans are offered.
            try:
                rf_list = await api.get_remotes()
            except (IthoWiFiApiError, IthoWiFiConnectionError):
                rf_list = []
            try:
                vr_list = await api.get_vremotes()
            except IthoWiFiNotFoundError:
                vr_list = []
            except (IthoWiFiApiError, IthoWiFiConnectionError):
                vr_list = []
            remote_fan_opts, demandflow_default_remote_fans = _build_remote_fan_options(
                rf_list, vr_list
            )
            remote_sensor_opts = _build_remote_sensor_options(rf_list, vr_list)
            thermostat_opts = _build_thermostat_options(rf_list)

            if rf_standalone:
                rfdata = await api.get_rfstatus()
                self._rf_sources = _sorted_rf_sources(rfdata.get("sources", []))
                available_sensors, available_diagnostics = _rf_source_keys(
                    self._rf_sources,
                    _current_rf_source_id(self._config_entry.options, self._rf_sources),
                )
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

        if self._rf_sources:
            schema_dict[
                vol.Optional(
                    CONF_RF_SOURCE,
                    default=_current_rf_source_id(
                        self._config_entry.options, self._rf_sources
                    ),
                )
            ] = SelectSelector(
                SelectSelectorConfig(
                    options=_rf_source_options(self._rf_sources),
                    mode=SelectSelectorMode.LIST,
                )
            )

        if remote_fan_opts:
            # Current selection falls back to the DemandFlow default if
            # nothing has been saved yet — this is the only place where a
            # first-time DemandFlow user gets auto-populated from options.
            current_remote_fans = self._config_entry.options.get(
                CONF_REMOTE_FANS
            )
            if current_remote_fans is None:
                current_remote_fans = (
                    demandflow_default_remote_fans if is_df else []
                )
            valid_values = {o["value"] for o in remote_fan_opts}
            default_remote_fans = [
                v for v in current_remote_fans if v in valid_values
            ]
            schema_dict[
                vol.Optional(
                    CONF_REMOTE_FANS,
                    default=default_remote_fans,
                )
            ] = SelectSelector(
                SelectSelectorConfig(
                    options=remote_fan_opts,
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                )
            )

        if remote_sensor_opts:
            current_remote_sensors = (
                self._config_entry.options.get(CONF_REMOTE_SENSORS) or []
            )
            valid_sensor_values = {o["value"] for o in remote_sensor_opts}
            schema_dict[
                vol.Optional(
                    CONF_REMOTE_SENSORS,
                    default=[
                        v for v in current_remote_sensors if v in valid_sensor_values
                    ],
                )
            ] = SelectSelector(
                SelectSelectorConfig(
                    options=remote_sensor_opts,
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                )
            )

        if thermostat_opts:
            current_thermostats = self._config_entry.options.get(CONF_THERMOSTATS) or []
            valid_thermostats = {o["value"] for o in thermostat_opts}
            schema_dict[
                vol.Optional(
                    CONF_THERMOSTATS,
                    default=[t for t in current_thermostats if t in valid_thermostats],
                )
            ] = SelectSelector(
                SelectSelectorConfig(
                    options=thermostat_opts,
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                )
            )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema_dict),
        )
