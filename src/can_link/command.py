"""COMMAND frame builders (MessageType.COMMAND, 29-bit ID).

Two separate builders, deliberately not unified into one parameterized
function: relay commands and dimmable light commands use incompatible
payload shapes, and conflating them was the single biggest bug the source
community research hit (see dev-notes/ARCHITECTURE.md). Relay commands
carry their command in the CAN ID's message-data byte with a MANDATORY
EMPTY payload; any payload bytes cause the device to silently discard the
command. Dimmable light commands carry an 8-byte payload with the
message-data byte left at zero.

build_relay_command() is confirmed against real hardware (2026-08-20,
samples/capture.log): two real relay commands, msg_data=0x00/0x01, empty
payload, exactly matching this builder.

build_dimmable_light_command()'s byte layout was originally sourced from
community research (esphome-onecontrol's IDS-CAN.md: "[mode, brightness
1-100, auto_off_minutes, t1_hi, t1_lo, t2_hi, t2_lo, reserved]") and
initially looked wrong: two real dimmable-light COMMAND frames in
samples/capture.log (a plain on/off tap, not a slider drag) carried
payloads `7F00000000000000` (on) / `0000000000000000` (off), neither of
which fits mode 0-3 / brightness 1-100.

A follow-up real capture of an actual brightness-slider drag
(samples/dimming_capture.log, 2026-08-20) resolved this completely: the
byte *positions* were right all along, but brightness is on a raw 0-255
scale (matching DimmableLightStatus.current_brightness's own scale in
device_status.py -- not a 1-100 percentage), and a plain on/off tap uses a
separate simplified command: mode=0x7F to turn on at the light's own last
remembered brightness, or all-zero bytes to turn off. Five distinct
mode=1/brightness=N commands in that capture (0x3E, 0xB5, 0xFF, 0x20, 0xFF)
each produced an immediate, exact-match DEVICE_STATUS.current_brightness --
this builder's mode/brightness validation now reflects that confirmed
0-255 range. auto_off_minutes/t1_ms/t2_ms were not exercised in either
capture (always 0) and remain unconfirmed, but aren't needed for Phase 3's
scope (a plain brightness percentage, no auto-off timer or cycling).

build_dimmable_light_toggle_command() below uses the separately-confirmed
simple on/off payloads -- distinct from, not a special case of, this
function's granular mode=1 command.

This module intentionally has no builder for motor commands (H-bridge
awning/slide/jack devices) -- see ARCHITECTURE.md's "No Motor Commands"
safety boundary. Nothing in this codebase constructs a motor COMMAND frame.
"""

from __future__ import annotations

from enum import IntEnum

from can_link.frame import CanFrame, encode_extended_id
from can_link.types import MessageType


class RelayCommandMode(IntEnum):
    OFF = 0
    ON = 1
    CLEAR_LATCH = 3


def build_relay_command(
    source_address: int, target_address: int, command_mode: RelayCommandMode
) -> CanFrame:
    """Builds a COMMAND frame for a relay-driven device (light, water pump,
    water heater). Payload is always empty -- this is not a truncation, it
    is required by the protocol."""
    can_id = encode_extended_id(
        source_address=source_address,
        target_address=target_address,
        message_data=int(command_mode),
        message_type=MessageType.COMMAND,
    )
    frame = CanFrame(can_id=can_id, is_extended=True, data=b"")
    if len(frame.data) != 0:
        raise AssertionError("relay command payload must be empty")
    return frame


def build_dimmable_light_command(
    source_address: int,
    target_address: int,
    mode: int,
    brightness: int,
    auto_off_minutes: int,
    t1_ms: int,
    t2_ms: int,
) -> CanFrame:
    """Builds a COMMAND frame for a dimmable light. mode: 0=off, 1=on,
    2=blink, 3=swell. brightness: 0-255 (raw scale, matches
    DimmableLightStatus.current_brightness -- not a 1-100 percentage;
    confirmed against a real brightness-slider capture, see this module's
    docstring)."""
    if not (0 <= mode <= 0xFF):
        raise ValueError(f"mode out of range: {mode}")
    if not (0 <= brightness <= 255):
        raise ValueError(f"brightness must be 0-255, got {brightness}")
    if not (0 <= auto_off_minutes <= 0xFF):
        raise ValueError(f"auto_off_minutes out of range: {auto_off_minutes}")
    if not (0 <= t1_ms <= 0xFFFF):
        raise ValueError(f"t1_ms out of range: {t1_ms}")
    if not (0 <= t2_ms <= 0xFFFF):
        raise ValueError(f"t2_ms out of range: {t2_ms}")

    payload = bytes(
        [mode, brightness, auto_off_minutes]
    ) + t1_ms.to_bytes(2, "big") + t2_ms.to_bytes(2, "big") + b"\x00"

    can_id = encode_extended_id(
        source_address=source_address,
        target_address=target_address,
        message_data=0,
        message_type=MessageType.COMMAND,
    )
    frame = CanFrame(can_id=can_id, is_extended=True, data=payload)
    if len(frame.data) != 8:
        raise AssertionError(f"dimmable light command payload must be 8 bytes, got {len(frame.data)}")
    return frame


DIMMABLE_LIGHT_TOGGLE_ON_PAYLOAD = bytes([0x7F, 0, 0, 0, 0, 0, 0, 0])
DIMMABLE_LIGHT_TOGGLE_OFF_PAYLOAD = bytes(8)


def build_dimmable_light_toggle_command(source_address: int, target_address: int, turn_on: bool) -> CanFrame:
    """Builds a COMMAND frame for a plain on/off toggle -- the same simple
    command the OneControl app sends for a tap (not a slider drag). Turning
    on resumes the light's own last remembered brightness (mode=0x7F
    sentinel) rather than setting a specific level; use
    build_dimmable_light_command(mode=1, brightness=N) for that."""
    payload = DIMMABLE_LIGHT_TOGGLE_ON_PAYLOAD if turn_on else DIMMABLE_LIGHT_TOGGLE_OFF_PAYLOAD
    can_id = encode_extended_id(
        source_address=source_address,
        target_address=target_address,
        message_data=0,
        message_type=MessageType.COMMAND,
    )
    return CanFrame(can_id=can_id, is_extended=True, data=payload)
