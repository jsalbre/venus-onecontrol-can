import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from can_link.pid_client import (
    PID_BATTERY_VOLTAGE,
    build_pid_read_request,
    decode_16_16_fixed_point,
    parse_pid_reply,
)


class BuildPidReadRequestTests(unittest.TestCase):
    def test_matches_worked_example_pid_43(self):
        # From dev-notes/ARCHITECTURE.md worked example: "read PID 43" ->
        # payload #00:2B
        self.assertEqual(build_pid_read_request(43), bytes([0x00, 0x2B]))

    def test_rejects_out_of_range_pid(self):
        with self.assertRaises(ValueError):
            build_pid_read_request(0x10000)


class ParsePidReplyTests(unittest.TestCase):
    def test_matches_worked_example_battery_voltage(self):
        # From dev-notes/ARCHITECTURE.md: reply payload #00:2B:0D:7F:F3 ->
        # PID 43, value 0x0D7FF3, DLC-truncated to 3 value bytes (not the
        # nominal 4-byte UINT32 width).
        payload = bytes([0x00, 0x2B, 0x0D, 0x7F, 0xF3])
        reply = parse_pid_reply(payload)
        self.assertEqual(reply.pid, PID_BATTERY_VOLTAGE)
        self.assertEqual(reply.raw_value, 0x0D7FF3)
        self.assertEqual(reply.value_byte_count, 3)
        self.assertAlmostEqual(decode_16_16_fixed_point(reply.raw_value), 13.5, places=3)

    def test_handles_full_4_byte_value(self):
        payload = bytes([0x00, 0x2B, 0x00, 0x0D, 0x80, 0x00])
        reply = parse_pid_reply(payload)
        self.assertEqual(reply.value_byte_count, 4)
        self.assertEqual(decode_16_16_fixed_point(reply.raw_value), 13.5)

    def test_rejects_too_short_payload(self):
        with self.assertRaises(ValueError):
            parse_pid_reply(bytes([0x00, 0x2B]))


if __name__ == "__main__":
    unittest.main()
