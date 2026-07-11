# vessel-bridge

Hardware Abstraction Layer for ESP32 to Jetson to Cloud. Unified sensor/actuator API across vessel domains.

## Architecture

```
ESP32 (micro) ←→ UART/I2C/SPI ←→ Jetson (edge) ←→ MQTT/HTTP ←→ Cloud (cocapn)
```

## Current Status

- ✅ **Real today**: the sensor/actuator data model, marine and aerial
  domain presets (sensor/actuator configs + factory functions), value
  clamping, safety-critical command logging, JSON state export, and the
  ESP32 binary protocol encoder/decoder with CRC8 validation. The other
  three domain *types* (industrial, home, medical) exist in the
  `VesselDomain` enum but have no preset configurations yet.
- 🔮 **Not yet wired**: `command_actuator()` validates and clamps the
  value but never actually sends it to hardware (`_route_command` is
  commented out). `read_sensor()` only returns cached data; the cache is
  only populated when something calls `update_reading()`, and no real
  transport driver (serial, I2C, SPI, MQTT, HTTP, …) is implemented in
  this file. This repository is a HAL skeleton + protocol layer, not an
  end-to-end hardware bridge yet.

## Domains

The `VesselDomain` enum defines five types. Only marine and aerial have
preset sensor/actuator configurations and factory functions today:

- ✅ **Marine** — GPS, compass, sonar, depth, thermistor, current, voltage; thrusters, rudder, nav light, winch (`create_marine_vessel`)
- ✅ **Aerial** — GPS, IMU, barometer, lidar; motors, servos (`create_aerial_vessel`)
- 🔮 **Industrial** — enum type defined, no preset config yet
- 🔮 **Home** — enum type defined, no preset config yet
- 🔮 **Medical** — enum type defined, no preset config yet

## Usage

```python
from bridge import create_marine_vessel, VesselDomain

vessel = create_marine_vessel("fishing-boat-01")

# Read sensors
gps = vessel.read_sensor("gps_0")
compass = vessel.read_sensor("compass_0")

# Command actuators (auto-clamped to safe range)
vessel.command_actuator("thruster_port", "set", 0.7)
vessel.command_actuator("rudder", "set", -15.0)

# Register callbacks
vessel.on_reading("gps_0", lambda r: print(f"GPS: {r.values}"))

# Health check
health = vessel.get_health()

# Publish state (JSON for MQTT)
vessel.get_state_json()
```

## ESP32 Protocol

Lightweight binary frame protocol for UART:

```
[0xAA][0x55][len_h][len_l][type][payload][crc8]
```

Sensor data uses compact binary encoding. Commands use JSON.

## Testing

14 tests cover the implemented logic — ESP32 protocol encode/decode
round-trips and CRC8 corruption rejection, vessel preset registration,
the actuator clamping path (including its testability gap: the clamped
value is not externally observable), and JSON serialization round-trips.
Run with:

```bash
PYTHONPATH=src pytest
```

CI runs the same suite on Python 3.12 via GitHub Actions.

## Related Repos

vessel-bridge sits at the hardware layer of a three-tier stack
(ESP32 ↔ Jetson ↔ Cloud). Sibling repos under
[SuperInstance](https://github.com/SuperInstance):

- [openconstruct-esp32](https://github.com/SuperInstance/openconstruct-esp32) — real ESP32 sensor firmware that would run on the microcontroller end of this bridge's UART link
- [nexus-edge-runtime](https://github.com/SuperInstance/nexus-edge-runtime) — edge-side runtime (sensor fusion, navigation, wire protocol) that would consume bridge data on the Jetson
- [fleet-conductor](https://github.com/SuperInstance/fleet-conductor) — fleet orchestration core that would coordinate multiple vessels via the cloud tier of this bridge's architecture
