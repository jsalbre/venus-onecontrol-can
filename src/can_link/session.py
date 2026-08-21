"""TEA-cipher session handshake, generalized across SESSION_ID (each session
type uses the same tea_transform()-shaped algorithm with its own cypher
constant -- see SESSION_CYPHERS). SESSION_ID 4 ("REMOTE_CONTROL") is
required before any COMMAND frame is honored and is this module's default.
See dev-notes/ARCHITECTURE.md.

CONFIRMED against real hardware: 8 independent seed/key pairs captured from
this coach's actual OneControl bus (2026-08-19, see
tests/test_session.py::TeaTransformRealHardwareTests) all match
tea_transform() exactly, using the default REMOTE_CONTROL cypher. Other
session types (e.g. DIAGNOSTIC, SESSION_ID 2, used for PID writes) are
decompiled-only -- no real handshake capture exists for them yet.

This module is pure -- no socket I/O. Callers combine build_*() outputs with
frame.encode_extended_id(..., message_type=REQUEST/COMMAND, message_data=...)
and send/receive bytes via bus/socketcan.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

TEA_SESSION_CONSTANT = 0xB16B00B5
TEA_DELTA = 0x9E3779B9
TEA_ROUNDS = 32
_TEA_MAGIC_A = 1131376761
_TEA_MAGIC_B = 1919510376
_TEA_MAGIC_C = 1948272964
_TEA_MAGIC_D = 1400073827
_MASK32 = 0xFFFFFFFF

SESSION_ID_DIAGNOSTIC = 2
SESSION_ID_REMOTE_CONTROL = 4
SESSION_TIMEOUT_SEC = 5.0

REQUEST_CODE_SESSION_REQUEST_SEED = 0x42  # 66
REQUEST_CODE_SESSION_TRANSMIT_KEY = 0x43  # 67
REQUEST_CODE_SESSION_END = 0x45  # 69

# Full catalog per decompiled `SESSION_ID` class (dev-notes/ARCHITECTURE.md).
# Only DIAGNOSTIC (2) and REMOTE_CONTROL (4) are used by this project;
# REMOTE_CONTROL is real-hardware-proven (8 captured handshakes), the rest
# are decompiled-only and unconfirmed against real traffic.
SESSION_CYPHERS: dict[int, int] = {
    1: 0xB16BA115,  # MANUFACTURING
    2: 0xBABECAFE,  # DIAGNOSTIC
    3: 0xDEADBEEF,  # REPROGRAMMING
    4: 0xB16B00B5,  # REMOTE_CONTROL (== TEA_SESSION_CONSTANT)
    5: 0x0B00B135,  # DAQ
}


def tea_transform(seed: int, session_constant: int = TEA_SESSION_CONSTANT) -> int:
    """32-round modified TEA transform. Given the 32-bit seed from a
    SESSION_REQUEST_SEED reply, returns the 32-bit key for
    SESSION_TRANSMIT_KEY. session_constant defaults to the real-hardware
    -proven REMOTE_CONTROL cypher; pass a different SESSION_CYPHERS value
    for another session type."""
    v = seed & _MASK32
    k = session_constant
    d = TEA_DELTA
    for _ in range(TEA_ROUNDS):
        v = (v + (((k << 4) + _TEA_MAGIC_A) ^ (k + d) ^ ((k >> 5) + _TEA_MAGIC_B))) & _MASK32
        k = (k + (((v << 4) + _TEA_MAGIC_C) ^ (v + d) ^ ((v >> 5) + _TEA_MAGIC_D))) & _MASK32
        d = (d + TEA_DELTA) & _MASK32
    return v


class SessionState(IntEnum):
    IDLE = 0
    SEED_REQUESTED = 1
    KEY_SENT = 2
    OPEN = 3
    CLOSED = 4
    EXPIRED = 5


class SessionError(Exception):
    """Raised on a handshake step attempted from the wrong state, or a
    malformed response payload."""


@dataclass
class SessionClient:
    """State machine for one device's session. One instance per target
    device -- sessions are not shared across devices. Defaults to
    REMOTE_CONTROL (SESSION_ID 4, real-hardware-proven); pass a different
    session_id (see SESSION_CYPHERS) for e.g. a DIAGNOSTIC session."""

    state: SessionState = SessionState.IDLE
    session_id: int = SESSION_ID_REMOTE_CONTROL
    _last_activity: float = 0.0

    def _session_id_payload(self) -> bytes:
        return bytes([0x00, self.session_id])

    def request_seed(self, now: float) -> bytes:
        if self.state != SessionState.IDLE:
            raise SessionError(f"cannot request seed from state {self.state.name}")
        self.state = SessionState.SEED_REQUESTED
        self._last_activity = now
        return self._session_id_payload()

    def handle_seed_response(self, payload: bytes, now: float) -> bytes:
        if self.state != SessionState.SEED_REQUESTED:
            raise SessionError(f"unexpected seed response in state {self.state.name}")
        if len(payload) != 6:
            raise ValueError(f"SESSION_REQUEST_SEED reply must be 6 bytes, got {len(payload)}")
        seed = int.from_bytes(payload[2:6], byteorder="big")
        key = tea_transform(seed, SESSION_CYPHERS[self.session_id])
        self.state = SessionState.KEY_SENT
        self._last_activity = now
        return self._session_id_payload() + key.to_bytes(4, byteorder="big")

    def handle_key_response(self, now: float) -> None:
        if self.state != SessionState.KEY_SENT:
            raise SessionError(f"unexpected key response in state {self.state.name}")
        self.state = SessionState.OPEN
        self._last_activity = now

    def note_activity(self, now: float) -> None:
        """Call when a COMMAND frame is sent through this open session, to
        reset the 5s silence timeout."""
        if self.state != SessionState.OPEN:
            raise SessionError("cannot send a command outside an OPEN session")
        self._last_activity = now

    def session_end(self, now: float) -> bytes:
        self.state = SessionState.CLOSED
        self._last_activity = now
        return self._session_id_payload()

    def expire_if_stale(self, now: float, timeout_sec: float = SESSION_TIMEOUT_SEC) -> bool:
        """Transitions to EXPIRED if more than timeout_sec has passed since
        the last handshake step or command. Returns True if it just expired."""
        if self.state in (SessionState.SEED_REQUESTED, SessionState.KEY_SENT, SessionState.OPEN):
            if now - self._last_activity > timeout_sec:
                self.state = SessionState.EXPIRED
                return True
        return False
