"""Async command-attempt orchestration: drives one device's TEA session
handshake through to a COMMAND (and immediate SESSION_END), composing
session.py's SessionClient and a caller-supplied command.py builder.
Neither of those modules is modified by this one.

The safety-critical property this module exists to provide: the command
safety gate (address_table.resolve_for_command) is re-checked immediately
before the COMMAND frame is built -- not only when the attempt started.
Minutes don't pass during a real handshake (round trips are ~1-3ms), but a
bus outage can be detected in that window, and this is the last point
before a frame with real physical effect goes out.

Pure -- no socket/dbus/gi imports. The caller (publisher.py) is responsible
for actually transmitting returned CanFrames, routing inbound RESPONSE
frames to the right CommandAttempt, and driving real time into
check_timeout() via a GLib timer. Multiple CommandAttempts can be in flight
at once (one per device with a write in progress); nothing here is
shared/global state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable

from can_link.frame import CanFrame, encode_extended_id
from can_link.session import (
    REQUEST_CODE_SESSION_END,
    REQUEST_CODE_SESSION_REQUEST_SEED,
    REQUEST_CODE_SESSION_TRANSMIT_KEY,
    SessionClient,
    SessionError,
)
from can_link.types import MessageType, StableKey

DEFAULT_STEP_TIMEOUT_SEC = 2.0

ResolveForCommand = Callable[[StableKey, float], "int | None"]
BuildCommandFrame = Callable[[int, int], CanFrame]


class CommandAttemptState(IntEnum):
    AWAITING_SEED_RESPONSE = 0
    AWAITING_KEY_RESPONSE = 1
    AWAITING_END_RESPONSE = 2
    DONE = 3


def _request_frame(source_address: int, target_address: int, request_code: int, payload: bytes) -> CanFrame:
    can_id = encode_extended_id(
        source_address=source_address,
        target_address=target_address,
        message_data=request_code,
        message_type=MessageType.REQUEST,
    )
    return CanFrame(can_id=can_id, is_extended=True, data=payload)


@dataclass
class CommandAttempt:
    """One in-flight command against one device. Construct with the target
    address already resolved by the caller's own early check -- this class
    re-verifies it via resolve_for_command right before sending COMMAND,
    and aborts (closing the session, but never sending COMMAND) if the
    device is no longer command-eligible by then."""

    key: StableKey
    source_address: int
    target_address: int
    build_command_frame: BuildCommandFrame
    resolve_for_command: ResolveForCommand
    session: SessionClient = field(default_factory=SessionClient)
    state: CommandAttemptState = CommandAttemptState.AWAITING_SEED_RESPONSE
    succeeded: bool | None = None  # None while in progress
    failure_reason: str | None = None
    _last_step_time: float = 0.0

    def start(self, now: float) -> CanFrame:
        self._last_step_time = now
        payload = self.session.request_seed(now)
        return _request_frame(self.source_address, self.target_address, REQUEST_CODE_SESSION_REQUEST_SEED, payload)

    def handle_response(self, request_code: int, payload: bytes, now: float) -> list[CanFrame]:
        """Advances the state machine given an inbound RESPONSE already
        confirmed to be from this attempt's target device. request_code is
        the RESPONSE's message_data byte (echoes the REQUEST code it
        answers). Returns the frame(s) to send next -- empty once done."""
        self._last_step_time = now
        try:
            if self.state == CommandAttemptState.AWAITING_SEED_RESPONSE:
                if request_code != REQUEST_CODE_SESSION_REQUEST_SEED:
                    return self._fail(f"unexpected response code {request_code:#x} while awaiting seed")
                key_payload = self.session.handle_seed_response(payload, now)
                self.state = CommandAttemptState.AWAITING_KEY_RESPONSE
                return [
                    _request_frame(
                        self.source_address, self.target_address, REQUEST_CODE_SESSION_TRANSMIT_KEY, key_payload
                    )
                ]

            if self.state == CommandAttemptState.AWAITING_KEY_RESPONSE:
                if request_code != REQUEST_CODE_SESSION_TRANSMIT_KEY:
                    return self._fail(f"unexpected response code {request_code:#x} while awaiting key ack")
                self.session.handle_key_response(now)
                return self._verify_and_send_command(now)

            if self.state == CommandAttemptState.AWAITING_END_RESPONSE:
                if request_code != REQUEST_CODE_SESSION_END:
                    return self._fail(f"unexpected response code {request_code:#x} while awaiting session end")
                self.state = CommandAttemptState.DONE
                if self.succeeded is None:
                    self.succeeded = True
                return []
        except (SessionError, ValueError) as e:
            return self._fail(f"session handshake error: {e}")

        return self._fail(f"response received in terminal state {self.state.name}")

    def _verify_and_send_command(self, now: float) -> list[CanFrame]:
        live_address = self.resolve_for_command(self.key, now)
        if live_address != self.target_address:
            self.succeeded = False
            self.failure_reason = (
                "device is no longer command-eligible at the expected address "
                f"(expected {self.target_address:#x}, resolved {live_address!r}) -- COMMAND was not sent"
            )
            end_payload = self.session.session_end(now)
            self.state = CommandAttemptState.AWAITING_END_RESPONSE
            return [_request_frame(self.source_address, self.target_address, REQUEST_CODE_SESSION_END, end_payload)]

        command_frame = self.build_command_frame(self.source_address, self.target_address)
        self.session.note_activity(now)
        end_payload = self.session.session_end(now)
        self.state = CommandAttemptState.AWAITING_END_RESPONSE
        return [
            command_frame,
            _request_frame(self.source_address, self.target_address, REQUEST_CODE_SESSION_END, end_payload),
        ]

    def _fail(self, reason: str) -> list[CanFrame]:
        # Deliberately sends nothing further -- an unexpected response or a
        # handshake protocol error means the exchange is already confused;
        # the device's own 5s session timeout cleans up its side. This
        # branch never has physical effect (no COMMAND frame is built).
        self.succeeded = False
        self.failure_reason = reason
        self.state = CommandAttemptState.DONE
        return []

    def abort(self, reason: str) -> None:
        """Immediate hard-stop -- e.g. a bus outage was just detected mid
        handshake. Never sends another frame, including SESSION_END."""
        self.succeeded = False
        self.failure_reason = reason
        self.state = CommandAttemptState.DONE

    def check_timeout(self, now: float, timeout_sec: float = DEFAULT_STEP_TIMEOUT_SEC) -> bool:
        """True if this attempt hasn't advanced within timeout_sec and
        isn't already done. Caller should then call abort()."""
        return self.state != CommandAttemptState.DONE and (now - self._last_step_time) > timeout_sec

    @property
    def is_done(self) -> bool:
        return self.state == CommandAttemptState.DONE
