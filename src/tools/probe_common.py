"""Shared bus-setup/address-claim/request-response plumbing for the manual
CAN diagnostic tools (pid_probe.py, pid_write.py). Extracted from
pid_probe.py (2026-08-21) once a second tool needed the identical logic --
not a speculative abstraction, both real callers exist.

Not used by publisher.py or any production code path.
"""

from __future__ import annotations

import os
import select
import time

from bus.socketcan import SocketCanBus
from can_link import address_claim
from can_link.device_id import decode_device_id, stable_key
from can_link.frame import CanFrame, ExtendedId, StandardId, decode_id, encode_extended_id
from can_link.types import MessageType, StableKey

DEFAULT_INTERFACE = "vecan1"
DEFAULT_RESPONSE_TIMEOUT_SEC = 2.0
DEFAULT_LISTEN_TIMEOUT_SEC = 15.0


def _recv_with_timeout(bus: SocketCanBus, timeout_sec: float):
    if timeout_sec <= 0:
        return None
    ready, _, _ = select.select([bus.fileno()], [], [], timeout_sec)
    if not ready:
        return None
    frame = bus.recv()
    return frame.can_id, frame.is_extended, frame.data


def claim_bridge_address(bus: SocketCanBus) -> int:
    identity_tail = os.urandom(address_claim.BRIDGE_IDENTITY_TAIL_LENGTH)
    active = address_claim.ActiveAddressTracker()
    claimer = address_claim.AddressClaimer(identity_tail=identity_tail)

    while True:
        frame = claimer.begin_attempt(active.active_addresses(time.time()))
        bus.send(frame)

        deadline = time.time() + address_claim.ADDRESS_CLAIM_WINDOW_SEC
        while time.time() < deadline:
            result = _recv_with_timeout(bus, deadline - time.time())
            if result is None:
                continue
            can_id, is_extended, data = result
            now = time.time()
            if not is_extended and can_id == address_claim.CLAIM_FRAME_CAN_ID:
                try:
                    claim = address_claim.decode_claim_frame(data)
                except ValueError:
                    continue
                active.note_address(claim.candidate_address, now)
                claimer.note_frame_seen(claim.candidate_address)
            else:
                decoded = decode_id(can_id, is_extended)
                active.note_address(decoded.source_address, now)
                claimer.note_frame_seen(decoded.source_address)

        if claimer.resolve():
            return claimer.claimed_address

        if claimer.should_back_off:
            print(f"Address claim contended repeatedly, backing off {claimer.backoff_sec:.0f}s...")
            time.sleep(claimer.backoff_sec)


def resolve_target_address(bus: SocketCanBus, target_key: StableKey, listen_timeout_sec: float) -> int | None:
    print(f"Listening up to {listen_timeout_sec:.0f}s for a DEVICE_ID broadcast matching {target_key.to_config_string()}...")
    deadline = time.time() + listen_timeout_sec
    while time.time() < deadline:
        result = _recv_with_timeout(bus, deadline - time.time())
        if result is None:
            continue
        can_id, is_extended, data = result
        if is_extended:
            continue
        decoded = decode_id(can_id, is_extended)
        assert isinstance(decoded, StandardId)
        if decoded.message_type != MessageType.DEVICE_ID:
            continue
        try:
            identity = decode_device_id(data)
        except ValueError:
            continue
        if stable_key(identity) == target_key:
            return decoded.source_address
    return None


def wait_for_response(bus: SocketCanBus, source: int, target: int, request_code: int, timeout_sec: float) -> bytes | None:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        result = _recv_with_timeout(bus, deadline - time.time())
        if result is None:
            continue
        can_id, is_extended, data = result
        if not is_extended:
            continue
        decoded = decode_id(can_id, is_extended)
        assert isinstance(decoded, ExtendedId)
        if (
            decoded.message_type == MessageType.RESPONSE
            and decoded.source_address == target
            and decoded.target_address == source
            and decoded.message_data == request_code
        ):
            return data
    return None


def send_request(bus: SocketCanBus, source: int, target: int, request_code: int, payload: bytes) -> None:
    can_id = encode_extended_id(
        source_address=source, target_address=target, message_data=request_code, message_type=MessageType.REQUEST
    )
    bus.send(CanFrame(can_id=can_id, is_extended=True, data=payload))
