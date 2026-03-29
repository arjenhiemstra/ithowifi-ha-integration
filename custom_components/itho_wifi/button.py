"""Button platform for IthoWiFi integration."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import COOK_DEVICE_TYPES, COOK_PRESETS, DOMAIN, TIMER_PRESETS
from .coordinator import IthoDeviceInfoCoordinator, IthoStatusCoordinator
from .entity import IthoEntity


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

    # Timer preset buttons
    presets = {**TIMER_PRESETS}
    if any(dt in device_type for dt in COOK_DEVICE_TYPES):
        presets.update(COOK_PRESETS)

    for cmd, label in presets.items():
        entities.append(
            IthoCommandButton(
                status_coord,
                device_coord,
                ButtonEntityDescription(
                    key=cmd,
                    name=label,
                    icon="mdi:timer-outline",
                ),
            )
        )

    # Reboot button
    entities.append(IthoRebootButton(status_coord, device_coord))

    async_add_entities(entities)


class IthoCommandButton(IthoEntity, ButtonEntity):
    """Button that sends a command to the Itho device."""

    def __init__(
        self,
        coordinator: IthoStatusCoordinator,
        device_info_coordinator: IthoDeviceInfoCoordinator,
        description: ButtonEntityDescription,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator, device_info_coordinator)
        self.entity_description = description
        info = device_info_coordinator.data or {}
        self._attr_unique_id = (
            f"{info.get('add-on_hwid', 'itho')}_{description.key}"
        )

    async def async_press(self) -> None:
        """Handle the button press."""
        if self.coordinator.rf_standalone:
            await self.coordinator.api.send_rf_command(
                self.entity_description.key
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
