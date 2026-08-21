#!/usr/bin/env python3
"""Read-only diagnostic tool: query PID_READ_LIST (enumerate every PID a
device actually supports), PID_READ_WRITE (raw value), and/or
PID_GET_PROPERTIES (writability/required session) against a real device.
See ARCHITECTURE.md's PID reconfiguration research for why this exists.

DEVICE_TYPE (183) and LOAD_TYPE (451), from a decompiled PID catalog that
may cover a different Lippert product line than this Unity X270 hardware,
were both confirmed NOT recognized (RESPONSE.UNKNOWN_ID) on three real
devices probed 2026-08-21 -- guessing further PID numbers from that catalog
isn't productive. --list-pids asks a device directly what it actually
supports instead of guessing.

This tool NEVER opens a session and NEVER sends a PID write or a COMMAND
frame -- it cannot change device configuration. It is the first tool
besides publisher.py to transmit at all, so it claims its own CAN address
first (reusing can_link.address_claim, the same machinery publisher.py
uses) using a fresh, non-persisted identity tail -- this is a one-shot
diagnostic run, not a long-lived service identity.

Usage:
    python3 pid_probe.py --stable-key "function_name=32,function_instance=1" --list-pids

    python3 pid_probe.py --stable-key "function_name=32,function_instance=1" \\
        --read-pid 183 --read-pid 451 --properties-pid 183 --properties-pid 451

    python3 pid_probe.py --address 0x1D --read-pid 183
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bus.socketcan import SocketCanBus
from can_link.pid_client import (
    PID_GET_PROPERTIES_REQUEST_CODE,
    PID_LIST_REQUEST_CODE,
    PID_READ_REQUEST_CODE,
    RESPONSE_CODE_NAMES,
    build_pid_list_request,
    build_pid_properties_request,
    build_pid_read_request,
    parse_pid_list_reply,
    parse_pid_properties_reply,
    parse_pid_reply,
)
from can_link.types import StableKey
from tools.probe_common import (
    DEFAULT_INTERFACE,
    DEFAULT_LISTEN_TIMEOUT_SEC,
    DEFAULT_RESPONSE_TIMEOUT_SEC,
    claim_bridge_address,
    resolve_target_address,
    send_request,
    wait_for_response,
)

# Display-only labels (2026-08-21, decompiled LippertConnect SESSION_ID
# class) -- not implemented/usable here, this tool never opens a session.
_SESSION_ID_NAMES = {
    0: "UNKNOWN",
    1: "MANUFACTURING",
    2: "DIAGNOSTIC",
    3: "REPROGRAMMING",
    4: "REMOTE_CONTROL",
    5: "DAQ",
}


def _pid_mismatch_note(requested_pid: int, echoed_pid: int, raw: bytes) -> str:
    """Confirmed 2026-08-21 against 3 real devices: an UNKNOWN_ID error
    reply doesn't echo the requested PID where a success reply would, so
    an echoed-PID mismatch plus a recognizable RESPONSE code in the last
    byte usually just means "device doesn't recognize that PID" -- not a
    parser bug. Still surfaced explicitly rather than silently assumed,
    since the success-reply shape for these two request types remains
    unconfirmed (no real device has returned one yet)."""
    if echoed_pid == requested_pid:
        return ""
    response_hint = ""
    if raw:
        name = RESPONSE_CODE_NAMES.get(raw[-1])
        if name:
            response_hint = f" Last byte (0x{raw[-1]:02X}) matches RESPONSE.{name}."
    return (
        f"  *** requested PID {requested_pid} but reply echoed PID {echoed_pid} -- "
        f"likely an error reply, not real data.{response_hint} ***"
    )


def probe_read(bus: SocketCanBus, source: int, target: int, pid: int, timeout_sec: float) -> None:
    send_request(bus, source, target, PID_READ_REQUEST_CODE, build_pid_read_request(pid))
    response = wait_for_response(bus, source, target, PID_READ_REQUEST_CODE, timeout_sec)
    if response is None:
        print(f"  READ  PID {pid}: no response within {timeout_sec:.0f}s")
        return
    print(f"  READ  PID {pid}: raw={response.hex()}")
    try:
        reply = parse_pid_reply(response)
        print(
            f"        parsed as: echoed_pid={reply.pid} raw_value=0x{reply.raw_value:X} "
            f"({reply.value_byte_count} bytes)"
        )
        note = _pid_mismatch_note(pid, reply.pid, response)
        if note:
            print(note)
    except ValueError as e:
        print(f"        parse failed: {e}")


def probe_properties(bus: SocketCanBus, source: int, target: int, pid: int, timeout_sec: float) -> None:
    send_request(bus, source, target, PID_GET_PROPERTIES_REQUEST_CODE, build_pid_properties_request(pid))
    response = wait_for_response(bus, source, target, PID_GET_PROPERTIES_REQUEST_CODE, timeout_sec)
    if response is None:
        print(f"  PROPS PID {pid}: no response within {timeout_sec:.0f}s")
        return
    print(f"  PROPS PID {pid}: raw={response.hex()}")
    try:
        props = parse_pid_properties_reply(response)
        session_label = "?"
        if props.session_id is not None:
            session_label = f"{props.session_id} ({_SESSION_ID_NAMES.get(props.session_id, 'UNKNOWN')})"
        print(
            f"        parsed as: echoed_pid={props.pid} flags={props.flags!r} "
            f"session_id={session_label} trailing_bytes={props.raw_value.hex()}"
        )
        note = _pid_mismatch_note(pid, props.pid, response)
        if note:
            print(note)
    except ValueError as e:
        print(f"        parse failed: {e}")


def probe_list_pids(bus: SocketCanBus, source: int, target: int, timeout_sec: float, max_pages: int = 200) -> None:
    print("  Enumerating supported PIDs...")
    entries = []
    total_count: int | None = None
    page = 0

    while True:
        send_request(bus, source, target, PID_LIST_REQUEST_CODE, build_pid_list_request(page))
        response = wait_for_response(bus, source, target, PID_LIST_REQUEST_CODE, timeout_sec)
        if response is None:
            print(f"  LIST  page {page}: no response within {timeout_sec:.0f}s")
            return
        try:
            parsed = parse_pid_list_reply(response)
        except ValueError as e:
            print(f"  LIST  page {page}: parse failed ({e}), raw={response.hex()}")
            return

        if parsed.page != page:
            print(f"  *** requested page {page} but reply echoed page {parsed.page} -- stopping ***")
            return

        if parsed.total_count is not None:
            total_count = parsed.total_count
            print(f"  Device reports {total_count} total supported PID(s)")
            if total_count == 0:
                return

        entries.extend(parsed.entries)
        if total_count is not None and len(entries) >= total_count:
            break

        page += 1
        if page > max_pages:
            print(f"  Stopping after {max_pages} pages without reaching the reported count ({len(entries)} collected)")
            break

    entries = entries[:total_count] if total_count is not None else entries
    print(f"  {len(entries)} PID(s) supported by this device:")
    for entry in entries:
        print(f"    PID {entry.pid:5d}  (0x{entry.pid:04X})  flags=0x{entry.flags:02X}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--interface", default=DEFAULT_INTERFACE, help=f"CAN interface (default: {DEFAULT_INTERFACE})")
    target_group = parser.add_mutually_exclusive_group(required=True)
    target_group.add_argument("--stable-key", help='e.g. "function_name=32,function_instance=1"')
    target_group.add_argument("--address", type=lambda s: int(s, 0), help="raw current CAN address, e.g. 0x1D")
    parser.add_argument(
        "--list-pids", action="store_true", help="enumerate every PID the device actually supports (PID_READ_LIST)"
    )
    parser.add_argument(
        "--read-pid", action="append", type=lambda s: int(s, 0), default=[], help="PID to read (repeatable)"
    )
    parser.add_argument(
        "--properties-pid",
        action="append",
        type=lambda s: int(s, 0),
        default=[],
        help="PID to query GetPidProperties for (repeatable)",
    )
    parser.add_argument(
        "--response-timeout", type=float, default=DEFAULT_RESPONSE_TIMEOUT_SEC, help="seconds to wait for each reply"
    )
    parser.add_argument(
        "--listen-timeout",
        type=float,
        default=DEFAULT_LISTEN_TIMEOUT_SEC,
        help="seconds to wait for --stable-key's DEVICE_ID broadcast",
    )
    args = parser.parse_args(argv)

    if not args.read_pid and not args.properties_pid and not args.list_pids:
        parser.error("specify at least one of --list-pids / --read-pid / --properties-pid")

    with SocketCanBus(args.interface) as bus:
        bridge_address = claim_bridge_address(bus)
        print(f"Claimed bridge address 0x{bridge_address:02X}")

        if args.stable_key:
            target_key = StableKey.from_config_string(args.stable_key)
            target_address = resolve_target_address(bus, target_key, args.listen_timeout)
            if target_address is None:
                print(f"Timed out waiting for a DEVICE_ID broadcast matching {args.stable_key}")
                return 1
            print(f"Resolved {args.stable_key} -> 0x{target_address:02X}")
        else:
            target_address = args.address

        if args.list_pids:
            probe_list_pids(bus, bridge_address, target_address, args.response_timeout)
        for pid in args.read_pid:
            probe_read(bus, bridge_address, target_address, pid, args.response_timeout)
        for pid in args.properties_pid:
            probe_properties(bus, bridge_address, target_address, pid, args.response_timeout)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
