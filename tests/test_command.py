import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from can_link.command import (
    RelayCommandMode,
    build_dimmable_light_command,
    build_dimmable_light_toggle_command,
    build_relay_command,
)
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

    def test_accepts_full_0_to_255_brightness_range(self):
        # brightness is a raw 0-255 scale (matches DEVICE_STATUS's own
        # current_brightness byte), not a 1-100 percentage -- see this
        # module's docstring for the real capture that confirmed this.
        build_dimmable_light_command(0xA0, 0x3F, 1, 0, 0, 0, 0)
        build_dimmable_light_command(0xA0, 0x3F, 1, 255, 0, 0, 0)

    def test_rejects_brightness_out_of_range(self):
        with self.assertRaises(ValueError):
            build_dimmable_light_command(0xA0, 0x3F, 1, -1, 0, 0, 0)
        with self.assertRaises(ValueError):
            build_dimmable_light_command(0xA0, 0x3F, 1, 256, 0, 0, 0)

    def test_rejects_out_of_range_cycle_times(self):
        with self.assertRaises(ValueError):
            build_dimmable_light_command(0xA0, 0x3F, 1, 50, 0, 0x10000, 0)


class BuildDimmableLightCommandRealHardwareTests(unittest.TestCase):
    # Captured 2026-08-20 from a real brightness-slider drag on "Kitchen
    # Pendants Light" (samples/dimming_capture.log, gitignored). Each
    # payload below produced an immediate, exact-match DEVICE_STATUS
    # current_brightness in the same capture.
    REAL_BRIGHTNESS_COMMANDS = [
        (0x3E, bytes.fromhex("013e000000000000")),
        (0xB5, bytes.fromhex("01b5000000000000")),
        (0xFF, bytes.fromhex("01ff000000000000")),
        (0x20, bytes.fromhex("0120000000000000")),
    ]

    def test_matches_every_captured_real_brightness_command(self):
        for brightness, expected_payload in self.REAL_BRIGHTNESS_COMMANDS:
            with self.subTest(brightness=hex(brightness)):
                frame = build_dimmable_light_command(
                    source_address=0x01,
                    target_address=0xEA,
                    mode=1,
                    brightness=brightness,
                    auto_off_minutes=0,
                    t1_ms=0,
                    t2_ms=0,
                )
                self.assertEqual(frame.data, expected_payload)


class BuildDimmableLightToggleCommandTests(unittest.TestCase):
    # Real payloads confirmed 2026-08-20 against two independent dimmable
    # lights (samples/capture.log) turned on then off from the OneControl
    # app -- see command.py's module docstring for why this differs from
    # build_dimmable_light_command()'s documented-but-unconfirmed layout.
    def test_on_payload_matches_real_hardware(self):
        frame = build_dimmable_light_toggle_command(source_address=0xF9, target_address=0x1D, turn_on=True)
        self.assertEqual(frame.data, bytes.fromhex("7F00000000000000"))

    def test_off_payload_matches_real_hardware(self):
        frame = build_dimmable_light_toggle_command(source_address=0xF9, target_address=0x1D, turn_on=False)
        self.assertEqual(frame.data, bytes.fromhex("0000000000000000"))

    def test_message_data_and_type(self):
        frame = build_dimmable_light_toggle_command(source_address=0xF9, target_address=0x1D, turn_on=True)
        decoded = decode_id(frame.can_id, is_extended=True)
        self.assertEqual(decoded.message_data, 0)
        self.assertEqual(decoded.message_type, MessageType.COMMAND)
        self.assertEqual(decoded.source_address, 0xF9)
        self.assertEqual(decoded.target_address, 0x1D)


if __name__ == "__main__":
    unittest.main()
