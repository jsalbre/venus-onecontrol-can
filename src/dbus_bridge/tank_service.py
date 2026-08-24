"""com.victronenergy.tank D-Bus service for a single OneControl tank sensor.

Requires dbus/gi (Linux/Venus OS only) -- cannot be imported or run on a
non-Linux dev machine. Structure mirrors
govee-ble-venus-py/src/govee_temperature_service.py.
"""

from __future__ import annotations

import logging
import os
import sys
import time

sys.path.insert(1, os.path.join(os.path.dirname(__file__), "..", "..", "ext", "velib_python"))

from vedbus import VeDbusService

from can_link.device_status import TankSensorStatus
from can_link.types import StableKey
from dbus_bridge.device_mapping import stable_id_for

_LOGGER = logging.getLogger(__name__)

STATUS_OK = 0
STATUS_DISCONNECTED = 1

# Not derivable from fluid_type_for() (e.g. an unconfigured/unnamed tank the
# user chose to expose manually) -- default to Raw water rather than
# something misleading like Fuel.
DEFAULT_FLUID_TYPE = 11

PRODUCT_ID = 0xA000  # self-assigned, not an official Victron product ID
PRODUCT_NAME = "OneControl Tank Sensor"


class TankService:
    def __init__(
        self,
        stable_key: StableKey,
        friendly_name: str,
        fluid_type: int | None,
        device_instance: int,
        firmware_version: str,
        dbusconn=None,
        on_name_change=None,
    ) -> None:
        self.stable_key = stable_key
        self.friendly_name = friendly_name
        self.fluid_type = fluid_type if fluid_type is not None else DEFAULT_FLUID_TYPE
        self.device_instance = device_instance
        self.on_name_change = on_name_change

        self.service_name = f"com.victronenergy.tank.onecontrol_{stable_id_for(stable_key)}"

        self.last_update_time: float | None = None

        self._dbusservice = VeDbusService(self.service_name, bus=dbusconn, register=False)
        self._add_paths(firmware_version)
        self._dbusservice.register()

        _LOGGER.info("Registered %s (instance=%d)", self.service_name, device_instance)

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
            connected=1,
        )
        self._dbusservice.add_path("/Level", value=None, description="Tank level in percent")
        self._dbusservice.add_path(
            "/Status", value=STATUS_OK, description="0=Ok, 1=Disconnected, 3=Unknown, 4=Configuration error"
        )
        self._dbusservice.add_path("/FluidType", value=self.fluid_type, description="Venus OS FluidType enum")
        self._dbusservice.add_path(
            "/CustomName",
            value=self.friendly_name,
            writeable=True,
            onchangecallback=self._handle_name_change,
            description="Custom name",
        )

    def update(self, status: TankSensorStatus) -> None:
        now = time.time()
        was_disconnected = self._dbusservice["/Status"] == STATUS_DISCONNECTED
        with self._dbusservice as s:
            s["/Level"] = status.fill_level_pct
            s["/Status"] = STATUS_OK
            s["/Connected"] = 1
        if was_disconnected:
            _LOGGER.info("%s: reconnected", self.service_name)
        self.last_update_time = now

    def mark_disconnected(self) -> None:
        if self._dbusservice["/Status"] != STATUS_DISCONNECTED:
            _LOGGER.warning("%s: disconnected (no broadcasts)", self.service_name)
            self._dbusservice["/Status"] = STATUS_DISCONNECTED
            self._dbusservice["/Connected"] = 0

    def check_stale(self, threshold_sec: float) -> bool:
        if self.last_update_time is None:
            return False
        return (time.time() - self.last_update_time) > threshold_sec

    def close(self) -> None:
        _LOGGER.info("Closing %s", self.service_name)
        if self._dbusservice:
            # Each service gets its own private dbus.SystemBus connection
            # (see publisher.py's _create_service) -- __del__() releases the
            # bus name and unregisters paths but doesn't close the
            # underlying connection itself, and letting it fall to Python's
            # garbage collector isn't reliable here (VeDbusService's own
            # internal objects hold reference cycles back to it). Must close
            # explicitly or a restart-heavy process leaks a connection per
            # service per restart.
            dbusconn = self._dbusservice.dbusconn
            self._dbusservice.__del__()
            self._dbusservice = None
            try:
                dbusconn.close()
            except Exception as e:
                _LOGGER.warning("%s: error closing private D-Bus connection: %s", self.service_name, e)
