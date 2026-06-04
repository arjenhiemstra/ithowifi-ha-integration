"""Fan platform for IthoWiFi integration."""

from __future__ import annotations

import asyncio
import math
from typing import Any

from homeassistant.components.fan import (
    FanEntity,
    FanEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_REMOTE_FANS,
    DOMAIN,
    MANUFACTURER,
    PRESET_AUTO,
    PRESET_AUTONIGHT,
    PRESET_AWAY,
    PRESET_HIGH,
    PRESET_LOW,
    PRESET_MEDIUM,
    is_demandflow_device,
    is_fan_device,
)
from .coordinator import (
    IthoDeviceInfoCoordinator,
    IthoRemotesCoordinator,
    IthoStatusCoordinator,
)
from .entity import IthoEntity

PRESET_MODES = [
    PRESET_LOW,
    PRESET_MEDIUM,
    PRESET_HIGH,
    PRESET_AUTO,
    PRESET_AUTONIGHT,
    PRESET_AWAY,
]

# Persistent preset commands that map to a stable device state. Timer and cook
# presets are one-shot and handled separately (see _TIMER_LIKE_PRESETS).
_PERSISTENT_PRESETS: set[str] = {
    PRESET_LOW,
    PRESET_MEDIUM,
    PRESET_HIGH,
    PRESET_AUTO,
    PRESET_AUTONIGHT,
    PRESET_AWAY,
}

# One-shot preset commands: exist in `preset_modes` so the fan entity can
# reflect an active timer, but the firmware may clear them once the device
# returns to its prior state. Gated on ithostatus "RemainingTime (min)".
_TIMER_LIKE_PRESETS: set[str] = {
    "timer1",
    "timer2",
    "timer3",
    "cook30",
    "cook60",
}

# Percentage mapping for preset-driven per-remote fans. Fixed buckets since
# per-remote commands are preset-only (low/medium/high/auto/autonight/away),
# not variable-speed. "auto"/"autonight" map to None so HA doesn't show a
# misleading percentage.
_PRESET_TO_PERCENTAGE: dict[str, int | None] = {
    PRESET_LOW: 33,
    PRESET_MEDIUM: 66,
    PRESET_HIGH: 100,
    PRESET_AWAY: 0,
    PRESET_AUTO: None,
    PRESET_AUTONIGHT: None,
}


def _parse_remote_fans(selection: list[str]) -> list[tuple[str, int]]:
    """Parse the CONF_REMOTE_FANS option into (kind, index) tuples.

    Each entry in `selection` is a string like "vr:3" or "rf:0".
    """
    parsed: list[tuple[str, int]] = []
    for entry in selection:
        try:
            kind, idx_str = entry.split(":", 1)
            if kind not in ("vr", "rf"):
                continue
            parsed.append((kind, int(idx_str)))
        except (ValueError, AttributeError):
            continue
    return parsed


def pick_main_fan_rf_index(remotes_coordinator: IthoRemotesCoordinator) -> int:
    """Return the RF remote index used for main-fan RF dispatch.

    Picks the first non-empty SEND remote (remfunc == 5) from the
    remotes coordinator's latest data. This is the remote the user
    explicitly configured to control the Itho unit — avoids the prior
    behavior of always using index 0, which on some setups points to a
    RECEIVE remote that isn't meant to transmit. Falls back to 0 if the
    coordinator has no data yet or no SEND remote is configured.
    """
    data = remotes_coordinator.data or {}
    for r in data.get("rf", []):
        if r.get("remfunc") != 5:  # SEND
            continue
        rid = r.get("id") or [0, 0, 0]
        if all(b == 0 for b in rid[:3]):
            continue
        return int(r.get("index", 0))
    return 0


