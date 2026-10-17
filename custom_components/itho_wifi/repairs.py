"""Repairs for IthoWiFi: offer newly found Itho Spider thermostats as climate entities."""

from __future__ import annotations

from typing import Any

from homeassistant import data_entry_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import (
    CONF_THERMOSTATS,
    DOMAIN,
    format_rf_uid,
    is_spider_thermostat,
    remote_rf_uid,
)

ISSUE_SPIDER_FOUND = "spider_found"


def _issue_id(entry: ConfigEntry, uid: str) -> str:
    return f"{ISSUE_SPIDER_FOUND}_{entry.entry_id}_{uid}"


@callback
def async_check_spider_issues(hass: HomeAssistant, entry: ConfigEntry, remotes: dict[str, Any] | None) -> None:
    """Raise a fixable issue per Spider thermostat the user has not added yet.

    Declining is HA's own "Ignore" on the issue, so the user is asked once.
    """
    selected = set(entry.options.get(CONF_THERMOSTATS, []))
    for r in (remotes or {}).get("rf", []):
        if not is_spider_thermostat(r):
            continue
        uid = remote_rf_uid(r)
        issue_id = _issue_id(entry, uid)
        if uid in selected:
            ir.async_delete_issue(hass, DOMAIN, issue_id)
            continue
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=True,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_SPIDER_FOUND,
            translation_placeholders={"rf_id": format_rf_uid(uid), "device": entry.title},
            data={"entry_id": entry.entry_id, "uid": uid},
        )


class SpiderThermostatRepairFlow(RepairsFlow):
    """Ask whether to add a found Spider as a climate entity."""

    def __init__(self, entry_id: str, uid: str) -> None:
        """Initialize the flow."""
        self._entry_id = entry_id
        self._uid = uid

    async def async_step_init(self, user_input: dict[str, str] | None = None) -> data_entry_flow.FlowResult:
        """Start with the confirmation step."""
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, str] | None = None) -> data_entry_flow.FlowResult:
        """Add the Spider to the selected thermostats; the entry reloads on the options change."""
        if user_input is not None:
            entry = self.hass.config_entries.async_get_entry(self._entry_id)
            if entry is not None:
                selected = list(entry.options.get(CONF_THERMOSTATS, []))
                if self._uid not in selected:
                    selected.append(self._uid)
                    self.hass.config_entries.async_update_entry(
                        entry, options={**entry.options, CONF_THERMOSTATS: selected}
                    )
            return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={"rf_id": format_rf_uid(self._uid)},
        )


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create the fix flow for a spider_found issue."""
    data = data or {}
    return SpiderThermostatRepairFlow(str(data.get("entry_id", "")), str(data.get("uid", "")))
