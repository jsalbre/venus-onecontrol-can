"""com.victronenergy.switch D-Bus service for a single OneControl relay or
dimmable light device (lights, water pump, water heater). Follows the real
SwitchableOutput path structure used by victronenergy/dbus-shelly.

Phase 3: /SwitchableOutput/0/State (and, for dimmable lights,
/SwitchableOutput/0/Dimming) are writeable. Both onchange callbacks always
return False -- the GUI reverts immediately, exactly like Phase 2 did --
but now also report the write upward via on_command(), which triggers a
real background command attempt (publisher.py/command_sequencer.py). This
class stays a dumb reporter: it never decides whether a command is allowed
(command_gate.py) or builds the CAN frame (command_mapping.py); real state
only ever changes via update_relay()/update_dimmable() being called from an
actually-confirmed DEVICE_STATUS broadcast, never from a write attempt
itself -- so there is no separate "pending" UI state to manage here.

Never used for motor-type devices (awning/slide/jack) -- see
motor_status_service.py and ARCHITECTURE.md's "No Motor Commands" boundary.

Requires dbus/gi (Linux/Venus OS only) -- cannot be imported or run on a
non-Linux dev machine.
"""

from __future__ import annotations

import logging
import os
import sys
import time

sys.path.insert(1, os.path.join(os.path.dirname(__file__), "..", "..", "ext", "velib_python"))

from vedbus import VeDbusService

from can_link.device_status import DimmableLightStatus, OutputState, RelayOrMotorStatus
from can_link.types import StableKey
from dbus_bridge.device_mapping import (
    output_function_for,
    output_type_for,
    stable_id_for,
)

_LOGGER = logging.getLogger(__name__)

STATUS_OFF = 0x00
STATUS_ON = 0x09

CONNECTED = 1
DISCONNECTED = 0

PRODUCT_ID = 0xA001  # self-assigned, not an official Victron product ID
PRODUCT_NAME = "OneControl Switch"

_CHANNEL = 0
_PATH_BASE = f"/SwitchableOutput/{_CHANNEL}/"
_ROOT_CUSTOM_NAME_PATH = "/CustomName"