def _is_empty_slot(remote: dict[str, Any]) -> bool:
    """Match the firmware's IthoRemote::isEmptySlot() check (all ID bytes zero)."""
    remid = remote.get("id") or [0, 0, 0]
    return all(b == 0 for b in remid[:3])


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the IthoWiFi fan entities.

    Creates the main `IthoFan` for traditional ventilation devices (CVE /
    HRU / QualityFlow) unless the device is a DemandFlow, and also creates
    per-remote `IthoRemoteFan` entities for every (kind, index) listed in
    the CONF_REMOTE_FANS option.
    """
    data = hass.data[DOMAIN][entry.entry_id]
    device_coord = data["device_coordinator"]
    devtype = (device_coord.data or {}).get("itho_devtype")

    entities: list[FanEntity] = []

    # Main fan: skip for non-fan devices (WPU/AutoTemp) and for DemandFlow
    # (which uses per-remote fans exclusively).
    if is_fan_device(devtype) and not is_demandflow_device(devtype):
        entities.append(
            IthoFan(
                data["status_coordinator"],
                device_coord,
                data["remotes_coordinator"],
            )
        )

    # Per-remote fans: gated by the CONF_REMOTE_FANS option. For DemandFlow
    # devices the default is all-configured-remotes-on (set by the options
    # flow the first time it runs); for other devices the default is empty
    # (opt-in). If the option isn't set yet and the device is DemandFlow,
    # fall back to "all configured remotes" so first-boot DemandFlow users
    # see their remotes without having to open the options flow first.
    remote_fans_opt: list[str] = entry.options.get(CONF_REMOTE_FANS, [])
    remotes_coord: IthoRemotesCoordinator = data["remotes_coordinator"]

    if not remote_fans_opt and is_demandflow_device(devtype):
        remote_fans_opt = _default_demandflow_remotes(remotes_coord.data or {})

    selection = _parse_remote_fans(remote_fans_opt)
    status_coord: IthoStatusCoordinator = data["status_coordinator"]
    for kind, index in selection:
        entities.append(
            IthoRemoteFan(
                remotes_coord,
                device_coord,
                status_coord,
                kind=kind,
                index=index,
            )
        )

    if entities:
        async_add_entities(entities)


def _default_demandflow_remotes(remotes_data: dict[str, Any]) -> list[str]:
    """Return a default CONF_REMOTE_FANS list for DemandFlow first-boot.

    Picks every non-empty virtual remote slot and every non-empty RF remote
    slot that is a SEND remote (remfunc == 5).
    """
    default: list[str] = []
    for vr in remotes_data.get("vr", []):
        if _is_empty_slot(vr):
            continue
        default.append(f"vr:{vr.get('index', 0)}")
    for rf in remotes_data.get("rf", []):
        if _is_empty_slot(rf):
            continue
        if rf.get("remfunc") != 5:  # SEND
            continue
        default.append(f"rf:{rf.get('index', 0)}")
    return default


class IthoFan(IthoEntity, FanEntity):
    """Representation of an Itho ventilation fan."""

    _attr_name = None  # Use device name
    _attr_unique_id_suffix = "fan"
    _attr_supported_features = (
        FanEntityFeature.SET_SPEED
        | FanEntityFeature.PRESET_MODE
        | FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF
    )
    _attr_speed_count = 100
    _attr_preset_modes = PRESET_MODES

    def __init__(
        self,
        coordinator: IthoStatusCoordinator,
        device_info_coordinator: IthoDeviceInfoCoordinator,
        remotes_coordinator: IthoRemotesCoordinator,
    ) -> None:
        """Initialize the fan."""
        super().__init__(coordinator, device_info_coordinator)
        self._remotes_coordinator = remotes_coordinator
        info = device_info_coordinator.data or {}
        self._attr_unique_id = f"{info.get('add-on_hwid', 'itho')}_{self._attr_unique_id_suffix}"

    @property
    def is_on(self) -> bool:
        """Return true if the fan is on."""
        pct = self.percentage
        return pct is not None and pct > 0

    @property
    def percentage(self) -> int | None:
        """Return the current speed percentage."""
        if self.coordinator.data is None:
            return None
        # Try Speed status from ithostatus (works for both RF standalone and
        # hybrid I2C+RF mode where currentspeed is 0)
        status = self.coordinator.data.get("status", {})
        val = status.get("Speed status")
        if val is not None and val != "not available":
            return min(round(float(val)), 100)
        # Fall back to currentspeed from /api/v2/speed
        speed = self.coordinator.data.get("speed", {}).get("currentspeed")
        if speed is None:
            return None
        return min(round(speed / 2.55), 100)

    @property
    def _use_rf_commands(self) -> bool:
        """Return True if commands should use RF (standalone or RF CO2 mode)."""
        return self.coordinator.use_rf_commands

    async def _async_refresh(self) -> None:
        """Refresh after command — delay in RF mode for data to arrive."""
        if self._use_rf_commands:
            await asyncio.sleep(5)
        await self.coordinator.async_request_refresh()

    def _rf_index(self) -> int:
        """Return the RF remote index used for main-fan RF dispatch."""
        return pick_main_fan_rf_index(self._remotes_coordinator)

    def _fan_in_auto(self) -> bool:
        """Return True only if we can confirm the unit is currently in auto mode.

        Returns False both when the unit is in a fixed mode (low/medium/high/
        timer/away/...) and when we have no FanInfo data (e.g. RF standalone
        without an rf_source configured). Used to decide whether to precede
        the 31E0 demand frame with an "auto" RF command — the unit only
        accepts demand frames when in auto mode, so we send "auto" first
        when not confirmed-auto, but skip it when confirmed-auto to avoid
        the boost-mode side-effect that ignores subsequent lower demands.
        """
        if not self.coordinator.data:
            return False
        status = self.coordinator.data.get("status") or {}

        # I2C 31DA path: ithostatus dict with "FanInfo" as a top-level key.
        fi = status.get("FanInfo") or status.get("fan-info")
        if fi:
            return str(fi).strip().lower() == "auto"

        # rfstatus path: sources -> measurements31DA -> {name, value}.
        for src in status.get("sources", []) or []:
            for m in src.get("measurements31DA", []) or []:
                if m.get("name") in ("FanInfo", "fan-info"):
                    v = m.get("value")
                    if v is not None:
                        return str(v).strip().lower() == "auto"

        return False

    async def async_set_percentage(self, percentage: int) -> None:
        """Set the speed percentage.

        Dispatch follows the same shape as async_set_preset_mode:
        * use_rf_commands → 31E0 demand frame, optionally preceded by an
          "auto" RF command when the unit isn't already in auto mode (the
          unit only accepts demand frames when in auto; sending "auto"
          when already in auto produces a boost-mode side-effect that
          drops subsequent lower demand values).
        * otherwise → direct PWM speed write via /api/v2/command. On
          PWM2I2C-only units (no RFT CO2 SEND remote configured) the
          RF path would only spam firmware error logs and silently fail.
        """
        if self._use_rf_commands:
            try:
                idx = self._rf_index()
                demand = percentage * 2  # 0-100% → 0-200 demand
                if not self._fan_in_auto():
                    await self.coordinator.api.send_rf_command("auto", idx)
                await self.coordinator.api.send_rf_demand(demand, index=idx)
            except Exception:
                speed = math.ceil(percentage * 2.55)
                await self.coordinator.api.set_speed(speed)
        else:
            speed = math.ceil(percentage * 2.55)
            await self.coordinator.api.set_speed(speed)
        await self._async_refresh()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set the preset mode."""
        if self._use_rf_commands:
            await self.coordinator.api.send_rf_command(preset_mode, self._rf_index())
        else:
            await self.coordinator.api.send_command(preset_mode)
        await self._async_refresh()

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Turn on the fan."""
        if preset_mode:
            await self.async_set_preset_mode(preset_mode)
        elif percentage is not None:
            await self.async_set_percentage(percentage)
        else:
            if self._use_rf_commands:
                await self.coordinator.api.send_rf_command("medium", self._rf_index())
            else:
                await self.coordinator.api.send_command("medium")
            await self._async_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the fan."""
        if self._use_rf_commands:
            await self.coordinator.api.send_rf_command("low", self._rf_index())
        else:
            await self.coordinator.api.set_speed(0)
        await self._async_refresh()


