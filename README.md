# IthoWiFi - Home Assistant Integration

Home Assistant integration for the [IthoWiFi add-on](https://github.com/arjenhiemstra/ithowifi), providing local control of Itho Daalderop ventilation units via the REST API.

## Features

- **Fan entity** with speed control and preset modes (low, medium, high, auto, autonight, away)
- **Sensors** dynamically created based on your device type (temperature, humidity, fan speed, CO2, bypass position, etc.)
- **Diagnostic sensors** for error codes, operation hours, startup counter, and more
- **Timer buttons** for timer1, timer2, timer3 (cook30/cook60 for DemandFlow/QualityFlow devices)
- **Fan demand slider** for direct speed control
- **Reboot button**
- **Sensor selection** during setup — choose which sensors and diagnostics to include
- **Options flow** to change sensor selection after setup

## Requirements

- [IthoWiFi add-on](https://github.com/arjenhiemstra/ithowifi) firmware **v3.0.0-beta1** or newer (REST API v2 required)
- The add-on must be reachable on your local network

## Installation

### HACS (recommended)

1. Open HACS in Home Assistant
2. Go to **Integrations**
3. Click the three-dot menu (top right) and select **Custom repositories**
4. Add `arjenhiemstra/ithowifi-ha-integration` with category **Integration**
5. Click **Add**, then find **IthoWiFi** and click **Download**
6. Restart Home Assistant

### Manual

1. Copy the `custom_components/itho_wifi` folder to your Home Assistant `config/custom_components/` directory
2. Restart Home Assistant

## Configuration

1. Go to **Settings** > **Devices & Services** > **Add Integration**
2. Search for **IthoWiFi**
3. Enter the IP address or hostname of your IthoWiFi add-on
4. Optionally enter credentials (only needed if API security is enabled on the device)
5. Select which sensors and diagnostic items to include
6. Click **Submit**

Sensor selection can be changed later via **Configure** on the integration card.

## Supported devices

This integration works with all Itho Daalderop ventilation units supported by the IthoWiFi add-on, including:

- CVE ECO 2
- CVE ECO RFT
- CVE-S ECO / PAK / Optima / CO2
- HRU 150 / 200 / 250 / 300 / 350 / 400
- DemandFlow / QualityFlow
- WPU 4G / 5G

Sensors are created dynamically based on what your specific device reports, so different device types automatically get the appropriate entities.

## How it works

This integration communicates with the IthoWiFi add-on over your local network using the REST API v2. No MQTT broker or cloud connection is required. The add-on polls the device every 10 seconds for status updates and every 5 minutes for device information.

## Troubleshooting

- **Cannot connect**: Verify the IP address and that the device is reachable from your HA instance
- **Authentication failed**: Check that username/password match the API security settings on the device
- **Missing sensors**: Some sensors only appear for specific device types. Use the **Configure** button to review available sensors.

## License

This project is licensed under the GPL-3.0 license. See the [LICENSE](LICENSE) file for details.
