"""Thin wrapper over Linux SocketCAN (stdlib socket.AF_CAN/CAN_RAW) -- no
third-party dependency (no python-can). See dev-notes/VENUS_OS_CONSTRAINTS.md
for why. Handles both standard (11-bit) and extended (29-bit) frame widths
via the CAN_EFF_FLAG bit, per linux/can.h.

The interface must already be up and configured (bitrate 250000) before
this is used -- that is a system-level `ip link` step, not this module's
job. See dev-notes/VENUS_OS_CONSTRAINTS.md's SocketCAN section (filled in
during Phase 0).

pack_frame/unpack_frame are pure and platform-independent (unit-tested on
any OS); SocketCanBus itself requires Linux SocketCAN support and can only
be exercised on the actual Cerbo.
"""

from __future__ import annotations

import struct

from can_link.frame import CanFrame

CAN_EFF_FLAG = 0x80000000
CAN_RTR_FLAG = 0x40000000
CAN_ERR_FLAG = 0x20000000
CAN_EFF_MASK = 0x1FFFFFFF
CAN_SFF_MASK = 0x000007FF

# struct can_frame { canid_t can_id; __u8 can_dlc; __u8 __pad; __u8 __res0;
# __u8 __res1; __u8 data[8]; } -- from linux/can.h.
_FRAME_FORMAT = "=IB3x8s"
FRAME_SIZE = struct.calcsize(_FRAME_FORMAT)


def pack_frame(frame: CanFrame) -> bytes:
    if len(frame.data) > 8:
        raise ValueError(f"CAN payload cannot exceed 8 bytes, got {len(frame.data)}")
    can_id = frame.can_id & (CAN_EFF_MASK if frame.is_extended else CAN_SFF_MASK)
    if frame.is_extended:
        can_id |= CAN_EFF_FLAG
    return struct.pack(_FRAME_FORMAT, can_id, len(frame.data), frame.data.ljust(8, b"\x00"))


def unpack_frame(raw: bytes) -> CanFrame:
    if len(raw) != FRAME_SIZE:
        raise ValueError(f"expected a {FRAME_SIZE}-byte can_frame, got {len(raw)} bytes")
    can_id_flags, dlc, data = struct.unpack(_FRAME_FORMAT, raw)
    is_extended = bool(can_id_flags & CAN_EFF_FLAG)
    can_id = can_id_flags & (CAN_EFF_MASK if is_extended else CAN_SFF_MASK)
    return CanFrame(can_id=can_id, is_extended=is_extended, data=data[:dlc])


class SocketCanBus:
    """Requires Linux with SocketCAN support -- only usable on the Cerbo,
    not during local development."""

    def __init__(self, interface: str) -> None:
        import socket  # local import: socket.AF_CAN doesn't exist on non-Linux

        self._interface = interface
        self._socket = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
        self._socket.bind((interface,))

    def close(self) -> None:
        self._socket.close()

    def send(self, frame: CanFrame) -> None:
        self._socket.send(pack_frame(frame))

    def recv(self) -> CanFrame:
        return unpack_frame(self._socket.recv(FRAME_SIZE))

    def fileno(self) -> int:
        return self._socket.fileno()

    def __enter__(self) -> "SocketCanBus":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
