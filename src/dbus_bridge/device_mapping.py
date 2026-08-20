"""Pure mapping/validation logic between config device_class values, live
observed DeviceType, and Venus OS D-Bus service shape. No dbus/gi imports --
testable without D-Bus.

This is the second half of the safety design (the first half is
ConfigManager.is_exposed()): even for a device explicitly enabled in
config, validate_device_class() cross-checks the config's declared
device_class against what the bus is *actually* broadcasting right now,
so a stale or mistaken config entry can't create a service that
misrepresents (or worse, mis-commands, in Phase 3) a device.
"""

from __future__ import annotations

import zlib
from enum import IntEnum

from can_link.types import COMMANDABLE_DEVICE_TYPES, MOTOR_DEVICE_TYPES, DeviceType, StableKey

# Venus OS SwitchableOutput API enums (confirmed against victronenergy/dbus-shelly's
# real driver -- these are the platform's own values, not Shelly-specific).
class OutputType(IntEnum):
    MOMENTARY = 0
    TOGGLE = 1
    DIMMABLE = 2
    THREE_STATE_SWITCH = 9
    RGB = 11
    CCT = 12
    RGBW = 13


class OutputFunction(IntEnum):
    ALARM = 0
    GENSET_START_STOP = 1
    MANUAL = 2
    TANK_PUMP = 3
    TEMPERATURE = 4
    CONNECTED_GENSET_HELPER_RELAY = 5
    OPPORTUNITY_LOAD = 6


# Non-motor relay types -- the subset of COMMANDABLE_DEVICE_TYPES that isn't
# DIMMABLE_LIGHT. Shared by relay_light/relay_pump/relay_water_heater since
# they all use the same decode_relay_or_motor() struct and only differ by
# config-declared purpose, not by protocol.
_RELAY_DEVICE_TYPES = frozenset(COMMANDABLE_DEVICE_TYPES - {DeviceType.DIMMABLE_LIGHT})

DEVICE_CLASS_EXPECTED_TYPES: dict[str, frozenset[DeviceType]] = {
    "tank": frozenset({DeviceType.TANK_SENSOR}),
    "relay_light": _RELAY_DEVICE_TYPES,
    "relay_pump": _RELAY_DEVICE_TYPES,
    "relay_water_heater": _RELAY_DEVICE_TYPES,
    "dimmable_light": frozenset({DeviceType.DIMMABLE_LIGHT}),
    "motor_status": frozenset(MOTOR_DEVICE_TYPES),
}

DEVICE_CLASS_SERVICE_KIND: dict[str, str] = {
    "tank": "tank",
    "relay_light": "switch",
    "relay_pump": "switch",
    "relay_water_heater": "switch",
    "dimmable_light": "switch",
    "motor_status": "motor_status",
}

# FUNCTION_NAME -> com.victronenergy.tank /FluidType value (per Venus OS's
# documented FluidType enum: 0=Fuel,1=Fresh water,2=Waste water,3=Live well,
# 4=Oil,5=Black water(sewage),...).
_FUNCTION_NAME_TO_FLUID_TYPE: dict[int, int] = {
    67: 1,  # FRESH_TANK
    68: 2,  # GREY_TANK
    69: 5,  # BLACK_TANK
    70: 0,  # FUEL_TANK
}


def service_kind_for(device_class: str) -> str | None:
    """'tank' / 'switch' / 'motor_status', or None for an unrecognized
    device_class."""
    return DEVICE_CLASS_SERVICE_KIND.get(device_class)


def validate_device_class(device_class: str, observed_type: DeviceType | None) -> bool:
    """True only if observed_type (from a live DEVICE_ID broadcast) is
    consistent with device_class. Fails closed: an unrecognized
    device_class or an unrecognized/None observed_type is never valid."""
    expected = DEVICE_CLASS_EXPECTED_TYPES.get(device_class)
    if expected is None or observed_type is None:
        return False
    return observed_type in expected


def output_type_for(device_class: str) -> OutputType:
    if device_class == "dimmable_light":
        return OutputType.DIMMABLE
    return OutputType.TOGGLE


def output_function_for(device_class: str) -> OutputFunction:
    if device_class == "relay_pump":
        return OutputFunction.TANK_PUMP
    return OutputFunction.MANUAL


def stable_id_for(stable_key: StableKey, modulo: int = 10000) -> int:
    """A small, deterministic integer derived from a stable key, for D-Bus
    service-name suffixes and device instance numbers. MUST be stable
    across process restarts -- unlike Python's builtin hash(), which is
    randomized per-process (PYTHONHASHSEED) and would silently change the
    service name/instance on every restart, breaking any GUI customization
    (CustomName, position) tied to it."""
    return zlib.crc32(stable_key.to_config_string().encode()) % modulo


def fluid_type_for(stable_key: StableKey) -> int | None:
    """Derives the tank's Venus OS FluidType from its stable key's
    FUNCTION_NAME, when known. Returns None if not derivable (e.g. an
    unconfigured/unnamed tank sensor)."""
    if stable_key.kind == "function_name":
        return _FUNCTION_NAME_TO_FLUID_TYPE.get(stable_key.primary)
    return None
