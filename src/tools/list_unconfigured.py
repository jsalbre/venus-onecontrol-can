#!/usr/bin/env python3
"""Purely passive diagnostic: list every currently-unconfigured
(FUNCTION_NAME=0) device sharing the same PRODUCT_ID/product_instance as a
known reference device -- i.e. every still-unnamed port on the same
physical board as that reference device. Optionally also resolves one or
more other already-named devices (--compare-key) and prints their full
DEVICE_ID fields alongside the unconfigured pool, so device_instance
numbering can be compared against known physical positions instead of
guessed.

Built 2026-08-22 to help identify a specific unused Unity X270D output
(e.g. "output 7") before writing anything to it via pid_write.py. 13 of 31
devices on the coach share an identical fallback stable key
(FUNCTION_NAME=0, same PRODUCT_ID/instance) per the 2026-08-19 capture --
this tool exists so a specific physical port can be identified by its
DEVICE_ID fields (particularly device_instance, which is decoded but not
used anywhere else in this project) rather than guessed.

Sends NOTHING on the bus -- doesn't even claim a CAN address, unlike
pid_probe.py/pid_write.py. Strictly read-only, zero risk.

Its scanning logic (tools.probe_common.scan_board()) is shared with
manage-system's own target-discovery step (2026-08-22).

Usage:
    python3 list_unconfigured.py --reference-stable-key "function_name=38,function_instance=0"

    python3 list_unconfigured.py --reference-stable-key "function_name=38,function_instance=0" \\
        --compare-key "function_name=5,function_instance=0" \\
        --compare-key "function_name=107,function_instance=0"
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bus.socketcan import SocketCanBus
from can_link.device_id import DeviceIdentity
from can_link.types import StableKey
from tools.probe_common import DEFAULT_INTERFACE, DEFAULT_LISTEN_TIMEOUT_SEC, scan_board


def _print_identity(label: str, addr: int, identity: DeviceIdentity) -> None:
    print(
        f"  {label:<12} addr=0x{addr:02X}  device_type_raw={identity.device_type_raw:3d}  "
        f"device_instance={identity.device_instance}  function_instance={identity.function_instance}  "
        f"capabilities_raw=0x{identity.capabilities_raw:02X}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--interface", default=DEFAULT_INTERFACE, help=f"CAN interface (default: {DEFAULT_INTERFACE})")
    parser.add_argument(
        "--reference-stable-key",
        required=True,
        help='a known, already-named device on the target board, used to filter the unconfigured pool by PRODUCT_ID/instance, e.g. "function_name=38,function_instance=0"',
    )
    parser.add_argument(
        "--compare-key",
        action="append",
        default=[],
        help="another known, already-named device to resolve and print full fields for (repeatable) -- for comparing device_instance against the unconfigured pool",
    )
    parser.add_argument(
        "--listen-timeout", type=float, default=DEFAULT_LISTEN_TIMEOUT_SEC, help="seconds to listen"
    )
    args = parser.parse_args(argv)

    reference_key = StableKey.from_config_string(args.reference_stable_key)
    compare_keys = tuple(StableKey.from_config_string(k) for k in args.compare_key)

    print(f"Listening up to {args.listen_timeout:.0f}s for DEVICE_ID broadcasts...")
    with SocketCanBus(args.interface) as bus:
        result = scan_board(bus, reference_key, args.listen_timeout, compare_keys)

    if result.reference_product is not None:
        print(
            f"Reference device resolved: addr=0x{result.reference_address:02X} "
            f"product_id={result.reference_product[0]} product_instance={result.reference_product[1]}"
        )

    if compare_keys:
        print("\nKnown/named devices (for device_instance comparison):")
        for key in compare_keys:
            if key in result.compare_resolved:
                addr, identity = result.compare_resolved[key]
                _print_identity(key.to_config_string(), addr, identity)
            else:
                print(f"  {key.to_config_string():<12} never seen in this window")

    if result.reference_product is None:
        print(f"\nNever saw a DEVICE_ID broadcast matching {args.reference_stable_key} -- can't filter by board.")
        print("Unconfigured devices seen (all, unfiltered):")
        matches = list(result.unconfigured.items())
    else:
        print(f"\nUnconfigured (FUNCTION_NAME=0) devices on the same board as {args.reference_stable_key}:")
        matches = [
            (addr, identity)
            for addr, identity in result.unconfigured.items()
            if (identity.product_id, identity.product_instance) == result.reference_product
        ]

    if not matches:
        print("  (none seen in this window)")
        return 0

    for addr, identity in sorted(matches):
        _print_identity("unconfigured", addr, identity)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
