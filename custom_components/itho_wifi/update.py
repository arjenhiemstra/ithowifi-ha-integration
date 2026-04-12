"""Update platform for IthoWiFi integration."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
import time
from typing import Any

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import (
    IthoWiFiApi,
    IthoWiFiApiError,
    IthoWiFiConnectionError,
    IthoWiFiNotFoundError,
)
from .const import DOMAIN, MANUFACTURER
from .coordinator import IthoDeviceInfoCoordinator

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(minutes=5)

_PRE_RELEASE_TAGS = ("-beta", "-rc", "-alpha", "-dev")

_RELEASE_URL_BASE = (
    "https://github.com/arjenhiemstra/ithowifi/releases/tag/Version-"
)


def _is_prerelease(version: str) -> bool:
    """Return True if the version string looks like a pre-release."""
    if not version:
        return False
    return any(tag in version for tag in _PRE_RELEASE_TAGS)


def _parse_version(version: str) -> tuple[list[int], str]:
    """Split a version string into numeric components and pre-release suffix.

    Mirrors the firmware's compareVersions semantics: a version with no
    pre-release suffix is considered newer than the same version with one.
    """
    if not version:
        return ([], "")
    core = version
    pre = ""
    for sep in ("-", "+"):
        if sep in core:
            core, pre = core.split(sep, 1)
            break
    nums: list[int] = []
    for part in core.split("."):
        try:
            nums.append(int(part))
        except ValueError:
            nums.append(0)
    return (nums, pre)


def _compare_versions(a: str, b: str) -> int:
    """Return 1 if a>b, -1 if a<b, 0 if equal."""
    a_nums, a_pre = _parse_version(a)
    b_nums, b_pre = _parse_version(b)
    for i in range(max(len(a_nums), len(b_nums))):
        av = a_nums[i] if i < len(a_nums) else 0
        bv = b_nums[i] if i < len(b_nums) else 0
        if av > bv:
            return 1
        if av < bv:
            return -1
    if a_pre == b_pre:
        return 0
    if not a_pre:
        return 1  # no pre-release > any pre-release
    if not b_pre:
        return -1
    if a_pre > b_pre:
        return 1
    return -1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the IthoWiFi update entity."""
    data = hass.data[DOMAIN][entry.entry_id]
    api: IthoWiFiApi = data["api"]
    device_coord: IthoDeviceInfoCoordinator = data["device_coordinator"]

    # Probe for /api/v2/ota before registering — older firmwares don't expose it.
    try:
        await api.get_ota()
    except IthoWiFiNotFoundError:
        _LOGGER.info(
            "Skipping update entity: /api/v2/ota not available on this firmware"
        )
        return
    except (IthoWiFiApiError, IthoWiFiConnectionError) as err:
        _LOGGER.warning(
            "Could not probe /api/v2/ota, skipping update entity: %s", err
        )
        return

    async_add_entities([IthoFirmwareUpdate(api, device_coord, data)])