class SwitchService:
    def __init__(
        self,
        stable_key: StableKey,
        friendly_name: str,
        device_class: str,
        device_instance: int,
        firmware_version: str,
        dbusconn=None,
        on_name_change=None,
        on_command=None,
        initial_group: str = "",
        on_group_change=None,
        initial_show_ui_control: int = 1,
        on_show_ui_control_change=None,
        dimming_capable: bool = True,
    ) -> None:
        self.stable_key = stable_key
        self.friendly_name = friendly_name
        self.device_class = device_class
        self.device_instance = device_instance
        self.on_name_change = on_name_change
        self.on_command = on_command
        self.group = initial_group
        self.on_group_change = on_group_change
        self.show_ui_control = initial_show_ui_control
        self.on_show_ui_control_change = on_show_ui_control_change
        self.dimming_capable = dimming_capable
        # dimming_capable only matters when device_class is actually
        # "dimmable_light" -- see device_mapping.output_type_for()'s
        # docstring for why this is a separate flag from device_class
        # rather than a different device_class value.
        self.is_dimmable = device_class == "dimmable_light" and dimming_capable

        self.service_name = f"com.victronenergy.switch.onecontrol_{stable_id_for(stable_key)}"

        self.last_update_time: float | None = None

        self._dbusservice = VeDbusService(self.service_name, bus=dbusconn, register=False)
        self._add_paths(firmware_version)
        self._dbusservice.register()

        _LOGGER.info(
            "Registered %s (instance=%d, device_class=%s)",
            self.service_name,
            device_instance,
            device_class,
        )

    def _handle_name_change(self, path, value):
        """Shared callback for both name paths (root /CustomName and the
        per-channel Settings/CustomName) -- since this service only ever
        has one channel, they're the same concept and are kept in sync
        regardless of which one was edited (real Venus OS device-list vs.
        the per-output settings page)."""
        if value != self.friendly_name:
            self.friendly_name = value
            with self._dbusservice as s:
                for name_path in (_ROOT_CUSTOM_NAME_PATH, _PATH_BASE + "Settings/CustomName", _PATH_BASE + "Name"):
                    if name_path != path:
                        s[name_path] = value
            if self.on_name_change:
                self.on_name_change(self.stable_key, value)
        return True

    def _handle_group_change(self, path, value):
        """Settings/Group -- purely a GUI panel-grouping label (see
        ARCHITECTURE.md's GUI panel sort/group mechanics note); this
        service does nothing with the value itself beyond persisting it."""
        if value != self.group:
            self.group = value
            if self.on_group_change:
                self.on_group_change(self.stable_key, value)
        return True

    def _handle_show_ui_control_change(self, path, value):
        """Settings/ShowUIControl -- confirmed against Victron's own dbus
        wiki (github.com/victronenergy/venus/wiki/dbus#switch): a bitmask,
        0=hidden everywhere, 1=always shown (bit 0 set overrides the rest),
        2=local UIs only (GX/MFD/WASM), 4=remote UIs only (VRM remote
        console/switch pane). This service does nothing with the value
        itself beyond persisting it, same as Settings/Group."""
        value = int(value)
        if value != self.show_ui_control:
            self.show_ui_control = value
            if self.on_show_ui_control_change:
                self.on_show_ui_control_change(self.stable_key, value)
        return True

    def _handle_state_write(self, path, value):
        """Always returns False (the GUI reverts immediately, matching
        Phase 2's UX) -- the real state only ever changes via a confirmed
        DEVICE_STATUS broadcast reaching update_relay()/update_dimmable().
        Reports the write upward; on_command() (publisher.py) is
        responsible for deciding whether it's actually allowed and, if so,
        attempting it in the background."""
        desired_on = bool(value)
        # DEBUG, not INFO: fires on every D-Bus write, e.g. once per tick
        # while dragging a slider -- see command_gate.py/publisher.py's
        # completed/refused logging for the meaningful-outcome trail.
        _LOGGER.debug("%s: write requested %s=%r (desired_on=%s)", self.service_name, path, value, desired_on)
        if self.on_command:
            self.on_command(self.stable_key, desired_on, None)
        return False

    def _handle_dimming_write(self, path, value):
        """A specific Dimming write of 0 means "turn off"; 1-100 means
        "turn on at exactly this percentage" (see command_mapping.py)."""
        desired_pct = int(value)
        desired_on = desired_pct > 0
        # DEBUG, not INFO: fires on every D-Bus write, e.g. once per tick
        # while dragging a slider -- see the note in _handle_state_write.
        _LOGGER.debug(
            "%s: write requested %s=%r (desired_on=%s, desired_brightness_pct=%s)",
            self.service_name,
            path,
            value,
            desired_on,
            desired_pct if desired_on else None,
        )
        if self.on_command:
            self.on_command(self.stable_key, desired_on, desired_pct if desired_on else None)
        return False

    def _add_paths(self, firmware_version: str) -> None:
        self._dbusservice.add_mandatory_paths(
            processname="onecontrol-can",
            processversion=firmware_version,
            connection="OneControl CAN",
            deviceinstance=self.device_instance,
            productid=PRODUCT_ID,
            productname=PRODUCT_NAME,
            firmwareversion=firmware_version,
            hardwareversion=None,
            connected=CONNECTED,
        )
        self._dbusservice.add_path(
            _ROOT_CUSTOM_NAME_PATH,
            value=self.friendly_name,
            writeable=True,
            onchangecallback=self._handle_name_change,
            description="Device name -- distinct from /ProductName, which is identical across every "
            "OneControl switch device and would otherwise make every panel tie on the GUI's "
            "alphabetical device sort (confirmed against gui-v2's BaseDevice::name()/device.cpp).",
        )

        output_type = output_type_for(self.device_class, self.dimming_capable)
        output_function = output_function_for(self.device_class)

        self._dbusservice.add_path(
            _PATH_BASE + "State",
            value=0,
            writeable=True,
            onchangecallback=self._handle_state_write,
            description="0=off, 1=on",
        )
        self._dbusservice.add_path(
            _PATH_BASE + "Status", value=STATUS_OFF, description="0=off, 9=on"
        )
        self._dbusservice.add_path(_PATH_BASE + "Name", value=self.friendly_name)
        self._dbusservice.add_path(
            _PATH_BASE + "Settings/CustomName",
            value=self.friendly_name,
            writeable=True,
            onchangecallback=self._handle_name_change,
            description="Custom name",
        )
        self._dbusservice.add_path(
            _PATH_BASE + "Settings/Group",
            value=self.group,
            writeable=True,
            onchangecallback=self._handle_group_change,
            description="GUI panel group -- devices sharing a non-empty group name are shown "
            "together in one panel instead of each getting its own.",
        )
        self._dbusservice.add_path(
            _PATH_BASE + "Settings/ShowUIControl",
            value=self.show_ui_control,
            writeable=True,
            onchangecallback=self._handle_show_ui_control_change,
            description="0=hidden everywhere, 1=always shown, 2=local UIs only, 4=remote/VRM UIs only.",
        )
        self._dbusservice.add_path(_PATH_BASE + "Settings/Type", value=int(output_type), writeable=False)
        self._dbusservice.add_path(
            _PATH_BASE + "Settings/Function", value=int(output_function), writeable=False
        )
        self._dbusservice.add_path(_PATH_BASE + "Settings/ValidTypes", value=1 << output_type, writeable=False)
        self._dbusservice.add_path(
            _PATH_BASE + "Settings/ValidFunctions", value=1 << output_function, writeable=False
        )

        if self.is_dimmable:
            self._dbusservice.add_path(
                _PATH_BASE + "Dimming",
                value=0,
                writeable=True,
                onchangecallback=self._handle_dimming_write,
                description="0-100%",
            )

    def update_relay(self, status: RelayOrMotorStatus) -> None:
        is_on = status.output_state != OutputState.OFF_STOP
        with self._dbusservice as s:
            s[_PATH_BASE + "State"] = 1 if is_on else 0
            s[_PATH_BASE + "Status"] = STATUS_ON if is_on else STATUS_OFF
        if self._dbusservice["/Connected"] != CONNECTED:
            self._dbusservice["/Connected"] = CONNECTED
        self.last_update_time = time.time()

    def update_dimmable(self, status: DimmableLightStatus) -> None:
        is_on = status.mode != 0
        dimming_pct = round(status.current_brightness / 255 * 100)
        with self._dbusservice as s:
            s[_PATH_BASE + "State"] = 1 if is_on else 0
            s[_PATH_BASE + "Status"] = STATUS_ON if is_on else STATUS_OFF
            s[_PATH_BASE + "Dimming"] = dimming_pct
        if self._dbusservice["/Connected"] != CONNECTED:
            self._dbusservice["/Connected"] = CONNECTED
        self.last_update_time = time.time()

    def mark_disconnected(self) -> None:
        if self._dbusservice["/Connected"] != DISCONNECTED:
            _LOGGER.warning("%s: disconnected (no broadcasts)", self.service_name)
            self._dbusservice["/Connected"] = DISCONNECTED

    def check_stale(self, threshold_sec: float) -> bool:
        if self.last_update_time is None:
            return False
        return (time.time() - self.last_update_time) > threshold_sec

    def close(self) -> None:
        _LOGGER.info("Closing %s", self.service_name)
        if self._dbusservice:
            # Explicit connection close required -- see tank_service.py's
            # close() for why __del__() alone / relying on GC isn't enough.
            dbusconn = self._dbusservice.dbusconn
            self._dbusservice.__del__()
            self._dbusservice = None
            try:
                dbusconn.close()
            except Exception as e:
                _LOGGER.warning("%s: error closing private D-Bus connection: %s", self.service_name, e)
