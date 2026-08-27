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
from dataclasses import dataclass
from enum import IntEnum

from can_link.types import (
    COMMANDABLE_DEVICE_TYPES,
    DeviceType,
    StableKey,
    function_name_label,
)

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
    "battery_voltage": frozenset({DeviceType.CHASSIS_INFO}),
}

DEVICE_CLASS_SERVICE_KIND: dict[str, str] = {
    "tank": "tank",
    "relay_light": "switch",
    "relay_pump": "switch",
    "relay_water_heater": "switch",
    "dimmable_light": "switch",
    "battery_voltage": "battery",
}

# Device instance ranges, offset by service kind so different kinds never
# collide. Each range must comfortably exceed the number of devices of that
# kind ever expected on one coach -- see assign_device_instance(). "motor_status"
# (base 800) removed 2026-08-24 along with the rest of motor status support --
# see ARCHITECTURE.md's "Motor Status Support -- Removed" note before reusing
# base 800 for anything else, to avoid colliding with any already-persisted
# motor_status device_instance still sitting in an existing config.json.
INSTANCE_BASE: dict[str, int] = {"tank": 20, "switch": 700, "battery": 900}
INSTANCE_RANGE = 100

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


def output_type_for(device_class: str, dimming_capable: bool = True) -> OutputType:
    """dimming_capable is only consulted for device_class="dimmable_light"
    -- it's how publisher.py's live PID 161 read (2026-08-24, see
    ARCHITECTURE.md) tells a dimmable_light configured to behave as a plain
    on/off switch (SIMULATE_ON_OFF_STYLE_LIGHT=1) apart from a real dimmer,
    without changing device_class itself (which stays "dimmable_light" for
    command-building purposes -- the underlying CAN command shape is
    unaffected by this PID, only the device's own physical response is)."""
    if device_class == "dimmable_light" and dimming_capable:
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


def assign_device_instance(
    kind: str, key: StableKey, already_assigned: dict[str, int], persisted: int | None
) -> int:
    """Returns the device instance to use for `key`, guaranteed unique
    against every instance in `already_assigned` (other devices of the same
    kind, keyed by their stable_key config string).

    If `persisted` is given (this key already has a saved instance), it's
    returned unchanged -- an instance must never change once assigned, since
    Venus OS ties GUI customization (position, name) to it. Otherwise a
    starting candidate is derived deterministically from the stable key
    (so instances stay reasonably stable across a fresh install even before
    persistence kicks in), and linearly probed forward within the kind's
    range until a free slot is found -- `stable_id_for()` alone is only
    evenly distributed, not collision-free, and a real collision was
    observed in practice with as few as 4 devices of one kind.

    The caller is responsible for persisting the result (ConfigManager) the
    first time this returns a freshly-assigned (non-persisted) instance.
    """
    if persisted is not None:
        return persisted

    base = INSTANCE_BASE[kind]
    used = set(already_assigned.values())
    candidate = base + stable_id_for(key, modulo=INSTANCE_RANGE)
    for _ in range(INSTANCE_RANGE):
        if candidate not in used:
            return candidate
        candidate += 1
        if candidate >= base + INSTANCE_RANGE:
            candidate = base
    raise RuntimeError(f"no free device instance in kind={kind!r} range (all {INSTANCE_RANGE} slots used)")


def fluid_type_for(stable_key: StableKey) -> int | None:
    """Derives the tank's Venus OS FluidType from its stable key's
    FUNCTION_NAME, when known. Returns None if not derivable (e.g. an
    unconfigured/unnamed tank sensor)."""
    if stable_key.kind == "function_name":
        return _FUNCTION_NAME_TO_FLUID_TYPE.get(stable_key.primary)
    return None


# FUNCTION_NAME codes that sub-classify a relay-family DEVICE_TYPE as a
# water heater or pump rather than the generic "relay_light" default.
# Deliberately narrow: e.g. "Fresh Tank Heater"/"Tank Heater"/"Holding Tanks
# Heater" are freeze-protection relays on a tank, not a domestic water
# heater, so they're excluded and fall back to relay_light.
_WATER_HEATER_FUNCTION_NAMES = frozenset({3, 4, 295, 296})  # Gas/Electric Water Heater, Water Heater(s)
_PUMP_FUNCTION_NAMES = frozenset({5, 191})  # Water Pump, Fuel Pump


def infer_device_class(stable_key: StableKey, device_type_label: str) -> str | None:
    """Infers device_class from what the device itself already broadcasts
    (DEVICE_TYPE + FUNCTION_NAME) -- no user input needed, since this is
    fully determined by the bus, the same way a human reviewing the
    discovery log would work it out. Returns None if DEVICE_TYPE is
    unrecognized (device_type_label doesn't match a DeviceType member, e.g.
    a raw/unmapped value) or isn't one of the supported device classes (e.g.
    GENERATOR_GENIE, BLUETOOTH_GATEWAY have no service type in this
    project). Motor DeviceTypes (awning/slide/jack) are also unsupported --
    motor status support was removed (see ARCHITECTURE.md's "Motor Status
    Support -- Removed" note), so a motor now returns None the same as any
    other unsupported DEVICE_TYPE, and won't be offered in manage-devices'
    addable-device list."""
    try:
        device_type = DeviceType[device_type_label]
    except KeyError:
        return None

    if device_type == DeviceType.TANK_SENSOR:
        return "tank"
    if device_type == DeviceType.DIMMABLE_LIGHT:
        return "dimmable_light"
    if device_type == DeviceType.CHASSIS_INFO:
        return "battery_voltage"
    if device_type in _RELAY_DEVICE_TYPES:
        if stable_key.kind == "function_name":
            if stable_key.primary in _WATER_HEATER_FUNCTION_NAMES:
                return "relay_water_heater"
            if stable_key.primary in _PUMP_FUNCTION_NAMES:
                return "relay_pump"
        return "relay_light"
    return None


@dataclass(frozen=True)
class AddableDevice:
    stable_key: StableKey
    device_class: str
    suggested_friendly_name: str


def build_addable_list(discovered: dict, already_configured: set) -> list[AddableDevice]:
    """Filters discovered_devices.json entries down to ones that could
    actually be added: not already configured, a supported device_class
    (infer_device_class() succeeded), and NOT a product_id-fallback key.
    product_id-fallback keys are excluded deliberately -- they're the
    known-empty/unconfigured Unity module ports documented in
    ARCHITECTURE.md, and multiple physical (non-)devices share the exact
    same fallback identity, so there's no reliable device to enable there.
    device_type-keyed entries (SYSTEM_SINGLETON_DEVICE_TYPES, e.g.
    CHASSIS_INFO) don't have this ambiguity problem -- see stable_key() in
    device_id.py -- so they're allowed through here."""
    result = []
    for key_str, info in discovered.items():
        if key_str in already_configured:
            continue
        try:
            key = StableKey.from_config_string(key_str)
        except ValueError:
            continue
        if key.kind not in ("function_name", "device_type"):
            continue

        device_class = infer_device_class(key, info.get("device_type", ""))
        if device_class is None:
            continue

        if key.kind == "device_type":
            name = "OneControl Battery Voltage"
        else:
            name = function_name_label(key.primary)
            if key.instance:
                name = f"{name} {key.instance}"

        result.append(AddableDevice(key, device_class, name))
    return result