class IthoFirmwareUpdate(UpdateEntity):
    """Firmware update entity for the IthoWiFi add-on."""

    _attr_has_entity_name = True
    _attr_name = "Firmware"
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_title = "Add-on Firmware"
    _attr_supported_features = (
        UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS
    )

    def __init__(
        self,
        api: IthoWiFiApi,
        device_info_coordinator: IthoDeviceInfoCoordinator,
        integration_data: dict[str, Any],
    ) -> None:
        """Initialize the update entity."""
        self._api = api
        self._device_info_coordinator = device_info_coordinator
        self._coordinators = [
            v for v in integration_data.values()
            if hasattr(v, "ota_in_progress")
        ]
        info = device_info_coordinator.data or {}
        hwid = info.get("add-on_hwid", "itho")
        self._attr_unique_id = f"{hwid}_firmware"
        self._installed: str | None = None
        self._latest_fw: str = ""
        self._latest_beta_fw: str = ""
        self._channel: str = "stable"
        self._install_task: asyncio.Task[None] | None = None

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info so this entity groups with the rest."""
        info = self._device_info_coordinator.data or {}
        return DeviceInfo(
            identifiers={(DOMAIN, info.get("add-on_hwid", "unknown"))},
            manufacturer=MANUFACTURER,
        )

    @property
    def entity_picture(self) -> str | None:
        """Use the device favicon to match the firmware's web UI."""
        return f"http://{self._api._host}/favicon.png"

    @property
    def installed_version(self) -> str | None:
        """Return the currently installed firmware version."""
        return self._installed

    @property
    def latest_version(self) -> str | None:
        """Return the latest available firmware version for the active channel.

        Channel is auto-selected from the installed version: a pre-release
        installed version pins the entity to the beta channel; otherwise stable.
        If no newer version is available for the active channel, return the
        installed version so HA's update entity stays in the 'off' state.
        """
        if self._installed is None:
            return None

        target = (
            self._latest_beta_fw if self._channel == "beta" else self._latest_fw
        )
        if not target:
            return self._installed
        if _compare_versions(target, self._installed) <= 0:
            return self._installed
        return target

    @property
    def release_url(self) -> str | None:
        """Return the GitHub release tag URL for the latest version."""
        latest = self.latest_version
        if not latest or latest == self._installed:
            return None
        return f"{_RELEASE_URL_BASE}{latest}"

    async def async_update(self) -> None:
        """Poll the device for firmware/OTA status.

        Skipped while an install is in progress — the install task manages
        state directly.
        """
        if self._install_task is not None and not self._install_task.done():
            return
        try:
            ota = await self._api.get_ota()
        except IthoWiFiNotFoundError:
            # Firmware no longer supports the endpoint (e.g. downgraded).
            # Mark unavailable so HA doesn't keep showing stale data.
            self._attr_available = False
            return
        except (IthoWiFiApiError, IthoWiFiConnectionError) as err:
            _LOGGER.debug("Failed to fetch OTA status: %s", err)
            return
        self._attr_available = True
        self._apply_ota_data(ota)

    def _apply_ota_data(self, ota: dict[str, Any]) -> None:
        """Apply a /api/v2/ota response to entity state."""
        installed = ota.get("installed_version") or None
        if installed:
            self._installed = installed
            self._channel = "beta" if _is_prerelease(installed) else "stable"
        self._latest_fw = ota.get("latest_fw") or ""
        self._latest_beta_fw = ota.get("latest_beta_fw") or ""

    def _set_coordinators_ota_flag(self, in_progress: bool) -> None:
        """Tell all coordinators to pause/resume polling during OTA."""
        for coord in self._coordinators:
            coord.ota_in_progress = in_progress

    async def async_install(
        self,
        version: str | None,
        backup: bool,
        **kwargs: Any,
    ) -> None:
        """Start a firmware install on the active channel."""
        if self._install_task is not None and not self._install_task.done():
            raise HomeAssistantError("An update is already in progress")

        try:
            await self._api.start_ota(self._channel)
        except (IthoWiFiApiError, IthoWiFiConnectionError) as err:
            raise HomeAssistantError(
                f"Failed to start firmware update: {err}"
            ) from err

        self._set_coordinators_ota_flag(True)
        self._attr_in_progress = True
        self._attr_update_percentage = 0
        self.async_write_ha_state()
        self._install_task = self.hass.async_create_task(self._install_monitor())

    async def _install_monitor(self) -> None:
        """Poll /api/v2/ota during an install and update entity state.

        The device will reboot when the flash completes; during that window
        polls will fail. We tolerate consecutive failures up to a timeout
        before giving up, and treat a return to 'idle' after a prior active
        state as a successful completion.
        """
        poll_interval = 2.0
        idle_timeout_after_active = 30.0
        hard_timeout = 900.0  # 15 min absolute ceiling
        consecutive_failures_limit = 150  # ~5 min of failed polls during reboot

        started = time.monotonic()
        seen_active = False
        idle_since: float | None = None
        failures = 0

        try:
            while True:
                await asyncio.sleep(poll_interval)

                now = time.monotonic()
                if now - started > hard_timeout:
                    _LOGGER.error("Firmware update timed out")
                    break

                try:
                    ota = await self._api.get_ota()
                except IthoWiFiNotFoundError:
                    # New firmware doesn't expose /api/v2/ota — treat as
                    # successful completion. Device info refresh in finally
                    # will pick up the new installed_version.
                    _LOGGER.info(
                        "Update endpoint not available after install — "
                        "assuming installed firmware does not support OTA tracking"
                    )
                    break
                except (IthoWiFiApiError, IthoWiFiConnectionError):
                    failures += 1
                    if failures > consecutive_failures_limit:
                        _LOGGER.error(
                            "Device unreachable after firmware update started"
                        )
                        break
                    continue

                failures = 0
                self._apply_ota_data(ota)

                state = ota.get("state", "idle")
                progress = ota.get("progress", 0)

                if state == "error":
                    _LOGGER.error("Firmware update reported error")
                    break

                if state in ("downloading", "starting"):
                    seen_active = True
                    idle_since = None
                    self._attr_in_progress = True
                    self._attr_update_percentage = progress
                    self.async_write_ha_state()
                    continue

                if state == "done":
                    seen_active = True
                    self._attr_in_progress = True
                    self._attr_update_percentage = 100
                    self.async_write_ha_state()
                    continue

                if state == "idle":
                    if not seen_active:
                        continue
                    if idle_since is None:
                        idle_since = now
                    if now - idle_since >= idle_timeout_after_active:
                        break
        finally:
            self._set_coordinators_ota_flag(False)
            self._attr_in_progress = False
            self._attr_update_percentage = None
            try:
                await self._device_info_coordinator.async_request_refresh()
            except Exception:  # noqa: BLE001
                pass
            try:
                ota = await self._api.get_ota()
                self._apply_ota_data(ota)
            except IthoWiFiNotFoundError:
                self._attr_available = False
            except (IthoWiFiApiError, IthoWiFiConnectionError):
                pass
            self.async_write_ha_state()
