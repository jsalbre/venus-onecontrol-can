"""Pure decision logic for whether a SwitchService write should actually be
attempted. No dbus/gi imports -- testable without D-Bus, matching
routing.py's pattern (which makes the equivalent decision for D-Bus service
*creation*; this is the analogous gate for *commands*).

Fails closed at every step, in order from cheapest/most-fundamental to
most dynamic: exposed in config, commands explicitly enabled for this
device, device_class is one this project ever sends commands for (never
motor_status/tank), and finally the safety-critical live check --
address_table.resolve_for_command(), which is also re-checked a second time
by command_sequencer.py immediately before the COMMAND frame is built. This
module's NOT_VERIFIED result is the first (cheap, early-refusal) check;
that second one is what actually protects against a race during the
handshake.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from can_link.address_table import AddressTable
from can_link.types import StableKey
from dbus_bridge.config_manager import ConfigManager

COMMANDABLE_DEVICE_CLASSES = frozenset({"relay_light", "relay_pump", "relay_water_heater", "dimmable_light"})


class CommandGateResult(Enum):
    NOT_EXPOSED = "not_exposed"
    COMMANDS_NOT_ENABLED = "commands_not_enabled"
    UNSUPPORTED_DEVICE_CLASS = "unsupported_device_class"
    NOT_VERIFIED = "not_verified"
    OK = "ok"


@dataclass(frozen=True)
class CommandGateDecision:
    result: CommandGateResult
    device_class: str | None = None
    target_address: int | None = None


def evaluate_command_request(
    key: StableKey,
    config_manager: ConfigManager,
    address_table: AddressTable,
    now: float,
) -> CommandGateDecision:
    if not config_manager.is_exposed(key):
        return CommandGateDecision(CommandGateResult.NOT_EXPOSED)

    if not config_manager.commands_enabled_for(key):
        return CommandGateDecision(CommandGateResult.COMMANDS_NOT_ENABLED)

    device_class = config_manager.get_device_class(key)
    if device_class not in COMMANDABLE_DEVICE_CLASSES:
        return CommandGateDecision(CommandGateResult.UNSUPPORTED_DEVICE_CLASS, device_class=device_class)

    target_address = address_table.resolve_for_command(key, now)
    if target_address is None:
        return CommandGateDecision(CommandGateResult.NOT_VERIFIED, device_class=device_class)

    return CommandGateDecision(CommandGateResult.OK, device_class=device_class, target_address=target_address)
