"""COMMAND frame builders (MessageType.COMMAND, 29-bit ID).

Two separate builders, deliberately not unified into one parameterized
function: relay commands and dimmable light commands use incompatible
payload shapes, and conflating them was the single biggest bug the source
community research hit (see dev-notes/ARCHITECTURE.md). Relay commands
carry their command in the CAN ID's message-data byte with a MANDATORY
EMPTY payload; any payload bytes cause the device to silently discard the
command. Dimmable light commands carry an 8-byte payload with the
message-data byte left at zero.

This module intentionally has no builder for motor commands (H-bridge
awning/slide/jack devices) -- see ARCHITECTURE.md's "No Motor Commands"
safety boundary. Nothing in this codebase constructs a motor COMMAND frame.
"""

from __future__ import annotations

from enum import IntEnum

from can_link.frame import CanFrame, encode_extended_id
from can_link.types import MessageType


class RelayCommandMode(IntEnum):
    OFF = 0
    ON = 1
    CLEAR_LATCH = 3


def build_relay_command(
    source_address: int, target_address: int, command_mode: RelayCommandMode
) -> CanFrame:
    """Builds a COMMAND frame for a relay-driven device (light, water pump,
    water heater). Payload is always empty -- this is not a truncation, it
    is required by the protocol."""
    can_id = encode_extended_id(
        source_address=source_address,
        target_address=target_address,
        message_data=int(command_mode),
        message_type=MessageType.COMMAND,
    )
    frame = CanFrame(can_id=can_id, is_extended=True, data=b"")
    if len(frame.data) != 0:
        raise AssertionError("relay command payload must be empty")
    return frame


def build_dimmable_light_command(
    source_address: int,
    target_address: int,
    mode: int,
    brightness: int,
    auto_off_minutes: int,
    t1_ms: int,
    t2_ms: int,
) -> CanFrame:
    """Builds a COMMAND frame for a dimmable light. mode: 0=off, 1=on,
    2=blink, 3=swell. brightness: 1-100."""
    if not (0 <= mode <= 0xFF):
        raise ValueError(f"mode out of range: {mode}")
    if not (1 <= brightness <= 100):
        raise ValueError(f"brightness must be 1-100, got {brightness}")
    if not (0 <= auto_off_minutes <= 0xFF):
        raise ValueError(f"auto_off_minutes out of range: {auto_off_minutes}")
    if not (0 <= t1_ms <= 0xFFFF):
        raise ValueError(f"t1_ms out of range: {t1_ms}")
    if not (0 <= t2_ms <= 0xFFFF):
        raise ValueError(f"t2_ms out of range: {t2_ms}")

    payload = bytes(
        [mode, brightness, auto_off_minutes]
    ) + t1_ms.to_bytes(2, "big") + t2_ms.to_bytes(2, "big") + b"\x00"

    can_id = encode_extended_id(
        source_address=source_address,
        target_address=target_address,
        message_data=0,
        message_type=MessageType.COMMAND,
    )
    frame = CanFrame(can_id=can_id, is_extended=True, data=payload)
    if len(frame.data) != 8:
        raise AssertionError(f"dimmable light command payload must be 8 bytes, got {len(frame.data)}")
    return frame
