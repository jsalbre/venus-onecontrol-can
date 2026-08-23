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

Usage:
    python3 list_unconfigured.py --reference-stable-key "function_name=38,function_instance=0"

    python3 list_unconfigured.py --reference-stable-key "function_name=38,function_instance=0" \\
        --compare-key "function_name=5,function_instance=0" \\
        --compare-key "function_name=107,function_instance=0"
"""

from __future__ import annotations

import argparse
import os
import select
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bus.socketcan import SocketCanBus
from can_link.device_id import DeviceIdentity, decode_device_id, stable_key
from can_link.frame import StandardId, decode_id
from can_link.types import MessageType, StableKey

DEFAULT_INTERFACE = "vecan1"
DEFAULT_LISTEN_TIMEOUT_SEC = 20.0


def _recv_with_timeout(bus: SocketCanBus, timeout_sec: float):
    if timeout_sec <= 0:
        return None
    ready, _, _ = select.select([bus.fileno()], [], [], timeout_sec)
    if not ready:
        return None
    frame = bus.recv()
    return frame.can_id, frame.is_extended, frame.data


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
    compare_keys = [StableKey.from_config_string(k) for k in args.compare_key]

    reference_product: tuple[int, int] | None = None
    compare_resolved: dict[StableKey, tuple[int, DeviceIdentity]] = {}
    seen: dict[int, DeviceIdentity] = {}

    with SocketCanBus(args.interface) as bus:
        print(f"Listening up to {args.listen_timeout:.0f}s for DEVICE_ID broadcasts...")
        deadline = time.time() + args.listen_timeout
        while time.time() < deadline:
            result = _recv_with_timeout(bus, deadline - time.time())
            if result is None:
                continue
            can_id, is_extended, data = result
            if is_extended:
                continue
            decoded = decode_id(can_id, is_extended)
            assert isinstance(decoded, StandardId)
            if decoded.message_type != MessageType.DEVICE_ID:
                continue
            try:
                identity = decode_device_id(data)
            except ValueError:
                continue

            key = stable_key(identity)

            if key == reference_key and reference_product is None:
                reference_product = (identity.product_id, identity.product_instance)
                print(
                    f"Reference device resolved: addr=0x{decoded.source_address:02X} "
                    f"product_id={identity.product_id} product_instance={identity.product_instance}"
                )

            if key in compare_keys and key not in compare_resolved:
                compare_resolved[key] = (decoded.source_address, identity)

            if identity.function_name == 0:
                seen[decoded.source_address] = identity

    if compare_keys:
        print("\nKnown/named devices (for device_instance comparison):")
        for key in compare_keys:
            if key in compare_resolved:
                addr, identity = compare_resolved[key]
                _print_identity(key.to_config_string(), addr, identity)
            else:
                print(f"  {key.to_config_string():<12} never seen in this window")

    if reference_product is None:
        print(f"\nNever saw a DEVICE_ID broadcast matching {args.reference_stable_key} -- can't filter by board.")
        print("Unconfigured devices seen (all, unfiltered):")
        matches = list(seen.items())
    else:
        print(f"\nUnconfigured (FUNCTION_NAME=0) devices on the same board as {args.reference_stable_key}:")
        matches = [(addr, identity) for addr, identity in seen.items() if (identity.product_id, identity.product_instance) == reference_product]

    if not matches:
        print("  (none seen in this window)")
        return 0

    for addr, identity in sorted(matches):
        _print_identity("unconfigured", addr, identity)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
