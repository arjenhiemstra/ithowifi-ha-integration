"""Button platform for IthoWiFi integration."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    COOK_DEVICE_TYPES,
    COOK_PRESETS,
    DOMAIN,
    MANUFACTURER,
    TIMER_PRESETS,
    is_demandflow_device,
    is_fan_device,
)
from .coordinator import (
    IthoDeviceInfoCoordinator,
    IthoRemotesCoordinator,
    IthoStatusCoordinator,
)
from .entity import IthoEntity
from .fan import pick_main_fan_rf_index


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up IthoWiFi buttons."""
    data = hass.data[DOMAIN][entry.entry_id]
    status_coord: IthoStatusCoordinator = data["status_coordinator"]
    device_coord: IthoDeviceInfoCoordinator = data["device_coordinator"]

    entities: list[ButtonEntity] = []
    device_type = (device_coord.data or {}).get("itho_devtype", "")

    # Preset command buttons. Only meaningful for fan/ventilation devices;
    # Heatpump / AutoTemp have no fan presets at all.
    #
    # Speed + mode presets (low / medium / high / auto / autonight) are
    # present for every fan-capable device except DemandFlow (low / high
    # only, see below). The firmware
    # (ithoExecCommand) handles the routing — vremote 0 dispatch when
    # itho_vremoteapi=1, PWM2I2C speed otherwise. For device types where
    # auto / autonight don't map to a distinct state, the firmware silently
    # aliases them to medium (pure PWM2I2C path).
    #
    # Timer buttons (timer1 / 2 / 3) are always present.
    # Cook buttons (cook30 / cook60) are gated on QualityFlow / DemandFlow.
    if is_fan_device(device_type):
        # cmd -> (friendly label, icon)
        preset_buttons: dict[str, tuple[str, str]] = {
            "low": ("Low", "mdi:fan-speed-1"),
            "medium": ("Medium", "mdi:fan-speed-2"),
            "high": ("High", "mdi:fan-speed-3"),
            "auto": ("Auto", "mdi:fan-auto"),
            "autonight": ("Auto night", "mdi:weather-night"),
        }
        if is_demandflow_device(device_type):
            # A DemandFlow is driven through a DF/QF remote, which has no medium,
            # auto or autonight. Drop the buttons an older version created.
            hwid = (device_coord.data or {}).get("add-on_hwid", "itho")
            registry = er.async_get(hass)
            for cmd in ("medium", "auto", "autonight"):
                del preset_buttons[cmd]
                entity_id = registry.async_get_entity_id("button", DOMAIN, f"{hwid}_{cmd}")
                if entity_id:
                    registry.async_remove(entity_id)
        for cmd, label in TIMER_PRESETS.items():
            preset_buttons[cmd] = (label, "mdi:timer-outline")
        if any(dt in device_type for dt in COOK_DEVICE_TYPES):
            for cmd, label in COOK_PRESETS.items():
                preset_buttons[cmd] = (label, "mdi:pot-steam-outline")

        remotes_coord_for_buttons = data.get("remotes_coordinator")
        for cmd, (label, icon) in preset_buttons.items():
            entities.append(
                IthoCommandButton(
                    status_coord,
                    device_coord,
                    ButtonEntityDescription(
                        key=cmd,
                        name=label,
                        icon=icon,
                    ),
                    remotes_coordinator=remotes_coord_for_buttons,
                )
            )

    # Reboot button — always available regardless of device type.
    entities.append(IthoRebootButton(status_coord, device_coord))

    # Rescan remotes button — forces a refresh of the remotes coordinator
    # so a newly-added/renamed/removed remote is reflected immediately.
    remotes_coord = data.get("remotes_coordinator")
    if remotes_coord is not None:
        entities.append(IthoRescanRemotesButton(device_coord, remotes_coord))

    async_add_entities(entities)


class IthoCommandButton(IthoEntity, ButtonEntity):
    """Button that sends a command to the Itho device."""

    def __init__(
        self,
        coordinator: IthoStatusCoordinator,
        device_info_coordinator: IthoDeviceInfoCoordinator,
        description: ButtonEntityDescription,
        remotes_coordinator: IthoRemotesCoordinator | None = None,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator, device_info_coordinator)
        self.entity_description = description
        self._remotes_coordinator = remotes_coordinator
        info = device_info_coordinator.data or {}
        self._attr_unique_id = (
            f"{info.get('add-on_hwid', 'itho')}_{description.key}"
        )

    async def async_press(self) -> None:
        """Handle the button press."""
        if self.coordinator.use_rf_commands:
            # In RF mode, dispatch to the first configured SEND remote
            # rather than the hardcoded default of index 0 — avoids
            # spoofing a RECEIVE remote's ID on setups where index 0 is
            # a receive slot.
            idx = (
                pick_main_fan_rf_index(self._remotes_coordinator)
                if self._remotes_coordinator is not None
                else 0
            )
            await self.coordinator.api.send_rf_command(
                self.entity_description.key, idx
            )
        else:
            await self.coordinator.api.send_command(
                self.entity_description.key
            )
        await self.coordinator.async_request_refresh()


class IthoRebootButton(IthoEntity, ButtonEntity):
    """Button to reboot the IthoWiFi add-on."""

    _attr_name = "Reboot"
    _attr_icon = "mdi:restart"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: IthoStatusCoordinator,
        device_info_coordinator: IthoDeviceInfoCoordinator,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator, device_info_coordinator)
        info = device_info_coordinator.data or {}
        self._attr_unique_id = f"{info.get('add-on_hwid', 'itho')}_reboot"

    async def async_press(self) -> None:
        """Handle the button press."""
        await self.coordinator.api.reboot()


class IthoRescanRemotesButton(ButtonEntity):
    """Button that forces a refresh of the per-remote coordinator.

    Pressing this triggers an immediate re-fetch of /api/v2/remotes and
    /api/v2/vremotes so any added/renamed/removed remote slot is
    reflected in per-remote fan entity names and availability without
    waiting for the 30s polling interval.
    """

    _attr_has_entity_name = True
    _attr_name = "Rescan remotes"
    _attr_icon = "mdi:refresh"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        device_info_coordinator: IthoDeviceInfoCoordinator,
        remotes_coordinator: IthoRemotesCoordinator,
    ) -> None:
        """Initialize the rescan button."""
        self._device_info_coordinator = device_info_coordinator
        self._remotes_coordinator = remotes_coordinator
        info = device_info_coordinator.data or {}
        self._attr_unique_id = f"{info.get('add-on_hwid', 'itho')}_rescan_remotes"

    @property
    def device_info(self) -> DeviceInfo:
        """Group under the main IthoWiFi device."""
        info = self._device_info_coordinator.data or {}
        return DeviceInfo(
            identifiers={(DOMAIN, info.get("add-on_hwid", "unknown"))},
            manufacturer=MANUFACTURER,
        )

    async def async_press(self) -> None:
        """Trigger an immediate remotes coordinator refresh."""
        await self._remotes_coordinator.async_request_refresh()
