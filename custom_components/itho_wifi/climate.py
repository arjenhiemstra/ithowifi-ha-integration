"""Climate platform for IthoWiFi: Itho Spider room thermostats.

The add-on reads a Spider's room temperature, setpoint and heat demand from its
RF broadcasts and sets the setpoint with W 01FF on the Spider's behalf (firmware
3.3+, ADR-0011). Each selected Spider is its own device under the add-on.
"""

from __future__ import annotations

from datetime import datetime
import logging
import time
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import IthoWiFiApi, IthoWiFiApiError, IthoWiFiConnectionError
from .const import (
    CONF_THERMOSTATS,
    DOMAIN,
    format_rf_uid,
    is_spider_thermostat,
    remote_rf_uid,
)
from .coordinator import IthoDeviceInfoCoordinator, IthoRemotesCoordinator

_LOGGER = logging.getLogger(__name__)

# A Spider that is awake gets the setpoint (W 01FF 3x, 1 s apart) right away;
# refresh after that so the confirmed value shows up.
_REFRESH_AFTER_SET = 9
# Otherwise the add-on delivers it at the Spider's next listen window: up to ~3 min for
# heater-bound Spiders, up to ~10 min for relay-bound ones (their relay poll). Keep showing
# the requested target until the Spider confirms it (or give up).
_PENDING_TIMEOUT = 900


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up climate entities for the Spider thermostats the user selected."""
    data = hass.data[DOMAIN][entry.entry_id]
    remotes_coord: IthoRemotesCoordinator | None = data.get("remotes_coordinator")
    selected = set(entry.options.get(CONF_THERMOSTATS, []))
    if remotes_coord is None or not selected:
        return
    async_add_entities(
        IthoSpiderClimate(remotes_coord, data["device_coordinator"], data["api"], uid)
        for uid in sorted(selected)
    )


class IthoSpiderClimate(CoordinatorEntity[IthoRemotesCoordinator], ClimateEntity, RestoreEntity):
    """An Itho Spider room thermostat, controlled through the add-on."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.HEAT]
    _attr_hvac_mode = HVACMode.HEAT
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_min_temp = 5.0
    _attr_max_temp = 30.0
    _attr_target_temperature_step = 0.5

    def __init__(
        self,
        coordinator: IthoRemotesCoordinator,
        device_info_coordinator: IthoDeviceInfoCoordinator,
        api: IthoWiFiApi,
        uid: str,
    ) -> None:
        """Initialize the thermostat entity for the Spider with RF id `uid`."""
        super().__init__(coordinator)
        self._device_info_coordinator = device_info_coordinator
        self._api = api
        self._uid = uid
        hwid = (device_info_coordinator.data or {}).get("add-on_hwid", "itho")
        self._hwid = hwid
        self._attr_unique_id = f"{hwid}_spider_{uid}"
        self._pending: float | None = None
        self._pending_until = 0.0
        # Last target seen, also restored across HA restarts: the add-on has no Spider data
        # until the Spider transmits, and HA hides the setpoint control without a target.
        self._last_target: float | None = None

    async def async_added_to_hass(self) -> None:
        """Restore the last known target temperature."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None:
            value = last.attributes.get(ATTR_TEMPERATURE)
            if isinstance(value, (int, float)):
                self._last_target = float(value)

    def _remote(self) -> dict[str, Any] | None:
        """Find this Spider's remote slot by RF id (slots may be reordered)."""
        for r in (self.coordinator.data or {}).get("rf", []):
            if remote_rf_uid(r) == self._uid:
                return r
        return None

    def _caps(self) -> dict[str, Any]:
        r = self._remote()
        caps = r.get("capabilities") if r else None
        return caps if isinstance(caps, dict) else {}

    @property
    def device_info(self) -> DeviceInfo:
        """Each Spider is its own device under the add-on."""
        r = self._remote() or {}
        name = r.get("name") or "Spider"
        return DeviceInfo(
            identifiers={(DOMAIN, self._attr_unique_id)},
            name=f"{name} {format_rf_uid(self._uid)}",
            manufacturer="Itho Daalderop",
            model="Spider room thermostat (via IthoWiFi RF)",
            via_device=(DOMAIN, self._hwid),
        )

    @property
    def available(self) -> bool:
        """Available while the add-on still has this Spider as a thermostat."""
        r = self._remote()
        return super().available and r is not None and is_spider_thermostat(r)

    @property
    def current_temperature(self) -> float | None:
        """Room temperature reported by the Spider."""
        return self._caps().get("temp")

    @property
    def target_temperature(self) -> float | None:
        """Setpoint reported by the Spider, else the one just requested or last sent/known."""
        caps = self._caps()
        reported = caps.get("setpoint")
        if self._pending is not None:
            if reported == self._pending or time.monotonic() > self._pending_until:
                self._pending = None
            else:
                return self._pending
        target = reported if reported is not None else caps.get("setpoint_cmd")
        if target is None:
            return self._last_target
        self._last_target = target
        return target

    @property
    def hvac_action(self) -> HVACAction | None:
        """Heating while the Spider asks its heater for heat."""
        demand = self._caps().get("demand")
        if demand is None:
            return None
        return HVACAction.HEATING if demand > 0 else HVACAction.IDLE

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Heat demand, schedule/manual flags and the RF id."""
        caps = self._caps()
        attrs: dict[str, Any] = {"rf_id": format_rf_uid(self._uid)}
        for key, name in (("demand", "heat_demand"), ("schedule", "schedule_active"), ("manual", "manually_adjusted")):
            if caps.get(key) is not None:
                attrs[name] = caps[key] if key == "demand" else bool(caps[key])
        return attrs

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Only heat is supported; the Spider itself decides when to heat."""
        if hvac_mode != HVACMode.HEAT:
            raise HomeAssistantError("Itho Spider only supports heat mode")

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Send a new setpoint to the Spider through the add-on."""
        target = kwargs.get(ATTR_TEMPERATURE)
        if target is None:
            return
        r = self._remote()
        if r is None:
            raise HomeAssistantError("Spider is no longer configured on the add-on")
        try:
            await self._api.set_rf_setpoint(r.get("index", 0), float(target))
        except (IthoWiFiApiError, IthoWiFiConnectionError) as err:
            raise HomeAssistantError(f"Setting the Spider setpoint failed: {err}") from err
        self._pending = round(float(target) * 2) / 2
        self._last_target = self._pending
        self._pending_until = time.monotonic() + _PENDING_TIMEOUT
        self.async_write_ha_state()
        async_call_later(self.hass, _REFRESH_AFTER_SET, self._async_refresh_later)

    async def _async_refresh_later(self, _now: datetime) -> None:
        await self.coordinator.async_request_refresh()
