"""Base entity for IthoWiFi integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import IthoDeviceInfoCoordinator, IthoStatusCoordinator


class IthoEntity(CoordinatorEntity[IthoStatusCoordinator]):
    """Base entity for IthoWiFi."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: IthoStatusCoordinator,
        device_info_coordinator: IthoDeviceInfoCoordinator,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._device_info_coordinator = device_info_coordinator

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        info = self._device_info_coordinator.data or {}
        itho_devtype = info.get("itho_devtype", "Unknown")
        itho_fw = info.get("itho_fwversion", "")
        itho_hw = info.get("itho_hwversion", "")
        addon_fw = info.get("add-on_fwversion", "")

        # Model line: show Itho device details
        itho_details = itho_devtype
        if itho_fw or itho_hw:
            parts = []
            if itho_fw:
                parts.append(
                    f"FW: {itho_fw} (0x{itho_fw:02X})" if isinstance(itho_fw, int)
                    else f"FW: {itho_fw}"
                )
            if itho_hw:
                parts.append(
                    f"HW: {itho_hw} (0x{itho_hw:02X})" if isinstance(itho_hw, int)
                    else f"HW: {itho_hw}"
                )
            itho_details += " — " + ", ".join(parts)

        return DeviceInfo(
            identifiers={(DOMAIN, info.get("add-on_hwid", "unknown"))},
            name=f"Itho {itho_devtype}",
            manufacturer=MANUFACTURER,
            model=f"WiFi add-on for {itho_details}",
            sw_version=addon_fw,
            configuration_url=f"http://{self.coordinator.api._host}",
        )
