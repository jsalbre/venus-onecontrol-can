"""com.victronenergy.switch D-Bus service for a single OneControl relay or
dimmable light device (lights, water pump, water heater). Follows the real
SwitchableOutput path structure used by victronenergy/dbus-shelly.

Phase 2 (current): read-only. /SwitchableOutput/0/State is registered
writeable=True (so it renders as a normal interactive switch in the Cerbo
GUI, matching how Shelly switches look), but the onchange callback always
rejects the write and logs clearly -- there is no command path wired up
yet. Phase 3 will replace the rejecting callback with a real one that
drives can_link.command/session through the address table's safety gate.

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
    ) -> None:
        self.stable_key = stable_key
        self.friendly_name = friendly_name
        self.device_class = device_class
        self.device_instance = device_instance
        self.on_name_change = on_name_change
        self.is_dimmable = device_class == "dimmable_light"

        self.service_name = f"com.victronenergy.switch.onecontrol_{stable_id_for(stable_key)}"

        self.last_update_time: float | None = None

        self._dbusservice = VeDbusService(self.service_name, bus=dbusconn, register=False)
        self._add_paths(firmware_version)
        self._dbusservice.register()

        _LOGGER.info(
            "Registered %s (instance=%d, device_class=%s, Phase 2 read-only)",
            self.service_name,
            device_instance,
            device_class,
        )

    def _handle_name_change(self, path, value):
        if value != self.friendly_name:
            self.friendly_name = value
            if self.on_name_change:
                self.on_name_change(self.stable_key, value)
        return True

    def _reject_state_write(self, path, value):
        """Phase 2: no command path is wired up yet. Reject every write
        attempt rather than silently accept a value the device never
        actually received."""
        _LOGGER.warning(
            "%s: rejected write to %s=%r -- command path not implemented yet (Phase 3)",
            self.service_name,
            path,
            value,
        )
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

        output_type = output_type_for(self.device_class)
        output_function = output_function_for(self.device_class)

        self._dbusservice.add_path(
            _PATH_BASE + "State",
            value=0,
            writeable=True,
            onchangecallback=self._reject_state_write,
            description="0=off, 1=on (read-only until Phase 3)",
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
        self._dbusservice.add_path(_PATH_BASE + "Settings/Type", value=int(output_type), writeable=False)
        self._dbusservice.add_path(
            _PATH_BASE + "Settings/Function", value=int(output_function), writeable=False
        )
        self._dbusservice.add_path(_PATH_BASE + "Settings/ValidTypes", value=1 << output_type, writeable=False)
        self._dbusservice.add_path(
            _PATH_BASE + "Settings/ValidFunctions", value=1 << output_function, writeable=False
        )

        if self.is_dimmable:
            self._dbusservice.add_path(_PATH_BASE + "Dimming", value=0, description="0-100%")

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
            self._dbusservice.__del__()
            self._dbusservice = None
