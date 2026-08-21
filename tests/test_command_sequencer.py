import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from can_link.command import build_dimmable_light_toggle_command
from can_link.command_sequencer import CommandAttempt, CommandAttemptState
from can_link.frame import decode_id
from can_link.types import MessageType, StableKey

LIGHT_KEY = StableKey("function_name", 49, 1)  # Awning Light 1
BRIDGE_ADDRESS = 0xF9
DEVICE_ADDRESS = 0x1D


def _turn_on_attempt(resolve_for_command):
    return CommandAttempt(
        key=LIGHT_KEY,
        source_address=BRIDGE_ADDRESS,
        target_address=DEVICE_ADDRESS,
        build_command_frame=lambda src, dst: build_dimmable_light_toggle_command(src, dst, turn_on=True),
        resolve_for_command=resolve_for_command,
    )


class HappyPathRealHardwareTraceTests(unittest.TestCase):
    # The exact real handshake captured 2026-08-19 for src=0xF9 (OneControl
    # app) -> dst=0x1D (Awning Light 1), samples/capture.log. Round trips
    # were ~1-3ms in practice; timestamps below are illustrative, not
    # literally the captured ones.
    def test_full_handshake_matches_real_captured_bytes(self):
        attempt = _turn_on_attempt(resolve_for_command=lambda key, now: DEVICE_ADDRESS)

        seed_request = attempt.start(now=0.0)
        self.assertEqual(seed_request.data, bytes([0x00, 0x04]))
        decoded = decode_id(seed_request.can_id, is_extended=True)
        self.assertEqual(decoded.message_data, 0x42)
        self.assertEqual(decoded.message_type, MessageType.REQUEST)
        self.assertEqual(decoded.source_address, BRIDGE_ADDRESS)
        self.assertEqual(decoded.target_address, DEVICE_ADDRESS)

        key_request_frames = attempt.handle_response(
            request_code=0x42, payload=bytes.fromhex("00040f51f82e"), now=0.002
        )
        self.assertEqual(len(key_request_frames), 1)
        self.assertEqual(key_request_frames[0].data, bytes.fromhex("000478b684ca"))
        self.assertEqual(attempt.state, CommandAttemptState.AWAITING_KEY_RESPONSE)

        command_and_end_frames = attempt.handle_response(request_code=0x43, payload=bytes.fromhex("0004"), now=0.004)
        self.assertEqual(len(command_and_end_frames), 2)
        command_frame, end_frame = command_and_end_frames
        self.assertEqual(command_frame.data, bytes.fromhex("7f00000000000000"))
        self.assertEqual(end_frame.data, bytes([0x00, 0x04]))
        end_decoded = decode_id(end_frame.can_id, is_extended=True)
        self.assertEqual(end_decoded.message_data, 0x45)
        self.assertEqual(attempt.state, CommandAttemptState.AWAITING_END_RESPONSE)
        self.assertIsNone(attempt.succeeded)  # not finalized until the end-response arrives

        final_frames = attempt.handle_response(request_code=0x45, payload=bytes.fromhex("000400"), now=1.023)
        self.assertEqual(final_frames, [])
        self.assertEqual(attempt.state, CommandAttemptState.DONE)
        self.assertTrue(attempt.succeeded)
        self.assertTrue(attempt.is_done)


class SecondSafetyCheckTests(unittest.TestCase):
    def test_aborts_without_sending_command_if_device_no_longer_resolves(self):
        attempt = _turn_on_attempt(resolve_for_command=lambda key, now: None)
        attempt.start(now=0.0)
        attempt.handle_response(request_code=0x42, payload=bytes.fromhex("00040f51f82e"), now=0.002)

        frames = attempt.handle_response(request_code=0x43, payload=bytes.fromhex("0004"), now=0.004)

        self.assertEqual(len(frames), 1)  # only SESSION_END, no COMMAND
        decoded = decode_id(frames[0].can_id, is_extended=True)
        self.assertEqual(decoded.message_data, 0x45)
        self.assertFalse(attempt.succeeded)
        self.assertIn("no longer command-eligible", attempt.failure_reason)

    def test_aborts_if_device_moved_to_a_different_address(self):
        attempt = _turn_on_attempt(resolve_for_command=lambda key, now: 0x99)
        attempt.start(now=0.0)
        attempt.handle_response(request_code=0x42, payload=bytes.fromhex("00040f51f82e"), now=0.002)

        frames = attempt.handle_response(request_code=0x43, payload=bytes.fromhex("0004"), now=0.004)

        self.assertEqual(len(frames), 1)
        self.assertFalse(attempt.succeeded)

    def test_final_response_after_abort_stays_failed(self):
        attempt = _turn_on_attempt(resolve_for_command=lambda key, now: None)
        attempt.start(now=0.0)
        attempt.handle_response(request_code=0x42, payload=bytes.fromhex("00040f51f82e"), now=0.002)
        attempt.handle_response(request_code=0x43, payload=bytes.fromhex("0004"), now=0.004)

        attempt.handle_response(request_code=0x45, payload=bytes.fromhex("000400"), now=0.010)

        self.assertEqual(attempt.state, CommandAttemptState.DONE)
        self.assertFalse(attempt.succeeded)


