#!/usr/bin/env python3
"""Manual, --confirm-gated PID write tool. Built 2026-08-21 specifically to
test one hypothesis on real hardware: PID 161 (SIMULATE_ON_OFF_STYLE_LIGHT)
reads 1 on Kitchen Island Light (a dimming-capable output that behaves as a
plain on/off latch) and 0 on a working dimmer (Kitchen Pendants Light) --
see ARCHITECTURE.md's PID Reconfiguration design decision.

UNLIKE pid_probe.py, this tool has real physical effect: it opens a
DIAGNOSTIC session (SESSION_ID 2) and writes a PID. DIAGNOSTIC has never
been exercised against real hardware before -- only REMOTE_CONTROL (used
for COMMAND frames) is real-hardware-proven. There is no touchscreen on
this installation to revert a bad write through official tooling. This is
a deliberately separate script from pid_probe.py (which is permanently
read-only) rather than a flag bolted onto it, on top of requiring an
explicit --confirm flag before sending anything.

Flow: claim a bridge CAN address -> resolve the target device's current
address -> open a DIAGNOSTIC session (SESSION_REQUEST_SEED /
SESSION_TRANSMIT_KEY, driven synchronously) -> send the PID write
(PID_READ_WRITE, 0x11, distinguished from a read by payload length) and
print the raw reply -> SESSION_END -> read the PID back (no session
needed) and print PASS/FAIL comparing the read-back value against what was
requested. The write reply's own success shape is unconfirmed project-wide
(only the error shape is confirmed) -- the read-back is the real
verification, not the write response itself.

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
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bus.socketcan import SocketCanBus
from can_link.pid_client import (
    PID_READ_REQUEST_CODE,
    RESPONSE_CODE_NAMES,
    build_pid_read_request,
    build_pid_write_request,
    parse_pid_reply,
)
from can_link.session import (
    REQUEST_CODE_SESSION_END,
    REQUEST_CODE_SESSION_REQUEST_SEED,
    REQUEST_CODE_SESSION_TRANSMIT_KEY,
    SESSION_ID_DIAGNOSTIC,
    SessionClient,
    SessionError,
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


class WriteAborted(Exception):
    """Raised when a handshake step fails or times out."""


def perform_write(
    bus: SocketCanBus,
    source: int,
    target: int,
    pid: int,
    value: int,
    value_byte_count: int,
    timeout_sec: float,
) -> None:
    session = SessionClient(session_id=SESSION_ID_DIAGNOSTIC)
    try:
        seed_payload = session.request_seed(time.time())
        send_request(bus, source, target, REQUEST_CODE_SESSION_REQUEST_SEED, seed_payload)
        seed_reply = wait_for_response(bus, source, target, REQUEST_CODE_SESSION_REQUEST_SEED, timeout_sec)
        if seed_reply is None:
            raise WriteAborted("no response to SESSION_REQUEST_SEED")
        print(f"  SESSION_REQUEST_SEED reply: raw={seed_reply.hex()}")
        try:
            key_payload = session.handle_seed_response(seed_reply, time.time())
        except (SessionError, ValueError) as e:
            raise WriteAborted(f"SESSION_REQUEST_SEED reply rejected: {e}") from e

        send_request(bus, source, target, REQUEST_CODE_SESSION_TRANSMIT_KEY, key_payload)
        key_reply = wait_for_response(bus, source, target, REQUEST_CODE_SESSION_TRANSMIT_KEY, timeout_sec)
        if key_reply is None:
            raise WriteAborted("no response to SESSION_TRANSMIT_KEY")
        print(f"  SESSION_TRANSMIT_KEY reply: raw={key_reply.hex()}")
        try:
            session.handle_key_response(time.time())
        except SessionError as e:
            raise WriteAborted(f"SESSION_TRANSMIT_KEY reply rejected: {e}") from e

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
        end_payload = session.session_end(time.time())
        send_request(bus, source, target, REQUEST_CODE_SESSION_END, end_payload)

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
    else:
        print(
            f"  FAIL: PID {pid} read back echoed_pid={reply.pid} raw_value={reply.raw_value} "
            f"-- does not match requested value {value}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--interface", default=DEFAULT_INTERFACE, help=f"CAN interface (default: {DEFAULT_INTERFACE})")
    target_group = parser.add_mutually_exclusive_group(required=True)
    target_group.add_argument("--stable-key", help='e.g. "function_name=38,function_instance=0"')
    target_group.add_argument("--address", type=lambda s: int(s, 0), help="raw current CAN address, e.g. 0x1D")
    parser.add_argument("--pid", type=lambda s: int(s, 0), required=True, help="PID to write")
    parser.add_argument("--value", type=lambda s: int(s, 0), required=True, help="value to write")
    parser.add_argument("--value-bytes", type=int, default=1, help="width of the value in bytes (default: 1)")
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
    print("This writes to real device configuration over a DIAGNOSTIC session that has never been")
    print("validated against real hardware before. There is no touchscreen on this installation to")
    print("revert a bad write through official tooling.")
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
        except WriteAborted as e:
            print(f"Write aborted: {e}")
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
