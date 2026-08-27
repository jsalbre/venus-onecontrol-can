"""PID_READ_LIST (request code 0x10), PID_READ_WRITE (request code 0x11),
and PID_GET_PROPERTIES (request code 0x12) request/reply handling (all
MessageType.REQUEST). No session is required for any of these -- all three
are read-only introspection operations. See ARCHITECTURE.md for
the PID_READ_WRITE worked example this is validated against, and for the
real error-reply format confirmed 2026-08-21 (see RESPONSE_CODE_NAMES
below): three real devices probed all returned RESPONSE.UNKNOWN_ID (4) for
PID_DEVICE_TYPE/PID_LOAD_TYPE, meaning those two PID numbers (sourced from
a decompiled catalog that may cover a different Lippert product line)
aren't recognized on this Unity X270 hardware -- PID_READ_LIST exists
precisely to stop guessing PID numbers and enumerate what a specific real
device actually supports instead.

This module only builds/parses the request/reply payload bytes -- the
caller is responsible for wrapping the request in an ExtendedId (frame.py)
with message_type=REQUEST, message_data=<the request code>, and the target
set to a candidate node address.

build_pid_write_request() (2026-08-21) adds write support, reusing the same
0x11 request code as a read (distinguished by payload length) per confirmed
decompiled mechanics -- see ARCHITECTURE.md's PID Reconfiguration design
decision. A write requires its target PID's own required session to be
open first (can_link/session.py); this module only builds/parses payload
bytes and has no session awareness itself. src/tools/pid_write.py is the
only caller so far -- a deliberately manual, --confirm-gated diagnostic
tool, not wired into any production code path.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from can_link.types import DeviceType

PID_LIST_REQUEST_CODE = 0x10
PID_READ_REQUEST_CODE = 0x11
PID_GET_PROPERTIES_REQUEST_CODE = 0x12

# Decompiled `enum RESPONSE : byte` (assembly_0079_Sharp.decompiled.cs,
# manos/OneControl-RV-C-Protocol), confirmed 2026-08-21 by numeric
# cross-check: byte 0x04 in a real reply lines up with UNKNOWN_ID here
# exactly, and this sequential-from-0 ordering also matches every other
# named value found earlier (READ_ONLY=7, SESSION_NOT_OPEN=14, etc.).
RESPONSE_CODE_NAMES: dict[int, str] = {
    0: "SUCCESS",
    1: "REQUEST_NOT_SUPPORTED",
    2: "BAD_REQUEST",
    3: "VALUE_OUT_OF_RANGE",
    4: "UNKNOWN_ID",
    5: "VALUE_TOO_LARGE",
    6: "INVALID_ADDRESS",
    7: "READ_ONLY",
    8: "WRITE_ONLY",
    9: "CONDITIONS_NOT_CORRECT",
    10: "FEATURE_NOT_SUPPORTED",
    11: "BUSY",
    12: "SEED_NOT_REQUESTED",
    13: "KEY_NOT_CORRECT",
    14: "SESSION_NOT_OPEN",
    15: "TIMEOUT",
    16: "REMOTE_REQUEST_NOT_SUPPORTED",
    17: "IN_MOTION_LOCKOUT_ACTIVE",
    18: "CRC_INVALID",
    19: "CANCELLED",
    20: "ABORTED",
    21: "FAILED",
    22: "IN_PROGRESS",
}

PID_AUX_BATTERY_VOLTAGE = 144

# Confirmed via decompiled LippertConnect source (2026-08-21, see
# ARCHITECTURE.md's PID reconfiguration research) -- both DIAGNOSTIC-session
# (SESSION_ID=2) gated for writes. Reads (this module) need no session.
PID_IDS_CAN_FUNCTION_NAME = 4
PID_IDS_CAN_FUNCTION_INSTANCE = 5
PID_DEVICE_TYPE = 183
# Was the candidate for why a hardware-dimmable output can behave as a
# plain on/off latch -- see ARCHITECTURE.md. Confirmed 2026-08-21: neither
# this nor PID_DEVICE_TYPE is recognized (RESPONSE.UNKNOWN_ID) on any of
# three real Unity X270 devices probed (two dimmable lights, one tank
# sensor) -- these PID numbers, from a decompiled catalog that may cover a
# different Lippert product line, don't apply to this hardware. Kept here
# for reference/citation, not as something worth reading again without new
# evidence they mean anything on this hardware family.
PID_LOAD_TYPE = 451

# Confirmed real root cause of a dimming-capable output behaving as a plain
# on/off latch (2026-08-21, real hardware) -- see ARCHITECTURE.md's "PID
# Reconfiguration" design decision. 0 = behaves as a real dimmer, 1 =
# behaves as a plain on/off switch. DIAGNOSTIC-session-gated for writes;
# reads need no session, same as any other PID_READ_WRITE read. Read live
# by publisher.py (2026-08-24) to decide a dimmable_light's D-Bus
# presentation (Settings/Type, whether the Dimming path exists at all) --
# see "PID 161 Live Read (dimmable_light D-Bus presentation)" in
# ARCHITECTURE.md.
PID_SIMULATE_ON_OFF_STYLE_LIGHT = 161

# Confirmed real and readable on this coach's CHASSIS_INFO node -- UINT32,
# scale x1/65536, volts. No session required for a read. Polled periodically
# by publisher.py for the battery_voltage device_class (see ARCHITECTURE.md).
PID_BATTERY_VOLTAGE = 43


@dataclass(frozen=True)
class ConfigurableSetting:
    """A known, real, non-identity PID that governs a genuine configuration
    choice on a device -- as opposed to PID 4/5 (identity/naming, handled
    separately since every device supports them) or PID_DEVICE_TYPE/
    PID_LOAD_TYPE above (confirmed NOT supported on this hardware family).

    applies_to is which DeviceType(s) might plausibly support this PID --
    a hint for deciding what to even attempt, not a guarantee. Callers
    (manage-system) must still confirm real support per-device (a read
    that comes back RESPONSE.UNKNOWN_ID means "not on this device," same
    lesson as the PID_DEVICE_TYPE/PID_LOAD_TYPE dead end) before offering
    to change it.

    No value_byte_count field: writes are confirmed (2026-08-24, decompiled
    LippertConnect WritePidAsync -- see ARCHITECTURE.md) to always send the
    value as a 6-byte UInt48 on the wire, for every PID, regardless of its
    own declared Formatter/display width -- not a per-setting choice."""

    pid: int
    name: str
    applies_to: frozenset[DeviceType]
    description: str


# Deliberately minimal -- one real, confirmed entry, not a speculative
# framework. Add more here as they're found and confirmed real, the same
# way PID 161 was (see ARCHITECTURE.md's "PID Reconfiguration" design
# decision).
KNOWN_SETTINGS: tuple[ConfigurableSetting, ...] = (
    ConfigurableSetting(
        pid=PID_SIMULATE_ON_OFF_STYLE_LIGHT,
        name="SIMULATE_ON_OFF_STYLE_LIGHT",
        applies_to=frozenset({DeviceType.DIMMABLE_LIGHT}),
        description="0 = behaves as a real dimmer, 1 = behaves as a plain on/off switch (a genuine "
        "configuration choice, e.g. for a non-dimmable fixture wired to a dimming-capable "
        "output -- not inherently a misconfiguration)",
    ),
)


def build_pid_list_request(page: int) -> bytes:
    """page=0 requests the first page (device's total supported-PID count
    plus its first entry); page=1,2,3,... request subsequent pages (2
    entries each). Same uint16-BE payload shape as the other PID requests,
    just semantically a page index here rather than a PID number."""
    if not (0 <= page <= 0xFFFF):
        raise ValueError(f"page out of range: {page}")
    return bytes([(page >> 8) & 0xFF, page & 0xFF])


@dataclass(frozen=True)
class PidListEntry:
    pid: int
    flags: int


@dataclass(frozen=True)
class PidListPage:
    page: int
    total_count: int | None  # only present on page 0
    entries: list[PidListEntry] = field(default_factory=list)


def parse_pid_list_reply(payload: bytes) -> PidListPage:
    """Parses a PID_READ_LIST reply. Confirmed via the decompiled server
    handler (Request10PidReadList, assembly_0079_Sharp.decompiled.cs):
    real replies are always exactly 8 bytes, padded with PID=0/Flags=0
    placeholder entries when there isn't enough real data left to fill the
    frame -- callers must stop once they've collected `total_count` real
    entries (from page 0), not when entries run out, since trailing
    padding entries are not distinguishable from a real PID=0 by shape
    alone. Page 0: [echo(2)][total_count(2)][reserved(1)][entry?]. Page
    N>0: [echo(2)][entry][entry?], entries are [PID(2), Flags(1)] each."""
    if len(payload) < 2:
        raise ValueError(f"PID list reply too short: {len(payload)} bytes")
    page = (payload[0] << 8) | payload[1]
    rest = payload[2:]

    total_count: int | None = None
    if page == 0:
        if len(rest) < 3:
            raise ValueError(f"PID list page 0 reply too short: {len(payload)} bytes")
        total_count = (rest[0] << 8) | rest[1]
        rest = rest[3:]

    entry_count = len(rest) // 3
    entries = [
        PidListEntry(pid=(rest[i * 3] << 8) | rest[i * 3 + 1], flags=rest[i * 3 + 2])
        for i in range(entry_count)
    ]
    return PidListPage(page=page, total_count=total_count, entries=entries)


def build_pid_read_request(pid: int) -> bytes:
    if not (0 <= pid <= 0xFFFF):
        raise ValueError(f"PID out of range: {pid}")
    return bytes([(pid >> 8) & 0xFF, pid & 0xFF])


def build_pid_properties_request(pid: int) -> bytes:
    """Same payload shape as build_pid_read_request() -- PID as uint16 BE.
    The caller distinguishes this from a read by using
    PID_GET_PROPERTIES_REQUEST_CODE instead of PID_READ_REQUEST_CODE as the
    frame's message_data."""
    return build_pid_read_request(pid)


