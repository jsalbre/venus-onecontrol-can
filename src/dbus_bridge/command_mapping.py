"""Pure device_class -> CanFrame dispatch for a SwitchService write request.
No dbus/gi imports -- testable without D-Bus, matching command_gate.py and
routing.py's pattern. command_gate.py is the actual enforcement point for
whether a write is allowed at all; this module only decides which frame a
write maps to, once that's already been decided.

switch_service.py has two writeable paths that both funnel into this
module's desired_on/desired_brightness_pct shape: a State write carries no
explicit brightness (desired_brightness_pct=None -- turning on resumes the
light's own last brightness, turning off ignores it); a Dimming write of 0
is translated to desired_on=False before it reaches here (see
switch_service.py); a Dimming write of 1-100 is desired_on=True with that
exact percentage, converted here to CAN's raw 0-255 scale to match
build_dimmable_light_command()/DEVICE_STATUS.current_brightness -- see
command.py's docstring for why that's a raw byte, not a percentage.
"""

from __future__ import annotations

from can_link.command import (
    RelayCommandMode,
    build_dimmable_light_command,
    build_dimmable_light_toggle_command,
    build_relay_command,
)
from can_link.frame import CanFrame

_RELAY_DEVICE_CLASSES = frozenset({"relay_light", "relay_pump", "relay_water_heater"})


def command_frame_for_switch_write(
    device_class: str,
    source_address: int,
    target_address: int,
    desired_on: bool,
    desired_brightness_pct: int | None = None,
) -> CanFrame:
    """Builds the CAN COMMAND frame for a SwitchService write.
    desired_brightness_pct (0-100), when given, only has an effect for
    dimmable_light and only when desired_on is True -- a specific
    percentage from a Dimming-path write. Raises ValueError for a
    device_class this project doesn't send commands for (never called for
    motor_status/tank in practice -- see command_gate.py)."""
    if desired_brightness_pct is not None and not (0 <= desired_brightness_pct <= 100):
        raise ValueError(f"desired_brightness_pct must be 0-100, got {desired_brightness_pct}")

    if device_class in _RELAY_DEVICE_CLASSES:
        mode = RelayCommandMode.ON if desired_on else RelayCommandMode.OFF
        return build_relay_command(source_address, target_address, mode)

    if device_class == "dimmable_light":
        if desired_on and desired_brightness_pct is not None:
            raw_brightness = round(desired_brightness_pct / 100 * 255)
            return build_dimmable_light_command(
                source_address,
                target_address,
                mode=1,
                brightness=raw_brightness,
                auto_off_minutes=0,
                t1_ms=0,
                t2_ms=0,
            )
        return build_dimmable_light_toggle_command(source_address, target_address, turn_on=desired_on)

    raise ValueError(f"no command builder for device_class={device_class!r}")
