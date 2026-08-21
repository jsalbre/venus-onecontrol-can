import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from can_link.session import (
    SESSION_CYPHERS,
    SESSION_ID_DIAGNOSTIC,
    SESSION_ID_REMOTE_CONTROL,
    SessionClient,
    SessionError,
    SessionState,
    tea_transform,
)


class TeaTransformRealHardwareTests(unittest.TestCase):
    # Captured 2026-08-19 from this coach's actual OneControl bus
    # (samples/capture.log, gitignored -- private, contains this coach's
    # addresses), via candump_replay.py while operating two dimmable
    # lights from the OneControl app. 8 independent seed/key pairs across
    # 4 full session handshakes, all matching tea_transform() exactly.
    # This confirms the TEA constants against real hardware -- see TODO.md
    # Phase 0/1, this satisfies that acceptance criterion.
    REAL_SEED_KEY_PAIRS = [
        (0x0F51F82E, 0x78B684CA),
        (0xECE39366, 0xF9F03457),
        (0x1D5E2718, 0x197E9847),
        (0x71E47905, 0xB41D1939),
        (0x896D9922, 0xEE0DE632),
        (0x7E3C1654, 0x1FAF2B5A),
        (0xECF2E11C, 0xB181EA5D),
        (0x9511BFBA, 0x3E22367D),
    ]

    def test_matches_every_captured_real_seed_key_pair(self):
        for seed, expected_key in self.REAL_SEED_KEY_PAIRS:
            with self.subTest(seed=hex(seed)):
                self.assertEqual(tea_transform(seed), expected_key)


class TeaTransformTests(unittest.TestCase):
    def test_deterministic(self):
        self.assertEqual(tea_transform(0x12345678), tea_transform(0x12345678))

    def test_returns_32_bit_value(self):
        result = tea_transform(0xFFFFFFFF)
        self.assertGreaterEqual(result, 0)
        self.assertLessEqual(result, 0xFFFFFFFF)

    def test_different_seeds_produce_different_keys(self):
        self.assertNotEqual(tea_transform(1), tea_transform(2))

    def test_zero_seed_does_not_crash(self):
        tea_transform(0)


class SessionClientHappyPathTests(unittest.TestCase):
    def test_full_handshake_and_close(self):
        client = SessionClient()
        self.assertEqual(client.state, SessionState.IDLE)

        seed_request_payload = client.request_seed(now=0.0)
        self.assertEqual(seed_request_payload, bytes([0x00, 0x04]))
        self.assertEqual(client.state, SessionState.SEED_REQUESTED)

        seed = 0xDEADBEEF
        seed_reply = bytes([0x00, 0x04]) + seed.to_bytes(4, "big")
        key_payload = client.handle_seed_response(seed_reply, now=1.0)
        self.assertEqual(client.state, SessionState.KEY_SENT)
        self.assertEqual(key_payload[:2], bytes([0x00, 0x04]))
        self.assertEqual(len(key_payload), 6)
        self.assertEqual(int.from_bytes(key_payload[2:], "big"), tea_transform(seed))

        client.handle_key_response(now=2.0)
        self.assertEqual(client.state, SessionState.OPEN)

        client.note_activity(now=2.5)

        end_payload = client.session_end(now=3.0)
        self.assertEqual(end_payload, bytes([0x00, 0x04]))
        self.assertEqual(client.state, SessionState.CLOSED)


class GeneralizedSessionIdTests(unittest.TestCase):
    """DIAGNOSTIC (SESSION_ID 2) support, added 2026-08-21 for PID writes
    (see ARCHITECTURE.md's PID Reconfiguration design decision). Pure logic
    tests only -- unlike TeaTransformRealHardwareTests above, there is no
    real captured DIAGNOSTIC handshake to validate against; the DIAGNOSTIC
    cypher constant is decompiled-only."""

    def test_default_session_id_is_remote_control_and_unchanged(self):
        # Regression: omitting session_id must still match every real
        # REMOTE_CONTROL fixture exactly.
        client = SessionClient()
        self.assertEqual(client.session_id, SESSION_ID_REMOTE_CONTROL)
        self.assertEqual(client.request_seed(now=0.0), bytes([0x00, 0x04]))

    def test_diagnostic_session_id_payload_byte(self):
        client = SessionClient(session_id=SESSION_ID_DIAGNOSTIC)
        self.assertEqual(client.request_seed(now=0.0), bytes([0x00, 0x02]))

    def test_diagnostic_handshake_uses_diagnostic_cypher(self):
        client = SessionClient(session_id=SESSION_ID_DIAGNOSTIC)
        client.request_seed(now=0.0)
        seed = 0xDEADBEEF
        seed_reply = bytes([0x00, 0x02]) + seed.to_bytes(4, "big")
        key_payload = client.handle_seed_response(seed_reply, now=1.0)
        expected_key = tea_transform(seed, SESSION_CYPHERS[SESSION_ID_DIAGNOSTIC])
        self.assertEqual(int.from_bytes(key_payload[2:], "big"), expected_key)
        # Must differ from what REMOTE_CONTROL's cypher would produce for
        # the same seed -- otherwise the session_id parameter isn't doing
        # anything.
        self.assertNotEqual(expected_key, tea_transform(seed))

    def test_tea_transform_default_matches_remote_control_cypher(self):
        seed = 0x12345678
        self.assertEqual(tea_transform(seed), tea_transform(seed, SESSION_CYPHERS[SESSION_ID_REMOTE_CONTROL]))


class SessionClientErrorTests(unittest.TestCase):
    def test_cannot_request_seed_twice(self):
        client = SessionClient()
        client.request_seed(now=0.0)
        with self.assertRaises(SessionError):
            client.request_seed(now=0.1)

    def test_cannot_handle_seed_response_before_requesting(self):
        client = SessionClient()
        with self.assertRaises(SessionError):
            client.handle_seed_response(bytes([0x00, 0x04, 0, 0, 0, 0]), now=0.0)

    def test_rejects_malformed_seed_reply_length(self):
        client = SessionClient()
        client.request_seed(now=0.0)
        with self.assertRaises(ValueError):
            client.handle_seed_response(bytes([0x00, 0x04, 0, 0]), now=0.1)

    def test_cannot_send_command_outside_open_session(self):
        client = SessionClient()
        with self.assertRaises(SessionError):
            client.note_activity(now=0.0)

    def test_cannot_handle_key_response_before_key_sent(self):
        client = SessionClient()
        with self.assertRaises(SessionError):
            client.handle_key_response(now=0.0)


class SessionClientExpiryTests(unittest.TestCase):
    def test_expires_open_session_after_timeout(self):
        client = SessionClient(state=SessionState.OPEN)
        client._last_activity = 0.0
        self.assertFalse(client.expire_if_stale(now=4.0))
        self.assertEqual(client.state, SessionState.OPEN)
        self.assertTrue(client.expire_if_stale(now=5.1))
        self.assertEqual(client.state, SessionState.EXPIRED)

    def test_note_activity_resets_expiry_clock(self):
        client = SessionClient(state=SessionState.OPEN)
        client._last_activity = 0.0
        client.note_activity(now=4.9)
        self.assertFalse(client.expire_if_stale(now=9.0))

    def test_idle_and_closed_sessions_never_expire(self):
        idle = SessionClient()
        self.assertFalse(idle.expire_if_stale(now=1000.0))
        closed = SessionClient(state=SessionState.CLOSED)
        closed._last_activity = 0.0
        self.assertFalse(closed.expire_if_stale(now=1000.0))


if __name__ == "__main__":
    unittest.main()
