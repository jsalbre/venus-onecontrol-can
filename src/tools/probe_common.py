"""Shared bus-setup/address-claim/request-response plumbing for the manual
CAN diagnostic tools (pid_probe.py, pid_write.py, relay_blip.py,
list_unconfigured.py, manage-system). Extracted from pid_probe.py
(2026-08-21) once a second tool needed the identical logic -- not a
speculative abstraction, real callers exist for everything here.

Not used by publisher.py or any production code path.
"""

from __future__ import annotations

import os
import select
import time
from dataclasses import dataclass, field

from bus.socketcan import SocketCanBus
from can_link import address_claim
from can_link.command import RelayCommandMode, build_dimmable_light_toggle_command, build_relay_command
from can_link.device_id import DeviceIdentity, decode_device_id, stable_key
from can_link.frame import CanFrame, ExtendedId, StandardId, decode_id, encode_extended_id
from can_link.session import (
    REQUEST_CODE_SESSION_END,
    REQUEST_CODE_SESSION_REQUEST_SEED,
    REQUEST_CODE_SESSION_TRANSMIT_KEY,
    SESSION_ID_REMOTE_CONTROL,
    SessionClient,
    SessionError,
)
from can_link.types import DeviceType, MessageType, StableKey

DEFAULT_INTERFACE = "vecan1"
DEFAULT_RESPONSE_TIMEOUT_SEC = 2.0
DEFAULT_LISTEN_TIMEOUT_SEC = 15.0

# Session auto-expires after 5s of silence (session.py SESSION_TIMEOUT_SEC).
# Confirmed for real on real hardware (2026-08-22, relay_blip.py's first
# run): a hold that reaches the timeout means the OFF/off-toggle command
# arrives after the device already closed the session and silently ignores
# it. MAX leaves margin for handshake round-trip time already elapsed
# before the hold starts.
DEFAULT_TEST_HOLD_SECONDS = 2.0
MAX_TEST_HOLD_SECONDS = 4.0


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
    resolved = resolve_target_identity(bus, target_key, listen_timeout_sec)
    return resolved[0] if resolved else None


def resolve_target_identity(
    bus: SocketCanBus, target_key: StableKey, listen_timeout_sec: float
) -> tuple[int, DeviceIdentity] | None:
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
            return decoded.source_address, identity
    return None


@dataclass(frozen=True)
class BoardScanResult:
    reference_address: int | None
    reference_product: tuple[int, int] | None
    compare_resolved: dict[StableKey, tuple[int, DeviceIdentity]] = field(default_factory=dict)
    unconfigured: dict[int, DeviceIdentity] = field(default_factory=dict)  # address -> identity, FUNCTION_NAME=0 only
    all_devices: dict[int, DeviceIdentity] = field(default_factory=dict)  # address -> identity, every device seen


def scan_board(
    bus: SocketCanBus,
    reference_key: StableKey,
    listen_timeout_sec: float,
    compare_keys: tuple[StableKey, ...] = (),
) -> BoardScanResult:
    """Listens for DEVICE_ID broadcasts, resolving reference_key's
    (PRODUCT_ID, product_instance) and collecting every currently
    unconfigured (FUNCTION_NAME=0) device seen, plus any compare_keys
    found along the way (for device_instance comparison against known
    devices). Does NOT filter `unconfigured` down to reference_product's
    board on its own -- product_instance turned out not to be reliably
    per-board (see ARCHITECTURE.md's "device_instance" design decision),
    so that filtering (if any) is a cheap second pass the caller does
    against the returned reference_product."""
    reference_address: int | None = None
    reference_product: tuple[int, int] | None = None
    compare_resolved: dict[StableKey, tuple[int, DeviceIdentity]] = {}
    unconfigured: dict[int, DeviceIdentity] = {}
    all_devices: dict[int, DeviceIdentity] = {}

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

        key = stable_key(identity)
        if key == reference_key and reference_product is None:
            reference_address = decoded.source_address
            reference_product = (identity.product_id, identity.product_instance)
        if key in compare_keys and key not in compare_resolved:
            compare_resolved[key] = (decoded.source_address, identity)
        if identity.function_name == 0:
            unconfigured[decoded.source_address] = identity
        all_devices[decoded.source_address] = identity

    return BoardScanResult(reference_address, reference_product, compare_resolved, unconfigured, all_devices)


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