def build_pid_write_request(pid: int, value: int, value_byte_count: int = 6) -> bytes:
    """Same request code as a read (PID_READ_REQUEST_CODE, 0x11) -- writes
    are distinguished purely by payload length: PID(2, BE) + value bytes,
    vs. a read's bare PID(2, BE). Default of 6 bytes (UInt48) is confirmed
    (2026-08-24, decompiled LippertConnect WritePidAsync -- see
    ARCHITECTURE.md) to be the real, universal wire width for every PID
    write, regardless of the PID's own declared Formatter/display width --
    NOT a per-PID choice, despite value_byte_count being a caller-supplied
    parameter here (kept for the rare case a caller wants to deliberately
    send a malformed width, e.g. to reproduce the BAD_REQUEST this
    generated before the fix). Requires the PID's own required session to
    be open first (see can_link/session.py's SESSION_CYPHERS) -- not
    enforced here, this module only builds/parses payload bytes."""
    if not (0 <= pid <= 0xFFFF):
        raise ValueError(f"PID out of range: {pid}")
    if value_byte_count < 1:
        raise ValueError(f"value_byte_count must be >= 1, got {value_byte_count}")
    max_value = (1 << (value_byte_count * 8)) - 1
    if not (0 <= value <= max_value):
        raise ValueError(f"value {value} does not fit in {value_byte_count} byte(s)")
    return build_pid_read_request(pid) + value.to_bytes(value_byte_count, byteorder="big")


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


