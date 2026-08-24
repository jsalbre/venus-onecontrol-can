import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from can_link.pid_client import (
    PID_BATTERY_VOLTAGE,
    PID_DEVICE_TYPE,
    build_pid_list_request,
    build_pid_properties_request,
    build_pid_read_request,
    build_pid_write_request,
    decode_16_16_fixed_point,
    parse_pid_list_reply,
    parse_pid_properties_reply,
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


class BuildPidWriteRequestTests(unittest.TestCase):
    def test_matches_real_pid_161_write_shape(self):
        # PID 161 (SIMULATE_ON_OFF_STYLE_LIGHT), value=0, 6 bytes -- the
        # real write that succeeded on real hardware (2026-08-21). A first
        # attempt at 1 byte (matching what a *read* returns) got
        # RESPONSE.BAD_REQUEST; 6 bytes (UInt48) is what actually worked --
        # confirmed 2026-08-24 (decompiled LippertConnect WritePidAsync,
        # see ARCHITECTURE.md) to be the universal write width for every
        # PID, not something specific to 161's own declared Formatter.
        self.assertEqual(build_pid_write_request(161, 0, 6), bytes([0x00, 0xA1, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]))

    def test_default_value_byte_count_is_six(self):
        self.assertEqual(
            build_pid_write_request(161, 1), bytes([0x00, 0xA1, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01])
        )

    def test_multi_byte_value(self):
        self.assertEqual(build_pid_write_request(4, 0x1234, 2), bytes([0x00, 0x04, 0x12, 0x34]))

    def test_rejects_out_of_range_pid(self):
        with self.assertRaises(ValueError):
            build_pid_write_request(0x10000, 0, 1)

    def test_rejects_value_too_large_for_byte_count(self):
        with self.assertRaises(ValueError):
            build_pid_write_request(161, 256, 1)

    def test_rejects_negative_value(self):
        with self.assertRaises(ValueError):
            build_pid_write_request(161, -1, 1)

    def test_rejects_zero_value_byte_count(self):
        with self.assertRaises(ValueError):
            build_pid_write_request(161, 0, 0)


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


class BuildPidPropertiesRequestTests(unittest.TestCase):
    def test_same_payload_shape_as_read_request(self):
        self.assertEqual(build_pid_properties_request(PID_DEVICE_TYPE), build_pid_read_request(PID_DEVICE_TYPE))

    def test_rejects_out_of_range_pid(self):
        with self.assertRaises(ValueError):
            build_pid_properties_request(0x10000)


class ParsePidPropertiesReplyTests(unittest.TestCase):
    def test_rejects_too_short_payload(self):
        with self.assertRaises(ValueError):
            parse_pid_properties_reply(bytes([0x00]))

    def test_pid_only_no_trailing_bytes(self):
        reply = parse_pid_properties_reply(bytes([0x00, 0xB7]))
        self.assertEqual(reply.pid, PID_DEVICE_TYPE)
        self.assertEqual(reply.raw_value, b"")
        self.assertIsNone(reply.flags)
        self.assertIsNone(reply.session_id)

    def test_two_trailing_bytes_taken_as_session_id_only(self):
        reply = parse_pid_properties_reply(bytes([0x00, 0xB7, 0x00, 0x02]))
        self.assertEqual(reply.raw_value, bytes([0x00, 0x02]))
        self.assertIsNone(reply.flags)
        self.assertEqual(reply.session_id, 2)

    def test_three_trailing_bytes_splits_one_flags_byte(self):
        reply = parse_pid_properties_reply(bytes([0x00, 0xB7, 0x01, 0x00, 0x02]))
        self.assertEqual(reply.raw_value, bytes([0x01, 0x00, 0x02]))
        self.assertEqual(reply.flags, 0x01)
        self.assertEqual(reply.session_id, 2)

    def test_four_trailing_bytes_splits_two_flags_bytes(self):
        reply = parse_pid_properties_reply(bytes([0x00, 0xB7, 0x00, 0x01, 0x00, 0x02]))
        self.assertEqual(reply.raw_value, bytes([0x00, 0x01, 0x00, 0x02]))
        self.assertEqual(reply.flags, 0x0001)
        self.assertEqual(reply.session_id, 2)


class BuildPidListRequestTests(unittest.TestCase):
    def test_page_zero(self):
        self.assertEqual(build_pid_list_request(0), bytes([0x00, 0x00]))

    def test_page_one(self):
        self.assertEqual(build_pid_list_request(1), bytes([0x00, 0x01]))

    def test_rejects_out_of_range_page(self):
        with self.assertRaises(ValueError):
            build_pid_list_request(0x10000)


class ParsePidListReplyTests(unittest.TestCase):
    # Worked examples derived directly from the decompiled server handler
    # (Request10PidReadList, assembly_0079_Sharp.decompiled.cs) for a
    # hypothetical 3-PID device: PID 5 (flags=1), PID 10 (flags=2),
    # PID 20 (flags=3).
    def test_page_zero_includes_count_and_first_entry(self):
        payload = bytes.fromhex("0000000300000501")
        page = parse_pid_list_reply(payload)
        self.assertEqual(page.page, 0)
        self.assertEqual(page.total_count, 3)
        self.assertEqual(len(page.entries), 1)
        self.assertEqual(page.entries[0].pid, 5)
        self.assertEqual(page.entries[0].flags, 1)

    def test_subsequent_page_has_two_entries_and_no_count(self):
        payload = bytes.fromhex("0001000a02001403")
        page = parse_pid_list_reply(payload)
        self.assertEqual(page.page, 1)
        self.assertIsNone(page.total_count)
        self.assertEqual(len(page.entries), 2)
        self.assertEqual((page.entries[0].pid, page.entries[0].flags), (10, 2))
        self.assertEqual((page.entries[1].pid, page.entries[1].flags), (20, 3))

    def test_rejects_too_short_payload(self):
        with self.assertRaises(ValueError):
            parse_pid_list_reply(bytes([0x00]))

    def test_rejects_page_zero_missing_count(self):
        with self.assertRaises(ValueError):
            parse_pid_list_reply(bytes([0x00, 0x00, 0x00]))


if __name__ == "__main__":
    unittest.main()
