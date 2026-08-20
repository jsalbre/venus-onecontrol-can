#!/usr/bin/env python3
"""Phase 1+ tool: replay a captured candump -L format log through the
can_link/ decoders and print structured output, for manual validation
against what the user observed physically (see TODO.md Phase 1).

Only DEVICE_ID and DEVICE_STATUS (11-bit broadcast) frames are semantically
decoded. Point-to-point (29-bit) frames -- PID reads, session handshakes,
commands -- are printed as their raw source/target/message-data/payload
breakdown for manual correlation, since interpreting them meaningfully
depends on knowing which request is in flight, which this tool doesn't
track across a whole log.

Usage: python3 candump_replay.py samples/capture.log
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from typing import Iterable, Iterator

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from can_link.address_table import AddressTable
from can_link.device_id import decode_device_id, stable_key
from can_link.device_status import UnknownDeviceTypeError, decode_status
from can_link.frame import ExtendedId, StandardId, decode_id
from can_link.types import DeviceType, MessageType, function_name_label

_LINE_PATTERN = re.compile(
    r"^\((?P<timestamp>[\d.]+)\)\s+(?P<interface>\S+)\s+(?P<id>[0-9A-Fa-f]+)#(?P<data>[0-9A-Fa-f]*)\s*$"
)


@dataclass(frozen=True)
class LogEntry:
    timestamp: float
    interface: str
    can_id: int
    is_extended: bool
    data: bytes


def parse_log_line(line: str) -> LogEntry | None:
    """Parses one candump -L format line. Returns None for blank/unparsable
    lines rather than raising, so a replay run doesn't abort on stray
    output mixed into a hand-edited log file."""
    match = _LINE_PATTERN.match(line.strip())
    if not match:
        return None
    id_hex = match.group("id")
    return LogEntry(
        timestamp=float(match.group("timestamp")),
        interface=match.group("interface"),
        can_id=int(id_hex, 16),
        is_extended=len(id_hex) > 3,
        data=bytes.fromhex(match.group("data")),
    )


def _message_type_label(value: int) -> str:
    try:
        return MessageType(value).name
    except ValueError:
        return f"UNKNOWN({value})"


def replay(lines: Iterable[str]) -> Iterator[str]:
    table = AddressTable()
    device_type_by_address: dict[int, DeviceType | None] = {}

    for line in lines:
        entry = parse_log_line(line)
        if entry is None:
            continue
        table.note_bus_activity(entry.timestamp)
        decoded_id = decode_id(entry.can_id, entry.is_extended)

        if isinstance(decoded_id, StandardId):
            if decoded_id.message_type == MessageType.DEVICE_ID:
                try:
                    identity = decode_device_id(entry.data)
                except ValueError as exc:
                    yield f"{entry.timestamp:.6f} DEVICE_ID     addr=0x{decoded_id.source_address:02X} decode error: {exc}"
                    continue
                key = stable_key(identity)
                table.observe_device_id(key, decoded_id.source_address, identity.device_type, entry.timestamp)
                device_type_by_address[decoded_id.source_address] = identity.device_type
                yield (
                    f"{entry.timestamp:.6f} DEVICE_ID     addr=0x{decoded_id.source_address:02X} "
                    f"device_type={identity.device_type!r} "
                    f"function_name={function_name_label(identity.function_name)} "
                    f"function_instance={identity.function_instance} "
                    f"stable_key={key.to_config_string()}"
                )
            elif decoded_id.message_type == MessageType.DEVICE_STATUS:
                device_type = device_type_by_address.get(decoded_id.source_address)
                try:
                    status = decode_status(device_type, entry.data)
                    yield (
                        f"{entry.timestamp:.6f} DEVICE_STATUS addr=0x{decoded_id.source_address:02X} "
                        f"device_type={device_type!r} {status!r}"
                    )
                except UnknownDeviceTypeError:
                    yield (
                        f"{entry.timestamp:.6f} DEVICE_STATUS addr=0x{decoded_id.source_address:02X} "
                        f"device_type={device_type!r} (no decoder yet, raw={entry.data.hex()})"
                    )
                except ValueError as exc:
                    yield (
                        f"{entry.timestamp:.6f} DEVICE_STATUS addr=0x{decoded_id.source_address:02X} "
                        f"decode error: {exc}"
                    )
            else:
                yield (
                    f"{entry.timestamp:.6f} {_message_type_label(decoded_id.message_type)} "
                    f"addr=0x{decoded_id.source_address:02X} raw={entry.data.hex()}"
                )
        else:
            assert isinstance(decoded_id, ExtendedId)
            yield (
                f"{entry.timestamp:.6f} {_message_type_label(decoded_id.message_type)} "
                f"src=0x{decoded_id.source_address:02X} dst=0x{decoded_id.target_address:02X} "
                f"msg_data=0x{decoded_id.message_data:02X} raw={entry.data.hex()}"
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log_file", help="candump -L format log, e.g. from candump_logger.py")
    args = parser.parse_args(argv)

    with open(args.log_file) as fh:
        for output_line in replay(fh):
            print(output_line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
