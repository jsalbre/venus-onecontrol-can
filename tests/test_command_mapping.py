import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from can_link.frame import decode_id
from can_link.types import MessageType
from dbus_bridge.command_mapping import command_frame_for_switch_write


class RelayDeviceClassTests(unittest.TestCase):
    def test_relay_light_on(self):
        frame = command_frame_for_switch_write("relay_light", 0xF9, 0x1D, desired_on=True)
        decoded = decode_id(frame.can_id, is_extended=True)
        self.assertEqual(decoded.message_data, 1)
        self.assertEqual(frame.data, b"")

    def test_relay_pump_off(self):
        frame = command_frame_for_switch_write("relay_pump", 0xF9, 0x1D, desired_on=False)
        decoded = decode_id(frame.can_id, is_extended=True)
        self.assertEqual(decoded.message_data, 0)
        self.assertEqual(frame.data, b"")

    def test_relay_water_heater_on(self):
        frame = command_frame_for_switch_write("relay_water_heater", 0xF9, 0x1D, desired_on=True)
        decoded = decode_id(frame.can_id, is_extended=True)
        self.assertEqual(decoded.message_data, 1)

    def test_relay_ignores_brightness(self):
        frame_a = command_frame_for_switch_write("relay_light", 0xF9, 0x1D, desired_on=True)
        frame_b = command_frame_for_switch_write("relay_light", 0xF9, 0x1D, desired_on=True, desired_brightness_pct=50)
        self.assertEqual(frame_a, frame_b)


class DimmableLightTests(unittest.TestCase):
    def test_on_without_brightness_uses_toggle_command(self):
        frame = command_frame_for_switch_write("dimmable_light", 0xF9, 0x1D, desired_on=True)
        self.assertEqual(frame.data, bytes.fromhex("7F00000000000000"))

    def test_off_uses_toggle_command(self):
        frame = command_frame_for_switch_write("dimmable_light", 0xF9, 0x1D, desired_on=False)
        self.assertEqual(frame.data, bytes.fromhex("0000000000000000"))

    def test_off_ignores_any_given_brightness(self):
        frame = command_frame_for_switch_write(
            "dimmable_light", 0xF9, 0x1D, desired_on=False, desired_brightness_pct=80
        )
        self.assertEqual(frame.data, bytes.fromhex("0000000000000000"))

    def test_on_with_specific_brightness_converts_percent_to_raw_scale(self):
        # 50% -> round(50/100*255) = 128 = 0x80
        frame = command_frame_for_switch_write(
            "dimmable_light", 0xF9, 0x1D, desired_on=True, desired_brightness_pct=50
        )
        self.assertEqual(frame.data[0], 1)  # mode=1 (on)
        self.assertEqual(frame.data[1], 128)
        decoded = decode_id(frame.can_id, is_extended=True)
        self.assertEqual(decoded.message_data, 0)
        self.assertEqual(decoded.message_type, MessageType.COMMAND)

    def test_on_with_full_brightness(self):
        frame = command_frame_for_switch_write(
            "dimmable_light", 0xF9, 0x1D, desired_on=True, desired_brightness_pct=100
        )
        self.assertEqual(frame.data[1], 255)

    def test_on_with_minimum_brightness(self):
        frame = command_frame_for_switch_write(
            "dimmable_light", 0xF9, 0x1D, desired_on=True, desired_brightness_pct=1
        )
        self.assertEqual(frame.data[1], round(1 / 100 * 255))

    def test_rejects_out_of_range_brightness_percent(self):
        with self.assertRaises(ValueError):
            command_frame_for_switch_write("dimmable_light", 0xF9, 0x1D, desired_on=True, desired_brightness_pct=101)
        with self.assertRaises(ValueError):
            command_frame_for_switch_write("dimmable_light", 0xF9, 0x1D, desired_on=True, desired_brightness_pct=-1)


class UnsupportedDeviceClassTests(unittest.TestCase):
    def test_tank_has_no_command_builder(self):
        with self.assertRaises(ValueError):
            command_frame_for_switch_write("tank", 0xF9, 0x1D, desired_on=True)


if __name__ == "__main__":
    unittest.main()
