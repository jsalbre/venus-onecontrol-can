"""CAN ID encode/decode for the IDS-CAN protocol.

Confirmed byte-exact against the decompiled Lippert `IDS.Core.IDS_CAN.CAN_ID`
struct (see dev-notes/ARCHITECTURE.md). Two ID widths share the bus:

- Standard 11-bit: broadcasts (DEVICE_ID, DEVICE_STATUS, TIME, ...)
- Extended 29-bit: point-to-point (REQUEST, RESPONSE, COMMAND, ...)

This module only encodes/decodes the CAN ID itself -- payload interpretation
lives in device_id.py / device_status.py / pid_client.py / command.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union

STANDARD_ID_MASK = 0x7FF
EXTENDED_ID_MASK = 0x1FFFFFFF
BYTE_MASK = 0xFF


@dataclass(frozen=True)
class StandardId:
    """Decoded 11-bit broadcast CAN ID."""

    source_address: int
    message_type: int  # raw value, 0..7

    def encode(self) -> int:
        if not (0 <= self.source_address <= BYTE_MASK):
            raise ValueError(f"source_address out of range: {self.source_address}")
        if not (0 <= self.message_type <= 0x7):
            raise ValueError(f"message_type out of range for standard ID: {self.message_type}")
        return ((self.message_type & 0x7) << 8) | (self.source_address & BYTE_MASK)


@dataclass(frozen=True)
class ExtendedId:
    """Decoded 29-bit point-to-point CAN ID."""

    source_address: int
    target_address: int
    message_data: int
    message_type: int  # raw value, already includes the 0x80 marker bit

    def encode(self) -> int:
        if not (0 <= self.source_address <= BYTE_MASK):
            raise ValueError(f"source_address out of range: {self.source_address}")
        if not (0 <= self.target_address <= BYTE_MASK):
            raise ValueError(f"target_address out of range: {self.target_address}")
        if not (0 <= self.message_data <= BYTE_MASK):
            raise ValueError(f"message_data out of range: {self.message_data}")
        if not (0x80 <= self.message_type <= 0xFF):
            raise ValueError(f"message_type out of range for extended ID: {self.message_type}")
        return (
            ((self.message_type & 0x1C) << 24)
            | ((self.source_address & BYTE_MASK) << 18)
            | ((self.message_type & 0x3) << 16)
            | ((self.target_address & BYTE_MASK) << 8)
            | (self.message_data & BYTE_MASK)
        )


CanId = Union[StandardId, ExtendedId]


@dataclass(frozen=True)
class CanFrame:
    """A complete outbound frame: an encoded CAN ID plus its data payload."""

    can_id: int
    is_extended: bool
    data: bytes


def decode_id(can_id: int, is_extended: bool) -> CanId:
    if is_extended:
        value = can_id & EXTENDED_ID_MASK
        return ExtendedId(
            source_address=(value >> 18) & BYTE_MASK,
            target_address=(value >> 8) & BYTE_MASK,
            message_data=value & BYTE_MASK,
            message_type=0x80 | ((value >> 24) & 0x1C) | ((value >> 16) & 0x3),
        )
    value = can_id & STANDARD_ID_MASK
    return StandardId(
        source_address=value & BYTE_MASK,
        message_type=(value >> 8) & 0x7,
    )


def encode_standard_id(source_address: int, message_type: int) -> int:
    return StandardId(source_address, message_type).encode()


def encode_extended_id(
    source_address: int, target_address: int, message_data: int, message_type: int
) -> int:
    return ExtendedId(source_address, target_address, message_data, message_type).encode()
