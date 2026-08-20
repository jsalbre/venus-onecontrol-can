import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from can_link.frame import CanFrame
from tools.candump_logger import format_candump_line


class FormatCandumpLineTests(unittest.TestCase):
    def test_formats_standard_frame(self):
        frame = CanFrame(can_id=0x242, is_extended=False, data=bytes([0x01, 0x02]))
        line = format_candump_line("can1", frame, timestamp=1700000000.123456)
        self.assertEqual(line, "(1700000000.123456) can1 242#0102")

    def test_formats_extended_frame_with_8_hex_digits(self):
        frame = CanFrame(can_id=0x0280DD42, is_extended=True, data=bytes([0x00, 0x04]))
        line = format_candump_line("can1", frame, timestamp=1.0)
        self.assertEqual(line, "(1.000000) can1 0280DD42#0004")

    def test_zero_pads_short_standard_id(self):
        frame = CanFrame(can_id=0x05, is_extended=False, data=b"")
        line = format_candump_line("can1", frame, timestamp=0.0)
        self.assertEqual(line, "(0.000000) can1 005#")


if __name__ == "__main__":
    unittest.main()
