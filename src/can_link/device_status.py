"""DEVICE_STATUS broadcast payload decoding (MessageType.DEVICE_STATUS,
11-bit ID 0x300 | source_address, ~1Hz idle / ~333ms on change).

Interpretation depends on the sender's DeviceType (known from that address's
prior DEVICE_ID broadcast, not from this payload) -- callers must dispatch
via decode_status(device_type, payload) rather than guessing from payload
shape alone. See dev-notes/ARCHITECTURE.md for the byte layouts.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from can_link.types import RELAY_OR_MOTOR_DEVICE_TYPES, DeviceType

NOT_SUPPORTED_BYTE = 0xFF
NOT_SUPPORTED_UINT16 = 0xFFFF
UNKNOWN_ACCELERATION_BYTE = -128


class UnknownDeviceTypeError(Exception):
    """Raised when decode_status is asked to decode a payload for a
    DeviceType this project has no decoder for. Callers must not guess a
    layout for an unmapped type."""


class OutputState(IntEnum):
    OFF_STOP = 0
    ON = 1
    FORWARD_EXTEND = 2
    REVERSE_RETRACT = 3


@dataclass(frozen=True)
class TankSensorStatus:
    fill_level_pct: int
    battery_level_pct: int | None
    measurement_quality_pct: int | None
    x_acceleration_g: float | None
    y_acceleration_g: float | None
    alert_active: bool
    alert_count: int
    dtc: int


@dataclass(frozen=True)
class RelayOrMotorStatus:
    output_state: OutputState
    fault_latch: bool
    reverse_allowed: bool
    forward_allowed: bool
    position_pct: int | None
    current_draw_amps: float | None
    dtc: int


@dataclass(frozen=True)
class DimmableLightStatus:
    mode: int
    max_brightness: int
    auto_off_minutes: int
    current_brightness: int
    t1_ms: int
    t2_ms: int


def _signed_byte(value: int) -> int:
    return value - 256 if value >= 128 else value


def decode_tank_sensor(payload: bytes) -> TankSensorStatus:
    if len(payload) != 8:
        raise ValueError(f"tank sensor status payload must be 8 bytes, got {len(payload)}")
    battery = payload[1]
    quality = payload[2]
    x_raw = _signed_byte(payload[3])
    y_raw = _signed_byte(payload[4])
    return TankSensorStatus(
        fill_level_pct=payload[0] & 0x7F,
        battery_level_pct=None if battery == NOT_SUPPORTED_BYTE else battery,
        measurement_quality_pct=None if quality == NOT_SUPPORTED_BYTE else quality,
        x_acceleration_g=None if x_raw == UNKNOWN_ACCELERATION_BYTE else x_raw / 1024.0,
        y_acceleration_g=None if y_raw == UNKNOWN_ACCELERATION_BYTE else y_raw / 1024.0,
        alert_active=bool(payload[5] & 0x80),
        alert_count=payload[5] & 0x7F,
        dtc=(payload[6] << 8) | payload[7],
    )


def decode_relay_or_motor(payload: bytes) -> RelayOrMotorStatus:
    if len(payload) != 6:
        raise ValueError(f"relay/motor status payload must be 6 bytes, got {len(payload)}")
    position = payload[1]
    current = (payload[2] << 8) | payload[3]
    return RelayOrMotorStatus(
        output_state=OutputState(payload[0] & 0x0F),
        fault_latch=bool(payload[0] & 0x20),
        reverse_allowed=bool(payload[0] & 0x40),
        forward_allowed=bool(payload[0] & 0x80),
        position_pct=None if position == NOT_SUPPORTED_BYTE else position,
        current_draw_amps=None if current == NOT_SUPPORTED_UINT16 else current / 256.0,
        dtc=(payload[4] << 8) | payload[5],
    )


def decode_dimmable_light(payload: bytes) -> DimmableLightStatus:
    if len(payload) != 8:
        raise ValueError(f"dimmable light status payload must be 8 bytes, got {len(payload)}")
    return DimmableLightStatus(
        mode=payload[0],
        max_brightness=payload[1],
        auto_off_minutes=payload[2],
        current_brightness=payload[3],
        t1_ms=(payload[4] << 8) | payload[5],
        t2_ms=(payload[6] << 8) | payload[7],
    )


def decode_status(device_type: DeviceType | None, payload: bytes):
    """Dispatch to the correct decoder for device_type. Raises
    UnknownDeviceTypeError for any type this project doesn't have a decoder
    for -- callers must not fall back to guessing a layout."""
    if device_type == DeviceType.TANK_SENSOR:
        return decode_tank_sensor(payload)
    if device_type in RELAY_OR_MOTOR_DEVICE_TYPES:
        return decode_relay_or_motor(payload)
    if device_type == DeviceType.DIMMABLE_LIGHT:
        return decode_dimmable_light(payload)
    raise UnknownDeviceTypeError(f"no DEVICE_STATUS decoder for device_type={device_type!r}")