class SessionOpenError(Exception):
    """Raised when a session handshake step fails or times out. If the
    handshake fails partway (not on the very first request), a SESSION_END
    is still attempted before this is raised -- cheap and harmless even if
    the device never actually opened anything."""


def open_session(bus: SocketCanBus, source: int, target: int, session_id: int, timeout_sec: float) -> SessionClient:
    session = SessionClient(session_id=session_id)
    try:
        seed_payload = session.request_seed(time.time())
        send_request(bus, source, target, REQUEST_CODE_SESSION_REQUEST_SEED, seed_payload)
        seed_reply = wait_for_response(bus, source, target, REQUEST_CODE_SESSION_REQUEST_SEED, timeout_sec)
        if seed_reply is None:
            raise SessionOpenError("no response to SESSION_REQUEST_SEED -- is this address currently live?")
        print(f"  SESSION_REQUEST_SEED reply: raw={seed_reply.hex()}")
        try:
            key_payload = session.handle_seed_response(seed_reply, time.time())
        except (SessionError, ValueError) as e:
            raise SessionOpenError(f"SESSION_REQUEST_SEED reply rejected: {e}") from e

        send_request(bus, source, target, REQUEST_CODE_SESSION_TRANSMIT_KEY, key_payload)
        key_reply = wait_for_response(bus, source, target, REQUEST_CODE_SESSION_TRANSMIT_KEY, timeout_sec)
        if key_reply is None:
            raise SessionOpenError("no response to SESSION_TRANSMIT_KEY")
        print(f"  SESSION_TRANSMIT_KEY reply: raw={key_reply.hex()}")
        try:
            session.handle_key_response(time.time())
        except SessionError as e:
            raise SessionOpenError(f"SESSION_TRANSMIT_KEY reply rejected: {e}") from e
    except SessionOpenError:
        close_session(bus, source, target, session)
        raise

    return session


def close_session(bus: SocketCanBus, source: int, target: int, session: SessionClient) -> None:
    end_payload = session.session_end(time.time())
    send_request(bus, source, target, REQUEST_CODE_SESSION_END, end_payload)


def send_test_blip(
    bus: SocketCanBus,
    source: int,
    target: int,
    device_type: DeviceType | None,
    hold_seconds: float,
    timeout_sec: float,
) -> None:
    """Opens a REMOTE_CONTROL session and briefly exercises a device so the
    user can visually/electrically confirm it's the right physical target
    -- a dimmable-light toggle for DIMMABLE_LIGHT, relay ON/OFF for
    anything else (including device_type=None, e.g. relay_blip.py's raw
    --address usage where the caller doesn't resolve a DeviceType first).
    hold_seconds must not exceed MAX_TEST_HOLD_SECONDS -- see that
    constant's docstring."""
    session = open_session(bus, source, target, SESSION_ID_REMOTE_CONTROL, timeout_sec)
    try:
        if device_type == DeviceType.DIMMABLE_LIGHT:
            print("  Session open. Sending dimmable-light ON -- check your target now.")
            bus.send(build_dimmable_light_toggle_command(source, target, turn_on=True))
        else:
            print("  Session open. Sending relay ON -- check your target now.")
            bus.send(build_relay_command(source, target, RelayCommandMode.ON))
        session.note_activity(time.time())

        print(f"  Holding ON for {hold_seconds:.0f}s...")
        time.sleep(hold_seconds)

        if device_type == DeviceType.DIMMABLE_LIGHT:
            print("  Sending dimmable-light OFF.")
            bus.send(build_dimmable_light_toggle_command(source, target, turn_on=False))
        else:
            print("  Sending relay OFF.")
            bus.send(build_relay_command(source, target, RelayCommandMode.OFF))
    finally:
        close_session(bus, source, target, session)
