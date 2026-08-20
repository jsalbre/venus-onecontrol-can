"""Read-only D-Bus service for motor status (awning/slide/leveling jack).

Deliberately NOT com.victronenergy.switch and has no writable state path of
any kind (only /CustomName, a label, is writable) -- see
ARCHITECTURE.md's "No Motor Commands" safety boundary. This service must
never look controllable, not even as a rejected write. Not a recognized
Venus OS device panel type, so it won't render specially in the Cerbo GUI --
visible via dbus-spy/MQTT/Node-RED only. That is intentional, not a
limitation to fix.

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

from can_link.device_status import RelayOrMotorStatus
from can_link.types import StableKey
from dbus_bridge.device_mapping import stable_id_for

_LOGGER = logging.getLogger(__name__)

CONNECTED = 1
DISCONNECTED = 0

PRODUCT_ID = 0xA002  # self-assigned, not an official Victron product ID
PRODUCT_NAME = "OneControl Motor Status (read-only)"


class MotorStatusService:
    def __init__(
        self,
        stable_key: StableKey,
        friendly_name: str,
        device_instance: int,
        firmware_version: str,
        dbusconn=None,
        on_name_change=None,
    ) -> None:
        self.stable_key = stable_key
        self.friendly_name = friendly_name
        self.device_instance = device_instance
        self.on_name_change = on_name_change

        self.service_name = f"com.victronenergy.genericstatus.onecontrol_motor_{stable_id_for(stable_key)}"

        self.last_update_time: float | None = None

        self._dbusservice = VeDbusService(self.service_name, bus=dbusconn, register=False)
        self._add_paths(firmware_version)
        self._dbusservice.register()

        _LOGGER.info(
            "Registered %s (instance=%d, status-only, no command path)",
            self.service_name,
            device_instance,
        )

    def _handle_name_change(self, path, value):
        if value != self.friendly_name:
            self.friendly_name = value
            if self.on_name_change:
                self.on_name_change(self.stable_key, value)
        return True

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
            "/MotorState", value=None, description="0=off/stop, 1=on, 2=forward/extend, 3=reverse/retract"
        )
        self._dbusservice.add_path("/PositionPercent", value=None, description="0-100%, absent if not supported")
        self._dbusservice.add_path("/CurrentAmps", value=None, description="Amps, absent if not supported")
        self._dbusservice.add_path("/FaultLatch", value=0, description="1=fault latch active")
        self._dbusservice.add_path("/Dtc", value=0, description="Diagnostic trouble code")
        self._dbusservice.add_path(
            "/CustomName",
            value=self.friendly_name,
            writeable=True,
            onchangecallback=self._handle_name_change,
            description="Custom name",
        )

    def update(self, status: RelayOrMotorStatus) -> None:
        self._dbusservice["/MotorState"] = int(status.output_state)
        self._dbusservice["/PositionPercent"] = status.position_pct
        self._dbusservice["/CurrentAmps"] = status.current_draw_amps
        self._dbusservice["/FaultLatch"] = 1 if status.fault_latch else 0
        self._dbusservice["/Dtc"] = status.dtc
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
