import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from can_link.address_claim import (
    RESERVED_ADDRESSES,
    ActiveAddressTracker,
    AddressClaimer,
    ClaimState,
    choose_candidate_address,
    decode_claim_frame,
    encode_bridge_device_id_frame,
    encode_claim_frame,
    encode_network_announce,
)
from can_link.frame import decode_id
from can_link.types import MessageType


class ClaimFrameCodecRealHardwareTests(unittest.TestCase):
    # Captured 2026-08-20 from a real OneControl power-cycle/reconnect
    # (samples/poweroutage_capture.log, gitignored). CAN ID 0x000, payload
    # [candidate_address, identity_tail(7)]. The awning motor's claim,
    # independently cross-validated against its subsequent DEVICE_ID
    # (FUNCTION_NAME=105 "Awning") and at-rest DEVICE_STATUS (C0FF00000000).
    REAL_CLAIM_PAYLOAD = bytes.fromhex("2A1E000000302B3E")

    def test_decodes_real_claim_payload(self):
        claim = decode_claim_frame(self.REAL_CLAIM_PAYLOAD)
        self.assertEqual(claim.candidate_address, 0x2A)
        self.assertEqual(claim.identity_tail, bytes.fromhex("1E000000302B3E"))

    def test_encode_matches_real_claim_payload(self):
        frame = encode_claim_frame(0x2A, bytes.fromhex("1E000000302B3E"))
        self.assertEqual(frame.can_id, 0x000)
        self.assertFalse(frame.is_extended)
        self.assertEqual(frame.data, self.REAL_CLAIM_PAYLOAD)

    def test_rejects_wrong_length_tail(self):
        with self.assertRaises(ValueError):
            encode_claim_frame(0x2A, b"\x00" * 6)

    def test_decode_rejects_wrong_length_payload(self):
        with self.assertRaises(ValueError):
            decode_claim_frame(b"\x00" * 7)


class NetworkAnnounceRealHardwareTests(unittest.TestCase):
    # Same coach, same claim (0x2A) -- steady-state NETWORK broadcast
    # observed exactly 1.002939s after the claim frame, same identity tail,
    # leading byte changed from the candidate address to 0x00.
    REAL_STEADY_STATE_PAYLOAD = bytes.fromhex("001E000000302B3E")

    def test_encode_matches_real_steady_state_payload(self):
        frame = encode_network_announce(0x2A, bytes.fromhex("1E000000302B3E"))
        self.assertEqual(frame.data, self.REAL_STEADY_STATE_PAYLOAD)
        decoded = decode_id(frame.can_id, is_extended=False)
        self.assertEqual(decoded.source_address, 0x2A)
        self.assertEqual(decoded.message_type, MessageType.NETWORK)

    def test_rejects_wrong_length_tail(self):
        with self.assertRaises(ValueError):
            encode_network_announce(0x2A, b"\x00" * 8)


class BridgeDeviceIdFrameTests(unittest.TestCase):
    def test_encodes_bridge_identity(self):
        frame = encode_bridge_device_id_frame(0x7A)
        decoded = decode_id(frame.can_id, is_extended=False)
        self.assertEqual(decoded.source_address, 0x7A)
        self.assertEqual(decoded.message_type, MessageType.DEVICE_ID)

        from can_link.device_id import decode_device_id

        identity = decode_device_id(frame.data)
        self.assertEqual(identity.product_id, 0xA0FF)
        self.assertEqual(identity.device_type_raw, 34)
        self.assertEqual(identity.function_name, 1)


class ActiveAddressTrackerTests(unittest.TestCase):
    def test_unknown_address_is_not_active(self):
        tracker = ActiveAddressTracker(ttl_sec=5.0)
        self.assertFalse(tracker.is_active(0x2A, now=0.0))

    def test_recently_seen_address_is_active(self):
        tracker = ActiveAddressTracker(ttl_sec=5.0)
        tracker.note_address(0x2A, now=0.0)
        self.assertTrue(tracker.is_active(0x2A, now=4.9))

    def test_address_expires_after_ttl(self):
        tracker = ActiveAddressTracker(ttl_sec=5.0)
        tracker.note_address(0x2A, now=0.0)
        self.assertFalse(tracker.is_active(0x2A, now=5.1))

    def test_active_addresses_set(self):
        tracker = ActiveAddressTracker(ttl_sec=5.0)
        tracker.note_address(0x2A, now=0.0)
        tracker.note_address(0x3F, now=4.0)
        self.assertEqual(tracker.active_addresses(now=4.5), {0x2A, 0x3F})
        self.assertEqual(tracker.active_addresses(now=5.5), {0x3F})


