import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from can_link.command import RelayCommandMode, build_dimmable_light_command, build_relay_command
from can_link.frame import decode_id
from can_link.types import MessageType


class BuildRelayCommandTests(unittest.TestCase):
    def test_payload_is_always_empty(self):
        frame = build_relay_command(0xA0, 0x3F, RelayCommandMode.ON)
        self.assertEqual(frame.data, b"")

    def test_command_mode_encoded_in_id_message_data_byte(self):
        frame = build_relay_command(0xA0, 0x3F, RelayCommandMode.ON)
        decoded = decode_id(frame.can_id, is_extended=True)
        self.assertEqual(decoded.message_data, RelayCommandMode.ON)
        self.assertEqual(decoded.message_type, MessageType.COMMAND)
        self.assertEqual(decoded.target_address, 0x3F)
        self.assertEqual(decoded.source_address, 0xA0)

    def test_off_and_clear_latch_modes(self):
        off_frame = build_relay_command(0xA0, 0x3F, RelayCommandMode.OFF)
        clear_frame = build_relay_command(0xA0, 0x3F, RelayCommandMode.CLEAR_LATCH)
        self.assertEqual(decode_id(off_frame.can_id, True).message_data, 0)
        self.assertEqual(decode_id(clear_frame.can_id, True).message_data, 3)


class BuildDimmableLightCommandTests(unittest.TestCase):
    def test_payload_is_8_bytes_with_message_data_zero(self):
        frame = build_dimmable_light_command(
            source_address=0xA0,
            target_address=0x3F,
            mode=1,
            brightness=100,
            auto_off_minutes=0,
            t1_ms=100,
            t2_ms=200,
        )
        self.assertEqual(len(frame.data), 8)
        decoded = decode_id(frame.can_id, is_extended=True)
        self.assertEqual(decoded.message_data, 0)
        self.assertEqual(decoded.message_type, MessageType.COMMAND)

    def test_payload_byte_layout(self):
        frame = build_dimmable_light_command(
            source_address=0xA0,
            target_address=0x3F,
            mode=1,
            brightness=75,
            auto_off_minutes=30,
            t1_ms=0x1234,
            t2_ms=0x5678,
        )
        self.assertEqual(
            frame.data,
            bytes([1, 75, 30, 0x12, 0x34, 0x56, 0x78, 0x00]),
        )

    def test_rejects_brightness_out_of_range(self):
        with self.assertRaises(ValueError):
            build_dimmable_light_command(0xA0, 0x3F, 1, 0, 0, 0, 0)
        with self.assertRaises(ValueError):
            build_dimmable_light_command(0xA0, 0x3F, 1, 101, 0, 0, 0)

    def test_rejects_out_of_range_cycle_times(self):
        with self.assertRaises(ValueError):
            build_dimmable_light_command(0xA0, 0x3F, 1, 50, 0, 0x10000, 0)


if __name__ == "__main__":
    unittest.main()
