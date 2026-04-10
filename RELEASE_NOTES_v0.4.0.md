## v0.4.0

Adds a native firmware update entity for the IthoWiFi add-on. Once the device is running firmware ≥ `3.1.0-beta2`, Home Assistant shows the add-on firmware as a first-class update entity that can be installed directly from the HA UI without leaving the dashboard.

### New Features

- **Firmware update entity** (`update.py`)
  - Polls the new `GET /api/v2/ota` endpoint to read installed and latest firmware versions
  - Auto-selects the release channel from the installed version: pre-release strings (`-beta`, `-rc`, `-alpha`, `-dev`) pin the entity to the beta channel; otherwise stable
  - Quietly stays in the "up to date" state when a beta user is on the latest beta but stable is older — no more bogus "downgrade available" notifications
  - Triggers a real install via `POST /api/v2/ota` with the active channel
  - Live progress tracking during install: monitors `state` (`idle` / `starting` / `downloading` / `done` / `error`) and `progress` (0-100), updates the entity's `in_progress` and `update_percentage` attributes in real time
  - Tolerates the device being unreachable during the post-flash reboot for up to 5 minutes
  - On 404 from `/api/v2/ota` (e.g. firmware downgraded to a version without the endpoint) the entity goes unavailable cleanly instead of churning forever
  - Hard timeout of 15 minutes on the install monitor as a safety net
  - Uses the device favicon as `entity_picture` to match the device's web UI
  - Release URL points at the GitHub release tag for the actual target version
  - Refreshes device info after install so HA's device registry `sw_version` field updates
- **Graceful degradation**: if the device is on firmware < `3.1.0-beta2` (no `/api/v2/ota` endpoint), the update entity is silently skipped during integration setup. The rest of the integration continues to work normally
- **New API client methods** in `api.py`:
  - `get_ota()` — fetches `/api/v2/ota` and returns the inner `ota` payload
  - `start_ota(channel)` — POSTs to `/api/v2/ota` with `{"channel": ...}`
- **New `IthoWiFiNotFoundError`** exception subclass of `IthoWiFiApiError`, raised on HTTP 404 so callers can distinguish "endpoint missing" from generic API errors

### Compatibility

- **Requires firmware `3.1.0-beta2` or newer** for the firmware update entity to appear. Older firmware works fine but the update entity will not be registered.
- All existing entities (fan, sensors, buttons, number) are unchanged.
- No breaking changes; this is a feature-add release.

### Notes

- The integration replaces the firmware-side MQTT auto-discovery update entity for users who have both MQTT and this integration. The MQTT-discovered update entity is still produced by the firmware unchanged; if you no longer want it, disable MQTT on the device or clear the retained discovery topic from your broker.
- Channel preference is auto-detected, not user-selectable yet — beta users automatically track the beta channel, stable users automatically track the stable channel. A user-selectable update channel may follow in a future release.
