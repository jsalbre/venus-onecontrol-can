"""Thin wrapper over Linux SocketCAN (stdlib socket.AF_CAN/CAN_RAW) -- no
third-party dependency (no python-can). See dev-notes/VENUS_OS_CONSTRAINTS.md
for why. Handles both standard (11-bit) and extended (29-bit) frame widths
via the CAN_EFF_FLAG bit, per linux/can.h.

SocketCanBus brings its own interface up (at the confirmed protocol bitrate,
CAN_BITRATE below) if it isn't already, rather than assuming a system-level
`ip link` step already happened -- this was Phase 0's original design
(assuming Venus OS or a one-time manual command had already brought it up),
but that assumption broke on real hardware (2026-08-21): after a Venus OS
firmware update, whatever had brought the interface up before did not
happen again, and the interface came back administratively DOWN with
nothing to notice or fix it. The check is a kernel-level IFF_UP flag read,
not `ip link show` output parsing, and only acts if the interface is
actually down -- an already-up interface is never touched, so this can't
disrupt a bus that's already fine.

pack_frame/unpack_frame are pure and platform-independent (unit-tested on
any OS); SocketCanBus itself requires Linux SocketCAN support and can only
be exercised on the actual Cerbo.
"""

from __future__ import annotations

import struct
import subprocess

from can_link.frame import CanFrame

CAN_EFF_FLAG = 0x80000000
CAN_RTR_FLAG = 0x40000000
CAN_ERR_FLAG = 0x20000000
CAN_EFF_MASK = 0x1FFFFFFF
CAN_SFF_MASK = 0x000007FF

CAN_BITRATE = 250000  # confirmed IDS-CAN protocol bitrate, see ARCHITECTURE.md
IFF_UP = 0x1  # linux/if.h


def _interface_is_up(interface: str) -> bool:
    try:
        with open(f"/sys/class/net/{interface}/flags") as f:
            flags = int(f.read().strip(), 16)
    except OSError:
        return False  # interface doesn't exist at all
    return bool(flags & IFF_UP)


def ensure_interface_up(interface: str, bitrate: int = CAN_BITRATE) -> None:
    """Brings `interface` up at `bitrate` if it isn't already up. No-ops
    (does not touch the interface at all) if it's already up, so this is
    always safe to call before every SocketCanBus connection attempt."""
    if _interface_is_up(interface):
        return
    result = subprocess.run(
        ["ip", "link", "set", interface, "up", "type", "can", "bitrate", str(bitrate)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise OSError(f"failed to bring up CAN interface {interface!r}: {result.stderr.strip()}")

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

        ensure_interface_up(interface)

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
