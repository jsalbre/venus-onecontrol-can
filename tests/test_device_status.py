import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from can_link.device_status import (
    OutputState,
    UnknownDeviceTypeError,
    decode_dimmable_light,
    decode_relay_or_motor,
    decode_status,
    decode_tank_sensor,
)
from can_link.types import DeviceType


class TankSensorTests(unittest.TestCase):
    def test_decodes_typical_reading(self):
        # 42% full, battery 90%, quality 80%, no tilt, no alert, no DTC
        payload = bytes([42, 90, 80, 0x00, 0x00, 0x00, 0x00, 0x00])
        status = decode_tank_sensor(payload)
        self.assertEqual(status.fill_level_pct, 42)
        self.assertEqual(status.battery_level_pct, 90)
        self.assertEqual(status.measurement_quality_pct, 80)
        self.assertEqual(status.x_acceleration_g, 0.0)
        self.assertEqual(status.y_acceleration_g, 0.0)
        self.assertFalse(status.alert_active)
        self.assertEqual(status.alert_count, 0)
        self.assertEqual(status.dtc, 0)

    def test_masks_reserved_bit_in_fill_level(self):
        payload = bytes([0x80 | 42, 0, 0, 0, 0, 0, 0, 0])
        status = decode_tank_sensor(payload)
        self.assertEqual(status.fill_level_pct, 42)

    def test_quality_not_supported_sentinel(self):
        payload = bytes([0, 0, 0xFF, 0, 0, 0, 0, 0])
        status = decode_tank_sensor(payload)
        self.assertIsNone(status.measurement_quality_pct)

    def test_unknown_acceleration_sentinel(self):
        payload = bytes([0, 0, 0, 0x80, 0x80, 0, 0, 0])  # -128 = unknown
        status = decode_tank_sensor(payload)
        self.assertIsNone(status.x_acceleration_g)
        self.assertIsNone(status.y_acceleration_g)

    def test_negative_acceleration_decodes_signed(self):
        payload = bytes([0, 0, 0, 0xFF, 0x00, 0, 0, 0])  # -1/1024, 0/1024
        status = decode_tank_sensor(payload)
        self.assertAlmostEqual(status.x_acceleration_g, -1 / 1024.0)
        self.assertEqual(status.y_acceleration_g, 0.0)

    def test_alert_active_flag_and_count(self):
        payload = bytes([0, 0, 0, 0, 0, 0x80 | 5, 0, 0])
        status = decode_tank_sensor(payload)
        self.assertTrue(status.alert_active)
        self.assertEqual(status.alert_count, 5)

    def test_dtc_is_big_endian(self):
        payload = bytes([0, 0, 0, 0, 0, 0, 0x12, 0x34])
        status = decode_tank_sensor(payload)
        self.assertEqual(status.dtc, 0x1234)

    def test_rejects_wrong_length(self):
        with self.assertRaises(ValueError):
            decode_tank_sensor(bytes([0] * 7))


class RelayOrMotorTests(unittest.TestCase):
    def test_decodes_observed_idle_reading(self):
        # From research: typical idle reading "80 FF 00 01 00 00" --
        # command-allowed bits set, position not-supported, ~1 count current.
        payload = bytes([0x80, 0xFF, 0x00, 0x01, 0x00, 0x00])
        status = decode_relay_or_motor(payload)
        self.assertEqual(status.output_state, OutputState.OFF_STOP)
        self.assertFalse(status.fault_latch)
        self.assertFalse(status.reverse_allowed)
        self.assertTrue(status.forward_allowed)
        self.assertIsNone(status.position_pct)
        self.assertAlmostEqual(status.current_draw_amps, 1 / 256.0)
        self.assertEqual(status.dtc, 0)

    def test_on_state_with_both_directions_allowed(self):
        payload = bytes([0xC0 | OutputState.ON, 50, 0x01, 0x00, 0, 0])
        status = decode_relay_or_motor(payload)
        self.assertEqual(status.output_state, OutputState.ON)
        self.assertTrue(status.reverse_allowed)
        self.assertTrue(status.forward_allowed)
        self.assertEqual(status.position_pct, 50)
        self.assertEqual(status.current_draw_amps, 1.0)

    def test_fault_latch_bit(self):
        payload = bytes([0x20, 0, 0, 0, 0, 0])
        status = decode_relay_or_motor(payload)
        self.assertTrue(status.fault_latch)

    def test_current_draw_not_supported_sentinel(self):
        payload = bytes([0, 0, 0xFF, 0xFF, 0, 0])
        status = decode_relay_or_motor(payload)
        self.assertIsNone(status.current_draw_amps)

    def test_rejects_wrong_length(self):
        with self.assertRaises(ValueError):
            decode_relay_or_motor(bytes([0] * 5))


class DimmableLightTests(unittest.TestCase):
    def test_decodes_on_at_full_brightness(self):
        payload = bytes([1, 255, 0, 255, 0x00, 0x64, 0x00, 0xC8])
        status = decode_dimmable_light(payload)
        self.assertEqual(status.mode, 1)
        self.assertEqual(status.max_brightness, 255)
        self.assertEqual(status.auto_off_minutes, 0)
        self.assertEqual(status.current_brightness, 255)
        self.assertEqual(status.t1_ms, 100)
        self.assertEqual(status.t2_ms, 200)

    def test_rejects_wrong_length(self):
        with self.assertRaises(ValueError):
            decode_dimmable_light(bytes([0] * 6))


class DecodeStatusDispatchTests(unittest.TestCase):
    def test_dispatches_tank_sensor(self):
        result = decode_status(DeviceType.TANK_SENSOR, bytes([42, 0, 0, 0, 0, 0, 0, 0]))
        self.assertEqual(result.fill_level_pct, 42)

    def test_dispatches_all_relay_and_motor_device_types(self):
        payload = bytes([0x80, 0xFF, 0, 1, 0, 0])
        for device_type in (
            DeviceType.LATCHING_RELAY,
            DeviceType.MOMENTARY_RELAY,
            DeviceType.LATCHING_H_BRIDGE,
            DeviceType.MOMENTARY_H_BRIDGE,
            DeviceType.LATCHING_RELAY_TYPE_2,
            DeviceType.MOMENTARY_RELAY_TYPE_2,
            DeviceType.LATCHING_H_BRIDGE_TYPE_2,
            DeviceType.MOMENTARY_H_BRIDGE_TYPE_2,
        ):
            with self.subTest(device_type=device_type):
                result = decode_status(device_type, payload)
                self.assertEqual(result.output_state, OutputState.OFF_STOP)

    def test_dispatches_dimmable_light(self):
        payload = bytes([1, 255, 0, 100, 0, 0, 0, 0])
        result = decode_status(DeviceType.DIMMABLE_LIGHT, payload)
        self.assertEqual(result.current_brightness, 100)

    def test_raises_for_unmapped_device_type(self):
        with self.assertRaises(UnknownDeviceTypeError):
            decode_status(DeviceType.GENERATOR_GENIE, bytes([0] * 8))

    def test_raises_for_none_device_type(self):
        with self.assertRaises(UnknownDeviceTypeError):
            decode_status(None, bytes([0] * 8))


if __name__ == "__main__":
    unittest.main()
