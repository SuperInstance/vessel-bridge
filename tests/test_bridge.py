"""Tests for the real, self-contained logic in vessel-bridge.

These tests cover the parts of the codebase that are implemented and
testable today: the data model, domain presets, JSON serialization, and
the ESP32 binary protocol encode/decode + CRC8. They deliberately do not
pretend to exercise real hardware I/O because none exists in this file.
"""

import json
import time

import pytest

from bridge import (
    ActuatorCommand,
    ActuatorConfig,
    ActuatorType,
    ESP32Protocol,
    QoSLevel,
    SensorConfig,
    SensorReading,
    SensorType,
    TransportType,
    VesselDomain,
    VesselBridge,
    create_aerial_vessel,
    create_marine_vessel,
)


class TestESP32Protocol:
    def test_sensor_encode_decode_roundtrip(self):
        reading = SensorReading(
            sensor_id="gps_0",
            sensor_type=SensorType.GPS,
            timestamp=time.time(),
            values={"lat": 12.34, "lon": 56.78},
            metadata={"foo": "bar"},
            qos=QoSLevel.REALTIME,
            confidence=0.95,
        )
        frame = ESP32Protocol.encode_sensor(reading)
        decoded = ESP32Protocol.decode_frame(frame)
        assert decoded is not None
        assert decoded["type"] == ESP32Protocol.FRAME_SENSOR
        # to_binary intentionally does not preserve metadata/qos/confidence,
        # but the payload must match what encode_sensor placed in the frame.
        assert decoded["payload"] == reading.to_binary()

    def test_command_encode_decode_roundtrip(self):
        cmd = ActuatorCommand(
            actuator_id="rudder",
            actuator_type=ActuatorType.RUDDER,
            timestamp=time.time(),
            command="set",
            value=-15.0,
            parameters={"rate": 1.0},
            qos=QoSLevel.REALTIME,
        )
        frame = ESP32Protocol.encode_command(cmd)
        decoded = ESP32Protocol.decode_frame(frame)
        assert decoded is not None
        assert decoded["type"] == ESP32Protocol.FRAME_COMMAND
        parsed = json.loads(decoded["payload"])
        assert parsed["aid"] == "rudder"
        assert parsed["cmd"] == "set"
        assert parsed["val"] == pytest.approx(-15.0)
        assert parsed["params"] == {"rate": 1.0}

    def test_corrupted_frame_is_rejected(self):
        reading = SensorReading(
            sensor_id="gps_0",
            sensor_type=SensorType.GPS,
            timestamp=time.time(),
            values={"lat": 12.34},
        )
        frame = bytearray(ESP32Protocol.encode_sensor(reading))
        # Corrupt a byte in the payload area (not the sync bytes or CRC byte).
        frame[-3] ^= 0xFF
        assert ESP32Protocol.decode_frame(bytes(frame)) is None

    def test_decode_too_short_returns_none(self):
        assert ESP32Protocol.decode_frame(b"\xAA") is None

    def test_decode_bad_sync_returns_none(self):
        assert ESP32Protocol.decode_frame(b"\x00\x55\x00\x00\x01\x00") is None

    def test_decode_truncated_payload_returns_none(self):
        """A frame with valid sync but fewer payload bytes than declared must not crash."""
        # Sync + declared length 10 + type, but only 6 bytes total -> missing payload/CRC.
        assert ESP32Protocol.decode_frame(b"\xAA\x55\x00\x0A\x01\x00") is None


class TestVesselCreation:
    def test_create_marine_vessel_registers_expected_sensors_and_actuators(self):
        vessel = create_marine_vessel("fishing-boat-01")
        assert vessel.vessel_id == "fishing-boat-01"
        assert vessel.domain == VesselDomain.MARINE
        expected_sensors = {
            "gps_0",
            "compass_0",
            "sonar_0",
            "depth_0",
            "thermistor_0",
            "current_0",
            "voltage_0",
        }
        expected_actuators = {
            "thruster_port",
            "thruster_stbd",
            "rudder",
            "light_nav",
            "winch",
        }
        assert set(vessel.sensors.keys()) == expected_sensors
        assert set(vessel.actuators.keys()) == expected_actuators

    def test_marine_preset_uses_transport_type_for_actuators(self):
        vessel = create_marine_vessel("boat")
        for aid, cfg in vessel.actuators.items():
            assert isinstance(cfg.transport, TransportType), (
                f"{aid} transport must be TransportType, got {type(cfg.transport).__name__}"
            )

    def test_create_aerial_vessel_registers_expected_sensors_and_actuators(self):
        vessel = create_aerial_vessel("drone-01")
        assert vessel.vessel_id == "drone-01"
        assert vessel.domain == VesselDomain.AERIAL
        expected_sensors = {"gps_0", "imu_0", "baro_0", "lidar_0"}
        expected_actuators = {
            "motor_fl",
            "motor_fr",
            "motor_bl",
            "motor_br",
            "servo_roll",
            "servo_pitch",
            "servo_yaw",
        }
        assert set(vessel.sensors.keys()) == expected_sensors
        assert set(vessel.actuators.keys()) == expected_actuators


