"""Shared enums and value types for the IDS-CAN protocol layer.

See dev-notes/ARCHITECTURE.md for the full protocol reference these are
transcribed from. Values are unvalidated against real hardware until
Phase 0/1 acceptance criteria are met (see TODO.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class MessageType(IntEnum):
    """CAN ID message type. Broadcast types use 11-bit IDs; point-to-point
    types (value >= 0x80) use 29-bit extended IDs."""

    NETWORK = 0
    CIRCUIT_ID = 1
    DEVICE_ID = 2
    DEVICE_STATUS = 3
    PRODUCT_STATUS = 6
    TIME = 7

    REQUEST = 128
    RESPONSE = 129
    COMMAND = 130
    EXT_STATUS = 131
    TEXT_CONSOLE = 132
    GROUP_ID = 133

    @property
    def is_point_to_point(self) -> bool:
        return self.value >= 0x80


class DeviceType(IntEnum):
    """DEVICE_TYPE enum from DEVICE_ID broadcasts, per decompiled Lippert
    firmware (D-Jeffrey/UnityX-canbus IDS-coding.md)."""

    UNKNOWN = 0
    GENERIC = 1
    TABLET = 2
    LATCHING_RELAY = 3
    MOMENTARY_RELAY = 4
    LATCHING_H_BRIDGE = 5
    MOMENTARY_H_BRIDGE = 6
    LEVELER_TYPE_1 = 7
    SWITCH = 8
    TOUCHSCREEN_SWITCH = 9
    TANK_SENSOR = 10
    LEVELER_TYPE_2 = 11
    HOUR_METER = 12
    RGB_LIGHT = 13
    REAL_TIME_CLOCK = 14
    IR_REMOTE_CONTROL = 15
    HVAC_CONTROL = 16
    LEVELER_TYPE_3 = 17
    CAN_TO_ETHERNET_GATEWAY = 18
    IN_TRANSIT_POWER_DISCONNECT = 19
    DIMMABLE_LIGHT = 20
    ONECONTROL_TOUCH_PAD = 21
    ANDROID_MOBILE_DEVICE = 22
    IOS_MOBILE_DEVICE = 23
    GENERATOR_GENIE = 24
    TEMPERATURE_SENSOR = 25
    AC_POWER_MONITOR = 26
    DC_POWER_MONITOR = 27
    SETEC_POWER_MANAGER = 28
    ONECONTROL_CLOUD_GATEWAY = 29
    LATCHING_RELAY_TYPE_2 = 30
    MOMENTARY_RELAY_TYPE_2 = 31
    LATCHING_H_BRIDGE_TYPE_2 = 32
    MOMENTARY_H_BRIDGE_TYPE_2 = 33
    ONECONTROL_APPLICATION = 34
    CONFIGURATOR_APPLICATION = 35
    BLUETOOTH_GATEWAY = 36
    MAXX_FAN = 37
    RAIN_SENSOR = 38
    CHASSIS_INFO = 39
    LEVELER_TYPE_4 = 40
    AWNING_SENSOR = 47


# DEVICE_TYPEs that share the RELAY_TYPE_2_STATUS_PARAMS 6-byte DEVICE_STATUS
# layout (latching relays used for lights/pump/water-heater, and H-bridge
# motors used for awnings/slides/jacks). The command path treats these very
# differently -- see command.py -- but the STATUS decode is identical.
RELAY_OR_MOTOR_DEVICE_TYPES = frozenset(
    {
        DeviceType.LATCHING_RELAY,
        DeviceType.MOMENTARY_RELAY,
        DeviceType.LATCHING_H_BRIDGE,
        DeviceType.MOMENTARY_H_BRIDGE,
        DeviceType.LATCHING_RELAY_TYPE_2,
        DeviceType.MOMENTARY_RELAY_TYPE_2,
        DeviceType.LATCHING_H_BRIDGE_TYPE_2,
        DeviceType.MOMENTARY_H_BRIDGE_TYPE_2,
    }
)

# DEVICE_TYPEs that are known to have a motor (never a valid COMMAND target
# in this project -- see ARCHITECTURE.md's "No Motor Commands" boundary).
MOTOR_DEVICE_TYPES = frozenset(
    {
        DeviceType.LATCHING_H_BRIDGE,
        DeviceType.MOMENTARY_H_BRIDGE,
        DeviceType.LATCHING_H_BRIDGE_TYPE_2,
        DeviceType.MOMENTARY_H_BRIDGE_TYPE_2,
    }
)

# DEVICE_TYPEs allowed as COMMAND targets in v1 (lights, relays, pump,
# water heater -- never anything in MOTOR_DEVICE_TYPES).
COMMANDABLE_DEVICE_TYPES = frozenset(
    {
        DeviceType.LATCHING_RELAY,
        DeviceType.MOMENTARY_RELAY,
        DeviceType.LATCHING_RELAY_TYPE_2,
        DeviceType.MOMENTARY_RELAY_TYPE_2,
        DeviceType.DIMMABLE_LIGHT,
    }
)

# Known FUNCTION_NAME values relevant to v1 priority devices (tanks, lights,
# pump, water heater) plus a few read-only-status-relevant names (awning,
# slide, jacks, generator). Not exhaustive -- FUNCTION_NAME is stored as a
# plain int everywhere else; this is only used for human-readable logging.
FUNCTION_NAMES: dict[int, str] = {
    3: "GAS_WATER_HEATER",
    4: "ELECTRIC_WATER_HEATER",
    5: "WATER_PUMP",
    67: "FRESH_TANK",
    68: "GREY_TANK",
    69: "BLACK_TANK",
    70: "FUEL_TANK",
    88: "LANDING_GEAR",
    89: "FRONT_STABILIZER",
    90: "REAR_STABILIZER",
    95: "GENERATOR",
    96: "SLIDE",
    105: "AWNING",
    106: "LEVEL_UP_LEVELER",
    138: "PATIO_AWNING",
    139: "REAR_AWNING",
    140: "SIDE_AWNING",
    141: "JACKS",
    187: "LEVEL_UP_UNITY",
    255: "BATTERY",
    256: "MAIN_BATTERY",
    257: "AUX_BATTERY",
}


def function_name_label(value: int) -> str:
    """Human-readable label for a FUNCTION_NAME value, for logging only."""
    return FUNCTION_NAMES.get(value, f"UNKNOWN_{value}")


@dataclass(frozen=True)
class StableKey:
    """Identifies a physical device independent of its volatile CAN
    SourceAddress. See ARCHITECTURE.md's "Stable-Key Device Discovery".

    kind="function_name": primary=FUNCTION_NAME, instance=function_instance nibble.
    kind="product_id": primary=PRODUCT_ID, instance=product instance (fallback,
        used only when FUNCTION_NAME is 0/unset on a device).
    """

    kind: str
    primary: int
    instance: int

    def __post_init__(self) -> None:
        if self.kind not in ("function_name", "product_id"):
            raise ValueError(f"invalid StableKey kind: {self.kind!r}")

    def to_config_string(self) -> str:
        return f"{self.kind}={self.primary},{'function_instance' if self.kind == 'function_name' else 'instance'}={self.instance}"

    @classmethod
    def from_config_string(cls, text: str) -> "StableKey":
        parts = dict(item.split("=", 1) for item in text.split(","))
        if "function_name" in parts:
            return cls("function_name", int(parts["function_name"]), int(parts["function_instance"]))
        if "product_id" in parts:
            return cls("product_id", int(parts["product_id"]), int(parts["instance"]))
        raise ValueError(f"unrecognized stable key format: {text!r}")
