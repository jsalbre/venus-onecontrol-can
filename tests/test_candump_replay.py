import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tools.candump_replay import parse_log_line, replay


class ParseLogLineTests(unittest.TestCase):
    def test_parses_standard_frame_line(self):
        entry = parse_log_line("(1700000000.123456) can1 242#0102")
        self.assertEqual(entry.timestamp, 1700000000.123456)
        self.assertEqual(entry.interface, "can1")
        self.assertEqual(entry.can_id, 0x242)
        self.assertFalse(entry.is_extended)
        self.assertEqual(entry.data, bytes([0x01, 0x02]))

    def test_parses_extended_frame_line(self):
        entry = parse_log_line("(1.000000) can1 0280DD42#0004")
        self.assertEqual(entry.can_id, 0x0280DD42)
        self.assertTrue(entry.is_extended)

    def test_parses_empty_payload(self):
        entry = parse_log_line("(0.000000) can1 005#")
        self.assertEqual(entry.data, b"")

    def test_returns_none_for_unparsable_line(self):
        self.assertIsNone(parse_log_line("not a log line"))
        self.assertIsNone(parse_log_line(""))


class ReplayTests(unittest.TestCase):
    def test_decodes_device_id_then_device_status_for_tank_sensor(self):
        lines = [
            "(0.000000) can1 242#1234010A00430100",  # DEVICE_ID: tank, FUNCTION_NAME=67 (Fresh Tank), function_instance=1
            "(1.000000) can1 342#2A5A500000000000",  # DEVICE_STATUS: fill=42%, battery=90, quality=80
        ]
        output = list(replay(lines))
        self.assertEqual(len(output), 2)
        self.assertIn("DEVICE_ID", output[0])
        self.assertIn("Fresh Tank", output[0])
        self.assertIn("stable_key=function_name=67,function_instance=1", output[0])
        self.assertIn("DEVICE_STATUS", output[1])
        self.assertIn("fill_level_pct=42", output[1])

    def test_device_status_before_any_device_id_has_unknown_type(self):
        output = list(replay(["(0.000000) can1 342#2A5A500000000000"]))
        self.assertIn("device_type=None", output[0])
        self.assertIn("no decoder yet", output[0])

    def test_point_to_point_frame_prints_raw_breakdown(self):
        output = list(replay(["(0.000000) can1 0280DD42#0004"]))
        self.assertIn("REQUEST", output[0])
        self.assertIn("src=0xA0", output[0])
        self.assertIn("dst=0xDD", output[0])
        self.assertIn("msg_data=0x42", output[0])

    def test_skips_unparsable_lines(self):
        output = list(replay(["", "garbage line", "(0.000000) can1 005#"]))
        self.assertEqual(len(output), 1)


if __name__ == "__main__":
    unittest.main()