class TestCommandActuator:
    def test_unknown_actuator_returns_false(self):
        vessel = create_marine_vessel("boat")
        assert vessel.command_actuator("not_an_actuator", "set", 0.5) is False

    def test_known_actuator_returns_true(self):
        vessel = create_marine_vessel("boat")
        assert vessel.command_actuator("rudder", "set", 0.0) is True

    def test_clamping_is_not_externally_observable(self):
        """command_actuator clamps internally but exposes no clamped value.

        This is a testability gap: the method always returns True and the
        clamped value is not returned, logged, or emitted anywhere a caller
        can inspect it without monkey-patching private state.
        """
        vessel = create_marine_vessel("boat")
        # Rudder range is [-45, 45]. Out-of-range commands still return True.
        assert vessel.command_actuator("rudder", "set", -90.0) is True
        assert vessel.command_actuator("rudder", "set", 90.0) is True
        # command_actuator hardcodes qos to REALTIME, so even extreme values
        # never reach the safety-critical command log.
        assert len(vessel._command_log) == 0

    def test_clamping_one_liner_logic(self):
        """Directly exercise the clamp expression used by command_actuator."""
        config = ActuatorConfig(
            actuator_id="rudder",
            actuator_type=ActuatorType.RUDDER,
            transport=TransportType.PWM,
            address="GPIO14",
            min_value=-45.0,
            max_value=45.0,
        )
        assert max(config.min_value, min(config.max_value, -90.0)) == -45.0
        assert max(config.min_value, min(config.max_value, 90.0)) == 45.0
        assert max(config.min_value, min(config.max_value, 10.0)) == 10.0


class TestDataModelRoundTrips:
    def test_sensor_reading_json_roundtrip(self):
        original = SensorReading(
            sensor_id="gps_0",
            sensor_type=SensorType.GPS,
            timestamp=time.time(),
            values={"lat": 12.34, "lon": 56.78},
            metadata={"foo": "bar"},
            qos=QoSLevel.REALTIME,
            confidence=0.95,
        )
        restored = SensorReading.from_json(original.to_json())
        assert restored.sensor_id == original.sensor_id
        assert restored.sensor_type == original.sensor_type
        assert restored.timestamp == pytest.approx(original.timestamp)
        assert restored.values == pytest.approx(original.values)
        assert restored.metadata == original.metadata
        assert restored.qos == original.qos
        assert restored.confidence == pytest.approx(original.confidence)

    def test_actuator_command_json_roundtrip(self):
        original = ActuatorCommand(
            actuator_id="thruster_port",
            actuator_type=ActuatorType.THRUSTER,
            timestamp=time.time(),
            command="set",
            value=0.7,
            parameters={"rate": 0.5},
            qos=QoSLevel.SAFETY_CRITICAL,
        )
        restored = ActuatorCommand.from_json(original.to_json())
        assert restored.actuator_id == original.actuator_id
        assert restored.actuator_type == original.actuator_type
        assert restored.timestamp == pytest.approx(original.timestamp)
        assert restored.command == original.command
        assert restored.value == pytest.approx(original.value)
        assert restored.parameters == original.parameters
        assert restored.qos == original.qos

    def test_vessel_state_json_roundtrip(self):
        vessel = create_marine_vessel("boat")
        vessel.update_reading("gps_0", {"lat": 1.0, "lon": 2.0})
        state_json = vessel.get_state_json()
        parsed = json.loads(state_json)
        assert parsed["vessel_id"] == "boat"
        assert parsed["domain"] == "marine"
        assert "timestamp" in parsed
        assert parsed["actuator_count"] == 5
        assert "gps_0" in parsed["sensors"]
        gps = parsed["sensors"]["gps_0"]
        assert gps["sid"] == "gps_0"
        assert gps["type"] == "gps"
        assert gps["v"] == pytest.approx({"lat": 1.0, "lon": 2.0})
