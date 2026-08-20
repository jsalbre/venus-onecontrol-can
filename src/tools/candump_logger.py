#!/usr/bin/env python3
"""Phase 0 tool: raw CAN frame capture, no decoding or interpretation.

Logs every frame seen on the given SocketCAN interface, both standard and
extended ID widths, to the console and (optionally) an append-only log file
in candump -L compatible text format. Run this while toggling physical
OneControl devices from the app to build a capture for manual correlation
and for Phase 1's candump_replay.py to validate the decoder against.

Usage: python3 candump_logger.py can1 --log-file samples/capture.log
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bus.socketcan import SocketCanBus
from can_link.frame import CanFrame


def format_candump_line(interface: str, frame: CanFrame, timestamp: float) -> str:
    id_width = 8 if frame.is_extended else 3
    id_hex = f"{frame.can_id:0{id_width}X}"
    data_hex = frame.data.hex().upper()
    return f"({timestamp:.6f}) {interface} {id_hex}#{data_hex}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("interface", help="SocketCAN interface name, e.g. can1")
    parser.add_argument(
        "--log-file", help="Append captured frames to this file (candump -L compatible format)"
    )
    args = parser.parse_args(argv)

    log_fh = open(args.log_file, "a") if args.log_file else None
    try:
        with SocketCanBus(args.interface) as bus:
            print(f"Listening on {args.interface}... (Ctrl+C to stop)", file=sys.stderr)
            while True:
                frame = bus.recv()
                line = format_candump_line(args.interface, frame, time.time())
                print(line)
                if log_fh:
                    log_fh.write(line + "\n")
                    log_fh.flush()
    except KeyboardInterrupt:
        print("Capture stopped.", file=sys.stderr)
        return 0
    finally:
        if log_fh:
            log_fh.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
