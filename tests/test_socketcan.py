import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from bus.socketcan import CAN_EFF_FLAG, FRAME_SIZE, pack_frame, unpack_frame
from can_link.frame import CanFrame


class PackUnpackTests(unittest.TestCase):
    def test_round_trips_standard_frame(self):
        frame = CanFrame(can_id=0x242, is_extended=False, data=b"\x01\x02\x03")
        packed = pack_frame(frame)
        self.assertEqual(len(packed), FRAME_SIZE)
        self.assertEqual(unpack_frame(packed), frame)

    def test_round_trips_extended_frame(self):
        frame = CanFrame(can_id=0x0280DD42, is_extended=True, data=b"\x00\x04")
        packed = pack_frame(frame)
        self.assertEqual(unpack_frame(packed), frame)

    def test_round_trips_empty_payload(self):
        frame = CanFrame(can_id=0x0280DD01, is_extended=True, data=b"")
        self.assertEqual(unpack_frame(pack_frame(frame)), frame)

    def test_round_trips_full_8_byte_payload(self):
        frame = CanFrame(can_id=0x300, is_extended=False, data=bytes(range(8)))
        self.assertEqual(unpack_frame(pack_frame(frame)), frame)

    def test_extended_flag_set_on_wire_for_extended_frames(self):
        frame = CanFrame(can_id=0x01, is_extended=True, data=b"")
        packed = pack_frame(frame)
        can_id_flags = int.from_bytes(packed[:4], "little")
        self.assertTrue(can_id_flags & CAN_EFF_FLAG)

    def test_extended_flag_not_set_for_standard_frames(self):
        frame = CanFrame(can_id=0x01, is_extended=False, data=b"")
        packed = pack_frame(frame)
        can_id_flags = int.from_bytes(packed[:4], "little")
        self.assertFalse(can_id_flags & CAN_EFF_FLAG)

    def test_rejects_payload_over_8_bytes(self):
        frame = CanFrame(can_id=0x01, is_extended=False, data=bytes(9))
        with self.assertRaises(ValueError):
            pack_frame(frame)

    def test_rejects_wrong_size_raw_frame(self):
        with self.assertRaises(ValueError):
            unpack_frame(b"\x00" * 4)


if __name__ == "__main__":
    unittest.main()
