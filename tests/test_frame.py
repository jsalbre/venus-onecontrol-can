import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from can_link.frame import ExtendedId, StandardId, decode_id, encode_extended_id, encode_standard_id
from can_link.types import MessageType


class StandardIdTests(unittest.TestCase):
    def test_decode_device_id_broadcast(self):
        # DEVICE_ID broadcast from source address 0x42: ID = 0x200 | 0x42
        decoded = decode_id(0x242, is_extended=False)
        self.assertIsInstance(decoded, StandardId)
        self.assertEqual(decoded.source_address, 0x42)
        self.assertEqual(decoded.message_type, MessageType.DEVICE_ID)

    def test_decode_device_status_broadcast(self):
        decoded = decode_id(0x342, is_extended=False)
        self.assertEqual(decoded.source_address, 0x42)
        self.assertEqual(decoded.message_type, MessageType.DEVICE_STATUS)

    def test_round_trip_encode_decode(self):
        original = StandardId(source_address=0xAB, message_type=MessageType.TIME)
        decoded = decode_id(original.encode(), is_extended=False)
        self.assertEqual(decoded, original)

    def test_encode_rejects_out_of_range_message_type(self):
        with self.assertRaises(ValueError):
            StandardId(source_address=0x01, message_type=8).encode()

    def test_encode_rejects_out_of_range_source_address(self):
        with self.assertRaises(ValueError):
            StandardId(source_address=256, message_type=2).encode()


class ExtendedIdTests(unittest.TestCase):
    def test_encode_matches_hand_derived_session_request_seed_id(self):
        # SESSION_REQUEST_SEED (request code 0x42) sent from address 0xA0 to
        # target 0xDD. Hand-derived from the CAN_ID formula in
        # dev-notes/ARCHITECTURE.md and cross-checked against the literal ID
        # value reported in the source research (0x0280DD42) -- NOT yet
        # confirmed against a real capture from this coach (see TODO.md
        # Phase 0). Only this direction (REQUEST) was independently
        # reconciled by hand; the RESPONSE direction's exact source address
        # in that same example could not be re-derived with confidence and
        # is deliberately not asserted here.
        encoded = encode_extended_id(
            source_address=0xA0,
            target_address=0xDD,
            message_data=0x42,
            message_type=MessageType.REQUEST,
        )
        self.assertEqual(encoded, 0x0280DD42)
        decoded = decode_id(encoded, is_extended=True)
        self.assertEqual(decoded.target_address, 0xDD)
        self.assertEqual(decoded.message_data, 0x42)
        self.assertEqual(decoded.message_type, MessageType.REQUEST)

    def test_round_trip_encode_decode(self):
        original = ExtendedId(
            source_address=0xA0,
            target_address=0xDD,
            message_data=0x43,
            message_type=MessageType.REQUEST,
        )
        decoded = decode_id(original.encode(), is_extended=True)
        self.assertEqual(decoded, original)

    def test_round_trip_command_type(self):
        original = ExtendedId(
            source_address=0x01,
            target_address=0x3F,
            message_data=0x01,
            message_type=MessageType.COMMAND,
        )
        decoded = decode_id(original.encode(), is_extended=True)
        self.assertEqual(decoded, original)

    def test_encode_rejects_out_of_range_message_type(self):
        with self.assertRaises(ValueError):
            ExtendedId(0, 0, 0, message_type=0x7F).encode()

    def test_encode_rejects_out_of_range_target_address(self):
        with self.assertRaises(ValueError):
            ExtendedId(0, 256, 0, message_type=MessageType.COMMAND).encode()


if __name__ == "__main__":
    unittest.main()