class UnexpectedResponseTests(unittest.TestCase):
    def test_wrong_code_while_awaiting_seed_fails_closed(self):
        attempt = _turn_on_attempt(resolve_for_command=lambda key, now: DEVICE_ADDRESS)
        attempt.start(now=0.0)

        frames = attempt.handle_response(request_code=0x43, payload=b"\x00\x04", now=0.002)

        self.assertEqual(frames, [])
        self.assertFalse(attempt.succeeded)
        self.assertTrue(attempt.is_done)

    def test_malformed_seed_payload_fails_closed(self):
        attempt = _turn_on_attempt(resolve_for_command=lambda key, now: DEVICE_ADDRESS)
        attempt.start(now=0.0)

        frames = attempt.handle_response(request_code=0x42, payload=b"\x00\x04\x00", now=0.002)

        self.assertEqual(frames, [])
        self.assertFalse(attempt.succeeded)
        self.assertTrue(attempt.is_done)

    def test_response_after_done_fails_closed(self):
        attempt = _turn_on_attempt(resolve_for_command=lambda key, now: DEVICE_ADDRESS)
        attempt.start(now=0.0)
        attempt.handle_response(request_code=0x43, payload=b"\x00\x04", now=0.002)  # wrong code -> DONE
        self.assertTrue(attempt.is_done)

        frames = attempt.handle_response(request_code=0x42, payload=b"\x00\x04\x00\x00\x00\x00", now=0.003)
        self.assertEqual(frames, [])


class OutageAbortTests(unittest.TestCase):
    def test_abort_marks_done_and_failed_with_no_further_frames(self):
        attempt = _turn_on_attempt(resolve_for_command=lambda key, now: DEVICE_ADDRESS)
        attempt.start(now=0.0)

        attempt.abort("bus outage detected")

        self.assertTrue(attempt.is_done)
        self.assertFalse(attempt.succeeded)
        self.assertEqual(attempt.failure_reason, "bus outage detected")

    def test_abort_at_any_state_is_safe(self):
        attempt = _turn_on_attempt(resolve_for_command=lambda key, now: DEVICE_ADDRESS)
        attempt.abort("bus outage detected")  # before start() was even called
        self.assertTrue(attempt.is_done)


class TimeoutTests(unittest.TestCase):
    def test_not_timed_out_before_threshold(self):
        attempt = _turn_on_attempt(resolve_for_command=lambda key, now: DEVICE_ADDRESS)
        attempt.start(now=0.0)
        self.assertFalse(attempt.check_timeout(now=1.9, timeout_sec=2.0))

    def test_timed_out_after_threshold(self):
        attempt = _turn_on_attempt(resolve_for_command=lambda key, now: DEVICE_ADDRESS)
        attempt.start(now=0.0)
        self.assertTrue(attempt.check_timeout(now=2.1, timeout_sec=2.0))

    def test_timeout_clock_resets_on_each_step(self):
        attempt = _turn_on_attempt(resolve_for_command=lambda key, now: DEVICE_ADDRESS)
        attempt.start(now=0.0)
        attempt.handle_response(request_code=0x42, payload=bytes.fromhex("00040f51f82e"), now=1.9)
        self.assertFalse(attempt.check_timeout(now=3.5, timeout_sec=2.0))

    def test_done_attempt_never_times_out(self):
        attempt = _turn_on_attempt(resolve_for_command=lambda key, now: DEVICE_ADDRESS)
        attempt.start(now=0.0)
        attempt.abort("done")
        self.assertFalse(attempt.check_timeout(now=1000.0, timeout_sec=2.0))


if __name__ == "__main__":
    unittest.main()
