"""PID_READ_WRITE request/reply handling (MessageType.REQUEST, message_data
request code 0x11 for reads). No session is required for reads. See
dev-notes/ARCHITECTURE.md for the worked example this is validated against.

This module only builds/parses the request/reply payload bytes -- the
caller is responsible for wrapping the request in an ExtendedId (frame.py)
with message_type=REQUEST, message_data=PID_READ_REQUEST_CODE, and the
target set to a candidate node address.
"""

from __future__ import annotations

from dataclasses import dataclass

PID_READ_REQUEST_CODE = 0x11

PID_BATTERY_VOLTAGE = 43
PID_AUX_BATTERY_VOLTAGE = 144


def build_pid_read_request(pid: int) -> bytes:
    if not (0 <= pid <= 0xFFFF):
        raise ValueError(f"PID out of range: {pid}")
    return bytes([(pid >> 8) & 0xFF, pid & 0xFF])


@dataclass(frozen=True)
class PidReply:
    pid: int
    raw_value: int
    value_byte_count: int


def parse_pid_reply(payload: bytes) -> PidReply:
    """Parses a PID_READ_WRITE reply payload. The value's width is taken
    from the payload length (DLC), not a fixed type size -- a UINT32
    16.16-fixed-point value may arrive truncated to 3 bytes with the
    leading zero byte omitted."""
    if len(payload) < 3:
        raise ValueError(f"PID reply payload too short: {len(payload)} bytes")
    pid = (payload[0] << 8) | payload[1]
    value_bytes = payload[2:]
    return PidReply(
        pid=pid,
        raw_value=int.from_bytes(value_bytes, byteorder="big"),
        value_byte_count=len(value_bytes),
    )


def decode_16_16_fixed_point(raw_value: int) -> float:
    """Applies the 16.16 fixed-point scale used by PID_BATTERY_VOLTAGE and
    PID_AUX_BATTERY_VOLTAGE (and other UINT32 x1/65536 PIDs)."""
    return raw_value / 65536.0