@dataclass(frozen=True)
class PidProperties:
    pid: int
    raw_value: bytes  # everything after the PID echo, untouched
    flags: int | None
    session_id: int | None


def parse_pid_properties_reply(payload: bytes) -> PidProperties:
    """Parses a PID_GET_PROPERTIES reply. The decompiled source confirms
    the reply is "PID + Flags + SESSION_ID" as a triple but not the exact
    byte widths (unconfirmed against a real capture) -- so raw_value is
    always exposed untouched, and flags/session_id are a best-effort
    decode using the one width we do know for certain (SESSION_ID is a
    16-bit value, per the decompiled SESSION_ID class): the last 2 bytes
    are taken as session_id, and whatever precedes them (0 or more bytes)
    as flags. Callers should treat flags/session_id as a hint, not a
    certainty, until checked against real hardware."""
    if len(payload) < 2:
        raise ValueError(f"PID properties reply too short: {len(payload)} bytes")
    pid = (payload[0] << 8) | payload[1]
    raw_value = payload[2:]

    session_id: int | None = None
    flags: int | None = None
    if len(raw_value) >= 2:
        session_id = (raw_value[-2] << 8) | raw_value[-1]
        flags_bytes = raw_value[:-2]
        if flags_bytes:
            flags = int.from_bytes(flags_bytes, byteorder="big")

    return PidProperties(pid=pid, raw_value=raw_value, flags=flags, session_id=session_id)


def decode_16_16_fixed_point(raw_value: int) -> float:
    """Applies the 16.16 fixed-point scale used by PID_BATTERY_VOLTAGE and
    PID_AUX_BATTERY_VOLTAGE (and other UINT32 x1/65536 PIDs)."""
    return raw_value / 65536.0