class IthoRemoteFan(CoordinatorEntity[IthoRemotesCoordinator], FanEntity):
    """A fan entity representing a single configured remote (RF or virtual).

    Command dispatch is per-remote (POST /api/v2/vremote or
    /api/v2/rfremote/command with the remote's index); state is read from
    the remote's `last_cmd` field exposed by firmware ≥3.1.0-beta3.

    preset_modes is sourced dynamically from the firmware's per-remote
    `presets` field (3.1.0-beta4+), which knows the exact subset of
    commands each remote type supports. On older firmware a conservative
    fallback set is used.
    """

    _attr_has_entity_name = True
    _attr_supported_features = (
        FanEntityFeature.PRESET_MODE
        | FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF
    )

    # Conservative default if firmware pre-3.1.0-beta4 doesn't expose the
    # per-remote `presets` field.
    _FALLBACK_PRESETS: tuple[str, ...] = (
        PRESET_LOW,
        PRESET_MEDIUM,
        PRESET_HIGH,
    )

    def __init__(
        self,
        coordinator: IthoRemotesCoordinator,
        device_info_coordinator: IthoDeviceInfoCoordinator,
        status_coordinator: IthoStatusCoordinator,
        *,
        kind: str,
        index: int,
    ) -> None:
        """Initialize a per-remote fan entity."""
        super().__init__(coordinator)
        self._device_info_coordinator = device_info_coordinator
        self._status_coordinator = status_coordinator
        self._kind = kind  # "vr" or "rf"
        self._index = index

        info = device_info_coordinator.data or {}
        hwid = info.get("add-on_hwid", "itho")
        self._attr_unique_id = f"{hwid}_{kind}_{index}_fan"
        # Name is set dynamically from the remote's current `name` field
        # in the coordinator data. Fall back to a kind+index label if the
        # slot has been cleared since setup.
        self._attr_name = self._make_name()

    def _remote_data(self) -> dict[str, Any] | None:
        """Find this entity's remote in the coordinator's latest data."""
        if self.coordinator.data is None:
            return None
        bucket = self.coordinator.data.get("vr" if self._kind == "vr" else "rf", [])
        for r in bucket:
            if r.get("index") == self._index:
                return r
        return None

    def _make_name(self) -> str:
        """Compute the entity friendly name from the remote's `name` field."""
        r = self._remote_data()
        label = "Virtual Remote" if self._kind == "vr" else "RF Remote"
        if r is None:
            return f"{label}: {self._index}"
        name = r.get("name") or f"{self._index}"
        return f"{label}: {name}"

    @property
    def device_info(self) -> DeviceInfo:
        """Group this entity under the main IthoWiFi device."""
        info = self._device_info_coordinator.data or {}
        return DeviceInfo(
            identifiers={(DOMAIN, info.get("add-on_hwid", "unknown"))},
            manufacturer=MANUFACTURER,
        )

    @property
    def available(self) -> bool:
        """Entity is available while the remote slot still exists."""
        if not super().available:
            return False
        r = self._remote_data()
        return r is not None and not _is_empty_slot(r)

    @property
    def preset_modes(self) -> list[str] | None:
        """Preset modes this remote type supports.

        Sourced from the firmware's `presets` field on each remote
        (3.1.0-beta4+). Falls back to low/medium/high if the field is
        missing (older firmware). Only commands the integration knows
        how to render are exposed.
        """
        r = self._remote_data()
        if r is None:
            return list(self._FALLBACK_PRESETS)
        raw = r.get("presets")
        if not raw:
            return list(self._FALLBACK_PRESETS)
        # Split and keep only entries in our known preset vocabulary.
        allowed = _PERSISTENT_PRESETS | _TIMER_LIKE_PRESETS
        return [p.strip() for p in raw.split(",") if p.strip() in allowed]

    @property
    def preset_mode(self) -> str | None:
        """Return the preset most recently dispatched via this remote.

        For persistent presets (low/medium/high/auto/autonight/away) the
        value is returned directly. For timer/cook presets we gate on
        ithostatus "RemainingTime (min)" — if the status coordinator
        reports a positive remaining time, the timer is still active and
        we show it; otherwise the preset clears to None.
        """
        r = self._remote_data()
        if r is None:
            return None
        last = r.get("last_cmd")
        if not last:
            return None

        # Persistent preset — show directly if our vocabulary recognises it
        # and it's in the remote type's preset list.
        modes = self.preset_modes or []
        if last in _PERSISTENT_PRESETS:
            return last if last in modes else None

        # Timer / cook — only show while the device still reports a
        # positive RemainingTime.
        if last in _TIMER_LIKE_PRESETS:
            remaining = self._remaining_time_minutes()
            if remaining is None:
                # Device doesn't expose RemainingTime — immediate clear.
                return None
            if remaining > 0:
                return last if last in modes else None
            return None

        return None

    def _remaining_time_minutes(self) -> float | None:
        """Look up RemainingTime (min) from the ithostatus coordinator.

        Returns None if the field is absent or reported as 'not available',
        meaning the device doesn't track an active timer via status.
        """
        data = self._status_coordinator.data or {}
        status = data.get("status", {})
        val = status.get("RemainingTime (min)")
        if val is None or val == "not available":
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    @property
    def percentage(self) -> int | None:
        """Map the current preset to a coarse percentage."""
        mode = self.preset_mode
        if mode is None:
            return None
        return _PRESET_TO_PERCENTAGE.get(mode)

    @property
    def is_on(self) -> bool | None:
        """True if the last dispatched preset is anything other than 'away'.

        Returns None when the remote has never been used via this add-on
        so HA shows 'unknown' instead of falsely claiming 'off'.
        """
        mode = self.preset_mode
        if mode is None:
            return None
        return mode != PRESET_AWAY

    @property
    def speed_count(self) -> int:
        """Preset-only entity: one step per preset with a percentage."""
        return 3

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Dispatch a preset command to this specific remote."""
        api = self.coordinator.api
        if self._kind == "vr":
            await api.send_vremote_command(preset_mode, self._index)
        else:
            await api.send_rf_command(preset_mode, self._index)
        # Nudge the coordinator: last_cmd updates immediately in the
        # firmware, so a quick refresh reflects the new state.
        await self.coordinator.async_request_refresh()

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Turn on via preset, mapping percentage if needed.

        Picks a target preset that the remote type actually supports —
        e.g. an RFT CO2 remote has no `medium`, so a bare "turn on" falls
        back to `auto`, and a bare percentage=50 picks `auto` instead of
        an unsupported `medium`.
        """
        modes = self.preset_modes or []

        if preset_mode:
            await self.async_set_preset_mode(preset_mode)
            return

        # Map percentage → tiered preset if provided.
        if percentage is not None:
            if percentage <= 40:
                candidates = (PRESET_LOW, PRESET_AWAY, PRESET_MEDIUM, PRESET_AUTO, PRESET_HIGH)
            elif percentage <= 80:
                candidates = (PRESET_MEDIUM, PRESET_AUTO, PRESET_HIGH, PRESET_LOW)
            else:
                candidates = (PRESET_HIGH, PRESET_MEDIUM, PRESET_AUTO, PRESET_LOW)
        else:
            # Bare "turn on" — prefer medium, fall back through common defaults.
            candidates = (PRESET_MEDIUM, PRESET_AUTO, PRESET_HIGH, PRESET_LOW)

        target: str | None = None
        for c in candidates:
            if c in modes:
                target = c
                break
        if target is None:
            return
        await self.async_set_preset_mode(target)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off: prefer 'away', fall back to 'low'.

        Remote types vary — some (ORCON15LF01) expose 'away' as the
        off-equivalent, others (RFT CVE / RFT Auto / DemandFlow) don't
        and 'low' is the lowest available state.
        """
        modes = self.preset_modes or []
        target = PRESET_AWAY if PRESET_AWAY in modes else PRESET_LOW
        if target not in modes:
            # Nothing off-like available for this remote type — no-op.
            return
        await self.async_set_preset_mode(target)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the underlying remote metadata for debugging."""
        r = self._remote_data() or {}
        return {
            "remote_kind": self._kind,
            "remote_index": self._index,
            "remote_name": r.get("name"),
            "remote_type": r.get("remtypename"),
            "remote_function": r.get("remfuncname"),
            "last_cmd": r.get("last_cmd"),
        }

    def _handle_coordinator_update(self) -> None:
        """Refresh cached name on every coordinator update."""
        self._attr_name = self._make_name()
        super()._handle_coordinator_update()
