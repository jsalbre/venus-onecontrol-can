#!/usr/bin/env python3
"""Manual, --confirm-gated relay blip tool. Built 2026-08-22 specifically to
physically identify which live CAN address is a specific unused Unity
X270D output (e.g. "output 7"), by briefly turning its relay on so the
user can watch a multimeter connected to that terminal -- the same
"operate it and correlate against real observation" method used
throughout this project, just live instead of via a log.

Sends a real COMMAND frame (relay ON, held briefly, then relay OFF) to a
raw CAN address, over a REMOTE_CONTROL session -- via
tools.probe_common.send_test_blip(), shared with manage-system's own
post-configure test step (2026-08-22). Targets a raw address directly
rather than going through the config-gated production safety path
(command_gate.py), since an unconfigured device has no device_class or
commands_enabled flag to gate on. This tool has real physical effect
(energizes a relay output) -- there is no config-gated safety net here,
only --confirm and the fact that nothing should be wired to the target
except a multimeter.

--hold-seconds must stay well under 5s: the session (see can_link/session.py,
SESSION_TIMEOUT_SEC) auto-expires after 5s of silence, and nothing is sent
on the wire during the hold itself -- a hold that reaches the timeout means
the OFF command arrives after the device has already closed the session, so
it's silently ignored and the relay stays on. Confirmed for real on real
hardware (2026-08-22): the original 5.0s default did exactly this. Capped
at MAX_HOLD_SECONDS (probe_common.MAX_TEST_HOLD_SECONDS) accordingly.

Usage:
    python3 relay_blip.py --address 0x11
    (prints the plan and exits -- nothing is sent without --confirm)

    python3 relay_blip.py --address 0x11 --confirm
    python3 relay_blip.py --address 0x11 --hold-seconds 3 --confirm
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bus.socketcan import SocketCanBus
from tools.probe_common import (
    DEFAULT_INTERFACE,
    DEFAULT_RESPONSE_TIMEOUT_SEC,
    DEFAULT_TEST_HOLD_SECONDS,
    MAX_TEST_HOLD_SECONDS,
    SessionOpenError,
    claim_bridge_address,
    send_test_blip,
)

DEFAULT_HOLD_SECONDS = DEFAULT_TEST_HOLD_SECONDS
MAX_HOLD_SECONDS = MAX_TEST_HOLD_SECONDS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--interface", default=DEFAULT_INTERFACE, help=f"CAN interface (default: {DEFAULT_INTERFACE})")
    parser.add_argument("--address", type=lambda s: int(s, 0), required=True, help="raw current CAN address, e.g. 0x11")
    parser.add_argument(
        "--hold-seconds",
        type=float,
        default=DEFAULT_HOLD_SECONDS,
        help=f"seconds to hold relay ON (default: {DEFAULT_HOLD_SECONDS:.0f}, max: {MAX_HOLD_SECONDS:.0f} -- session timeout)",
    )
    parser.add_argument(
        "--confirm", action="store_true", help="actually send the commands -- without this, only prints the plan"
    )
    parser.add_argument(
        "--response-timeout", type=float, default=DEFAULT_RESPONSE_TIMEOUT_SEC, help="seconds to wait for each reply"
    )
    args = parser.parse_args(argv)

    if args.hold_seconds > MAX_HOLD_SECONDS:
        parser.error(
            f"--hold-seconds {args.hold_seconds:.1f} exceeds MAX_HOLD_SECONDS ({MAX_HOLD_SECONDS:.0f}) -- "
            f"the session auto-expires after 5s of silence, so a longer hold means the OFF command arrives "
            f"too late and the device silently ignores it (confirmed on real hardware, see module docstring)."
        )

    print(f"Plan: open a REMOTE_CONTROL session with 0x{args.address:02X}, turn its relay ON for {args.hold_seconds:.0f}s, then OFF.")
    print("This energizes a real relay output. Make sure nothing except a multimeter (voltage mode) is connected.")
    if not args.confirm:
        print("\nNo command sent -- re-run with --confirm to actually perform it.")
        return 0

    with SocketCanBus(args.interface) as bus:
        bridge_address = claim_bridge_address(bus)
        print(f"\nClaimed bridge address 0x{bridge_address:02X}")

        try:
            send_test_blip(bus, bridge_address, args.address, None, args.hold_seconds, args.response_timeout)
        except SessionOpenError as e:
            print(f"Blip aborted: {e}")
            return 1

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
