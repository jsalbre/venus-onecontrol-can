#!/usr/bin/env python3
"""Manual, --confirm-gated PID write tool. Built 2026-08-21 specifically to
test one hypothesis on real hardware: PID 161 (SIMULATE_ON_OFF_STYLE_LIGHT)
reads 1 on Kitchen Island Light (a dimming-capable output that behaves as a
plain on/off latch) and 0 on a working dimmer (Kitchen Pendants Light) --
see ARCHITECTURE.md's PID Reconfiguration design decision.

UNLIKE pid_probe.py, this tool has real physical effect: it opens a
DIAGNOSTIC session (SESSION_ID 2) and writes a PID -- real-hardware-proven
since 2026-08-21 (this tool is what proved it, fixing the Kitchen Island
Light dimming problem). There is no touchscreen on this installation to
revert a bad write through official tooling (manage-system's "unconfigure"
flow can revert a PID 4/5 identity write; other PIDs have no generic revert
path -- know the PID's meaning before writing it). This is a deliberately
separate script from pid_probe.py (which is permanently read-only) rather
than a flag bolted onto it, on top of requiring an explicit --confirm flag
before sending anything.

Flow: claim a bridge CAN address -> resolve the target device's current
address -> read the PID's current value (so a later FAIL can say whether
the write had no effect or changed something unanticipated) -> open a
DIAGNOSTIC session (SESSION_REQUEST_SEED / SESSION_TRANSMIT_KEY, driven
synchronously) -> send the PID write (PID_READ_WRITE, 0x11, distinguished
from a read by payload length) and print the raw reply -> SESSION_END ->
read the PID back (no session needed) and print PASS/FAIL comparing the
read-back value against what was requested.

--value-bytes defaults to 6 (UInt48): confirmed 2026-08-24 (decompiled
LippertConnect WritePidAsync, see ARCHITECTURE.md) to be the real,
universal wire width for every PID write, not something derived from the
target PID's own declared Formatter/display width. Getting this wrong is
what caused two real write failures on real hardware the same day (PID 4
silently had no effect; PID 5 got RESPONSE.BAD_REQUEST) -- both used
value-bytes matching the PID's declared width (2, 1) instead of the real
required width (6). The flag still exists to deliberately send a
different width for diagnostic purposes.

Usage:
    python3 pid_write.py --stable-key "function_name=38,function_instance=0" \\
        --pid 161 --value 0
    (prints the plan and exits -- nothing is sent without --confirm)

    python3 pid_write.py --stable-key "function_name=38,function_instance=0" \\
        --pid 161 --value 0 --confirm
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bus.socketcan import SocketCanBus
from can_link.pid_client import (
    PID_READ_REQUEST_CODE,
    RESPONSE_CODE_NAMES,
    build_pid_read_request,
    build_pid_write_request,
    parse_pid_reply,
)
from can_link.session import SESSION_ID_DIAGNOSTIC
from can_link.types import StableKey
from tools.probe_common import (
    DEFAULT_INTERFACE,
    DEFAULT_LISTEN_TIMEOUT_SEC,
    DEFAULT_RESPONSE_TIMEOUT_SEC,
    SessionOpenError,
    claim_bridge_address,
    close_session,
    open_session,
    resolve_target_address,
    send_request,
    wait_for_response,
)


def _read_pid(bus: SocketCanBus, source: int, target: int, pid: int, timeout_sec: float) -> int | None:
    send_request(bus, source, target, PID_READ_REQUEST_CODE, build_pid_read_request(pid))
    reply = wait_for_response(bus, source, target, PID_READ_REQUEST_CODE, timeout_sec)
    if reply is None:
        return None
    try:
        parsed = parse_pid_reply(reply)
    except ValueError:
        return None
    if parsed.pid != pid:
        return None
    return parsed.raw_value


def perform_write(
    bus: SocketCanBus,
    source: int,
    target: int,
    pid: int,
    value: int,
    value_byte_count: int,
    timeout_sec: float,
) -> None:
    print("  Reading current value before writing...")
    pre_value = _read_pid(bus, source, target, pid, timeout_sec)
    print(f"  PID {pid} before write: {pre_value!r}")

    session = open_session(bus, source, target, SESSION_ID_DIAGNOSTIC, timeout_sec)
    try:
        print(f"  DIAGNOSTIC session open. Writing PID {pid} = {value} ({value_byte_count} byte(s))...")
        write_payload = build_pid_write_request(pid, value, value_byte_count)
        send_request(bus, source, target, PID_READ_REQUEST_CODE, write_payload)
        write_reply = wait_for_response(bus, source, target, PID_READ_REQUEST_CODE, timeout_sec)
        if write_reply is None:
            print(f"  WRITE PID {pid}: no response within {timeout_sec:.0f}s")
        else:
            print(f"  WRITE PID {pid}: raw={write_reply.hex()}")
            if len(write_reply) >= 1:
                name = RESPONSE_CODE_NAMES.get(write_reply[-1])
                if name:
                    print(f"        last byte (0x{write_reply[-1]:02X}) matches RESPONSE.{name}")
    finally:
        close_session(bus, source, target, session)

    print("  Reading PID back to verify (no session needed)...")
    send_request(bus, source, target, PID_READ_REQUEST_CODE, build_pid_read_request(pid))
    read_reply = wait_for_response(bus, source, target, PID_READ_REQUEST_CODE, timeout_sec)
    if read_reply is None:
        print(f"  READ-BACK PID {pid}: no response within {timeout_sec:.0f}s -- cannot verify")
        return
    print(f"  READ-BACK PID {pid}: raw={read_reply.hex()}")
    try:
        reply = parse_pid_reply(read_reply)
    except ValueError as e:
        print(f"  READ-BACK PID {pid}: parse failed ({e}) -- cannot verify")
        return
    if reply.pid == pid and reply.raw_value == value:
        print(f"  PASS: PID {pid} now reads {reply.raw_value} (matches requested value)")
    elif reply.pid == pid and reply.raw_value == pre_value:
        print(
            f"  FAIL: PID {pid} unchanged -- still reads {reply.raw_value} (its pre-write value), "
            f"write had no effect (wanted {value})"
        )
    else:
        print(
            f"  FAIL: PID {pid} read back echoed_pid={reply.pid} raw_value={reply.raw_value} -- "
            f"does not match requested value {value}, and differs from its pre-write value {pre_value!r} too "
            f"-- changed to something unanticipated, investigate before trusting this PID's state"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--interface", default=DEFAULT_INTERFACE, help=f"CAN interface (default: {DEFAULT_INTERFACE})")
    target_group = parser.add_mutually_exclusive_group(required=True)
    target_group.add_argument("--stable-key", help='e.g. "function_name=38,function_instance=0"')
    target_group.add_argument("--address", type=lambda s: int(s, 0), help="raw current CAN address, e.g. 0x1D")
    parser.add_argument("--pid", type=lambda s: int(s, 0), required=True, help="PID to write")
    parser.add_argument("--value", type=lambda s: int(s, 0), required=True, help="value to write")
    parser.add_argument("--value-bytes", type=int, default=6, help="width of the value in bytes (default: 6, UInt48 -- the confirmed real wire width)")
    parser.add_argument(
        "--confirm", action="store_true", help="actually send the write -- without this, only prints the plan"
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

    target_desc = args.stable_key if args.stable_key else f"0x{args.address:02X}"
    print(f"Plan: write PID {args.pid} = {args.value} ({args.value_bytes} byte(s)) to {target_desc}")
    print("This writes to real device configuration. There is no touchscreen on this installation")
    print("to revert a bad write through official tooling, and no config-gated safety net here --")
    print("know what this PID means before writing it.")
    if not args.confirm:
        print("\nNo write sent -- re-run with --confirm to actually perform it.")
        return 0

    with SocketCanBus(args.interface) as bus:
        bridge_address = claim_bridge_address(bus)
        print(f"\nClaimed bridge address 0x{bridge_address:02X}")

        if args.stable_key:
            target_key = StableKey.from_config_string(args.stable_key)
            target_address = resolve_target_address(bus, target_key, args.listen_timeout)
            if target_address is None:
                print(f"Timed out waiting for a DEVICE_ID broadcast matching {args.stable_key}")
                return 1
            print(f"Resolved {args.stable_key} -> 0x{target_address:02X}")
        else:
            target_address = args.address

        try:
            perform_write(
                bus, bridge_address, target_address, args.pid, args.value, args.value_bytes, args.response_timeout
            )
        except SessionOpenError as e:
            print(f"Write aborted: {e}")
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