class ChooseCandidateAddressTests(unittest.TestCase):
    def test_never_chooses_reserved_address(self):
        rng = random.Random(1)
        for _ in range(200):
            candidate = choose_candidate_address(excluded=set(), rng=rng)
            self.assertNotIn(candidate, RESERVED_ADDRESSES)

    def test_never_chooses_excluded_address(self):
        rng = random.Random(2)
        excluded = set(range(1, 255))
        candidate = choose_candidate_address(excluded, rng)
        self.assertEqual(candidate, 255)

    def test_raises_when_no_address_available(self):
        rng = random.Random(3)
        excluded = set(range(0, 256))
        with self.assertRaises(RuntimeError):
            choose_candidate_address(excluded, rng)


class AddressClaimerHappyPathTests(unittest.TestCase):
    def test_uncontended_claim_succeeds(self):
        claimer = AddressClaimer(identity_tail=bytes.fromhex("1E000000302B3E"), rng=random.Random(42))
        self.assertEqual(claimer.state, ClaimState.IDLE)

        frame = claimer.begin_attempt(excluded=set())
        self.assertEqual(claimer.state, ClaimState.AWAITING_WINDOW)
        self.assertEqual(frame.can_id, 0x000)

        claimed = claimer.resolve()
        self.assertTrue(claimed)
        self.assertEqual(claimer.state, ClaimState.CLAIMED)
        self.assertIsNotNone(claimer.claimed_address)

    def test_second_attempt_after_claimed_raises(self):
        claimer = AddressClaimer(identity_tail=bytes.fromhex("1E000000302B3E"), rng=random.Random(42))
        claimer.begin_attempt(excluded=set())
        claimer.resolve()
        with self.assertRaises(RuntimeError):
            claimer.begin_attempt(excluded=set())

    def test_resolve_before_begin_raises(self):
        claimer = AddressClaimer(identity_tail=bytes.fromhex("1E000000302B3E"))
        with self.assertRaises(RuntimeError):
            claimer.resolve()


class AddressClaimerContentionTests(unittest.TestCase):
    def test_contended_candidate_resets_to_idle(self):
        claimer = AddressClaimer(identity_tail=bytes.fromhex("1E000000302B3E"), rng=random.Random(7))
        frame = claimer.begin_attempt(excluded=set())
        claim = decode_claim_frame(frame.data)

        claimer.note_frame_seen(claim.candidate_address)
        claimed = claimer.resolve()

        self.assertFalse(claimed)
        self.assertEqual(claimer.state, ClaimState.IDLE)
        self.assertIsNone(claimer.claimed_address)

    def test_traffic_from_other_addresses_does_not_cause_contention(self):
        claimer = AddressClaimer(identity_tail=bytes.fromhex("1E000000302B3E"), rng=random.Random(7))
        frame = claimer.begin_attempt(excluded=set())
        claim = decode_claim_frame(frame.data)

        other_address = (claim.candidate_address + 1) % 256
        claimer.note_frame_seen(other_address)
        claimed = claimer.resolve()

        self.assertTrue(claimed)

    def test_frame_seen_before_attempt_begins_is_ignored(self):
        claimer = AddressClaimer(identity_tail=bytes.fromhex("1E000000302B3E"), rng=random.Random(7))
        claimer.note_frame_seen(0x2A)  # no attempt in progress yet
        frame = claimer.begin_attempt(excluded=set())
        claim = decode_claim_frame(frame.data)
        if claim.candidate_address == 0x2A:
            self.skipTest("rng happened to pick the address we pre-seeded -- not what this test checks")
        claimed = claimer.resolve()
        self.assertTrue(claimed)

    def test_should_back_off_after_max_fast_retries(self):
        claimer = AddressClaimer(
            identity_tail=bytes.fromhex("1E000000302B3E"), rng=random.Random(7), max_fast_retries=3
        )
        for _ in range(3):
            frame = claimer.begin_attempt(excluded=set())
            claim = decode_claim_frame(frame.data)
            claimer.note_frame_seen(claim.candidate_address)
            self.assertFalse(claimer.resolve())

        self.assertTrue(claimer.should_back_off)

    def test_successful_claim_resets_retry_count(self):
        claimer = AddressClaimer(
            identity_tail=bytes.fromhex("1E000000302B3E"), rng=random.Random(7), max_fast_retries=3
        )
        frame = claimer.begin_attempt(excluded=set())
        claim = decode_claim_frame(frame.data)
        claimer.note_frame_seen(claim.candidate_address)
        claimer.resolve()  # contended, attempt_count=1

        frame = claimer.begin_attempt(excluded=set())
        claimer.resolve()  # uncontended, succeeds and resets

        self.assertFalse(claimer.should_back_off)


class AddressClaimerExclusionTests(unittest.TestCase):
    def test_excluded_addresses_are_never_chosen_as_candidate(self):
        claimer = AddressClaimer(identity_tail=bytes.fromhex("1E000000302B3E"), rng=random.Random(9))
        excluded = set(range(1, 255))
        frame = claimer.begin_attempt(excluded=excluded)
        claim = decode_claim_frame(frame.data)
        self.assertEqual(claim.candidate_address, 255)


if __name__ == "__main__":
    unittest.main()
