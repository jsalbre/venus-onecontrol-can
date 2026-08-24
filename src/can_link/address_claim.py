"""CAN source-address self-claiming for this bridge, plus the steady-state
self-announcement frames sent afterward. Required starting Phase 3, since
sending a COMMAND requires a source address of our own -- Phases 0-2 never
transmitted onto the bus at all.

Frame formats below are NOT sourced from community documentation -- they
were decoded directly from a real OneControl power-cycle/reconnect capture
(samples/poweroutage_capture.log, gitignored) after community docs turned
out not to cover address claiming at all. See ARCHITECTURE.md for
the full analysis. Confirmed from that capture:

- Claim frame: standard (11-bit) CAN ID 0x000, 8-byte payload
  [candidate_address, *identity_tail(7 bytes)]. 32 real devices claimed 32
  distinct addresses with zero collisions across the capture.
- ~1.0s after its claim frame (matches decompiled firmware's
  ADDRESS_CLAIM_TIMEOUT exactly), a device begins steady-state NETWORK
  broadcasts at its claimed address: CAN ID = NETWORK message type at that
  source address, payload [0x00, *identity_tail] -- same tail, leading byte
  changes from the candidate address to 0x00.
- identity_tail is NOT a per-device-unique value (contrary to what might be
  assumed) -- 29 of the 32 real claims in the capture shared one identical
  tail (the common Unity relay/light board firmware), with only 3 distinct
  tails total across all device models present. This project has no real
  hardware identity to reuse, so it generates its own synthetic tail once
  (ConfigManager.get_or_create_bridge_identity_tail()) and persists it --
  not an attempt to impersonate any real device model.

This module is pure -- no socket/dbus/gi imports. The claim protocol's
timing (the 1s window, retry backoff) is driven externally by the caller
(publisher.py, via GLib timers) calling into AddressClaimer's methods; the
rest of this module's own logic is unit-testable without a live bus.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import IntEnum

from can_link.frame import CanFrame, encode_standard_id
from can_link.types import DeviceType, MessageType

CLAIM_FRAME_CAN_ID = 0x000
BRIDGE_IDENTITY_TAIL_LENGTH = 7

# Vendor-defined identity this bridge claims on the bus. Both values already
# exist in types.py for exactly this kind of node (a diagnostic/config tool,
# not a physical device) -- not arbitrary/guessed picks.
BRIDGE_DEVICE_TYPE = DeviceType.ONECONTROL_APPLICATION
BRIDGE_FUNCTION_NAME = 1  # "Diagnostic Tool"
BRIDGE_PRODUCT_ID = 0xA0FF  # self-assigned, same family as the D-Bus ProductIds

ADDRESS_CLAIM_WINDOW_SEC = 1.0

# 0x00 must never be claimed: this bridge's own steady-state NETWORK
# broadcast from address 0x00 would encode to CAN ID 0x000 -- identical to
# CLAIM_FRAME_CAN_ID -- making our own traffic indistinguishable from a
# fresh claim attempt. (No real device in the capture ever claimed 0x00
# either, consistent with this being a real protocol-level reservation, not
# just a theoretical concern.)
RESERVED_ADDRESSES = frozenset({0x00})

DEFAULT_ACTIVE_ADDRESS_TTL_SEC = 5.0
DEFAULT_MAX_FAST_RETRIES = 5
DEFAULT_BACKOFF_SEC = 30.0


@dataclass(frozen=True)
class ClaimFrame:
    candidate_address: int
    identity_tail: bytes  # 7 bytes


def encode_claim_frame(candidate_address: int, identity_tail: bytes) -> CanFrame:
    if not (0 <= candidate_address <= 0xFF):
        raise ValueError(f"candidate_address out of range: {candidate_address}")
    if len(identity_tail) != BRIDGE_IDENTITY_TAIL_LENGTH:
        raise ValueError(f"identity_tail must be {BRIDGE_IDENTITY_TAIL_LENGTH} bytes, got {len(identity_tail)}")
    return CanFrame(can_id=CLAIM_FRAME_CAN_ID, is_extended=False, data=bytes([candidate_address]) + identity_tail)


def decode_claim_frame(data: bytes) -> ClaimFrame:
    if len(data) != 8:
        raise ValueError(f"claim frame payload must be 8 bytes, got {len(data)}")
    return ClaimFrame(candidate_address=data[0], identity_tail=bytes(data[1:]))


def encode_network_announce(source_address: int, identity_tail: bytes) -> CanFrame:
    """The steady-state broadcast a device (including, from Phase 3 on,
    this bridge) sends ~1Hz once it holds a claimed address."""
    if len(identity_tail) != BRIDGE_IDENTITY_TAIL_LENGTH:
        raise ValueError(f"identity_tail must be {BRIDGE_IDENTITY_TAIL_LENGTH} bytes, got {len(identity_tail)}")
    can_id = encode_standard_id(source_address, MessageType.NETWORK)
    return CanFrame(can_id=can_id, is_extended=False, data=bytes([0x00]) + identity_tail)


def encode_bridge_device_id_frame(source_address: int) -> CanFrame:
    """This bridge's own DEVICE_ID self-announcement, using the byte layout
    device_id.py decodes (product_id, product_instance, device_type,
    function_name, device_instance<<4|function_instance, capabilities)."""
    payload = bytes(
        [
            (BRIDGE_PRODUCT_ID >> 8) & 0xFF,
            BRIDGE_PRODUCT_ID & 0xFF,
            0x00,  # product_instance -- only one bridge instance ever runs
            int(BRIDGE_DEVICE_TYPE),
            (BRIDGE_FUNCTION_NAME >> 8) & 0xFF,
            BRIDGE_FUNCTION_NAME & 0xFF,
            0x00,  # device_instance=0, function_instance=0
            0x00,  # capabilities -- nothing declared
        ]
    )
    can_id = encode_standard_id(source_address, MessageType.DEVICE_ID)
    return CanFrame(can_id=can_id, is_extended=False, data=payload)


class ActiveAddressTracker:
    """Tracks which CAN source addresses have produced traffic recently, for
    claim candidate selection. Deliberately broader than address_table.py's
    StableKey-keyed map -- marks an address in-use on ANY frame from it
    (matching the real firmware's own "any traffic marks an address in-use"
    behavior), not only on a decoded DEVICE_ID. Fed from the same raw frame
    stream as address_table.py, so it's naturally a superset of every
    address address_table.py currently tracks -- no separate query into
    address_table.py is needed for candidate exclusion."""

    def __init__(self, ttl_sec: float = DEFAULT_ACTIVE_ADDRESS_TTL_SEC) -> None:
        self._ttl_sec = ttl_sec
        self._last_seen: dict[int, float] = {}

    def note_address(self, source_address: int, now: float) -> None:
        self._last_seen[source_address] = now

    def is_active(self, source_address: int, now: float) -> bool:
        last = self._last_seen.get(source_address)
        return last is not None and (now - last) <= self._ttl_sec

    def active_addresses(self, now: float) -> set[int]:
        return {addr for addr, last in self._last_seen.items() if (now - last) <= self._ttl_sec}


def choose_candidate_address(excluded: set[int], rng: random.Random) -> int:
    available = [a for a in range(1, 256) if a not in excluded and a not in RESERVED_ADDRESSES]
    if not available:
        raise RuntimeError("No CAN address available to claim -- every address 1-255 appears active")
    return rng.choice(available)


class ClaimState(IntEnum):
    IDLE = 0
    AWAITING_WINDOW = 1
    CLAIMED = 2


class AddressClaimer:
    """Pure state machine for claiming this bridge's own CAN source address.

    Does not send or receive frames itself. The caller (publisher.py) drives
    it: begin_attempt() returns the claim CanFrame to transmit and starts a
    1s window; note_frame_seen() is called for every frame observed on the
    bus during that window (source address only); resolve() is called once
    the window has elapsed (via a GLib one-shot timer) to find out whether
    the claim succeeded.
    """

    def __init__(
        self,
        identity_tail: bytes,
        rng: random.Random | None = None,
        max_fast_retries: int = DEFAULT_MAX_FAST_RETRIES,
        backoff_sec: float = DEFAULT_BACKOFF_SEC,
    ) -> None:
        if len(identity_tail) != BRIDGE_IDENTITY_TAIL_LENGTH:
            raise ValueError(f"identity_tail must be {BRIDGE_IDENTITY_TAIL_LENGTH} bytes, got {len(identity_tail)}")
        self._identity_tail = identity_tail
        self._rng = rng or random.Random()
        self._max_fast_retries = max_fast_retries
        self.backoff_sec = backoff_sec

        self.state = ClaimState.IDLE
        self.claimed_address: int | None = None
        self._candidate: int | None = None
        self._contended = False
        self._attempt_count = 0

    def begin_attempt(self, excluded: set[int]) -> CanFrame:
        if self.state == ClaimState.CLAIMED:
            raise RuntimeError("address already claimed -- cannot begin a new attempt")
        self._candidate = choose_candidate_address(excluded, self._rng)
        self._contended = False
        self.state = ClaimState.AWAITING_WINDOW
        return encode_claim_frame(self._candidate, self._identity_tail)

    def note_frame_seen(self, source_address: int) -> None:
        """Call for every received frame's source address while a claim
        window is open. Marks the attempt contended if some other device is
        using (or is also claiming) our candidate address."""
        if self.state == ClaimState.AWAITING_WINDOW and source_address == self._candidate:
            self._contended = True

    def resolve(self) -> bool:
        """Call once the 1s claim window has elapsed. Returns True if the
        candidate is now claimed (state becomes CLAIMED); False if contended
        (state resets to IDLE so begin_attempt() can be called again)."""
        if self.state != ClaimState.AWAITING_WINDOW:
            raise RuntimeError(f"cannot resolve from state {self.state.name}")
        if self._contended:
            self.state = ClaimState.IDLE
            self._attempt_count += 1
            return False
        self.state = ClaimState.CLAIMED
        self.claimed_address = self._candidate
        self._attempt_count = 0
        return True

    @property
    def should_back_off(self) -> bool:
        """True once max_fast_retries consecutive contended attempts have
        happened -- caller should wait backoff_sec before calling
        begin_attempt() again, instead of retrying immediately."""
        return self._attempt_count >= self._max_fast_retries
