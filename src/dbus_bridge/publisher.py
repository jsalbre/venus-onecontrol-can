#!/usr/bin/env python3
"""Main orchestrator: reads the OneControl CAN bus, decodes frames, and
publishes only explicitly-configured devices to Venus OS's D-Bus.

Phase 2 scope: read-only. Never transmits onto the bus (no PID polling, no
commands) -- see TODO.md. The exposure safety gate is two-layered:
ConfigManager.is_exposed() (explicit expose=true required) and
device_mapping.validate_device_class() (config's declared device_class must
match what the device is actually broadcasting). A device failing either
check is never given a D-Bus service, only logged to the discovery log for
the user's review.

Requires dbus/gi (Linux/Venus OS only) -- cannot be imported or run on a
non-Linux dev machine.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

from _version import __version__
from bus.socketcan import SocketCanBus
from can_link.address_table import AddressTable
from can_link.device_id import decode_device_id, stable_key
from can_link.device_status import UnknownDeviceTypeError, decode_status
from can_link.frame import ExtendedId, StandardId, decode_id
from can_link.types import MessageType, StableKey, function_name_label
from dbus_bridge.backoff import RestartBackoff
from dbus_bridge.config_manager import ConfigManager, DiscoveryLog
from dbus_bridge.device_mapping import assign_device_instance, fluid_type_for
from dbus_bridge.motor_status_service import MotorStatusService
from dbus_bridge.routing import DeviceIdAction, route_device_id, status_update_method_for
from dbus_bridge.switch_service import SwitchService
from dbus_bridge.tank_service import TankService

_LOGGER = logging.getLogger(__name__)


@dataclass
class _ServiceEntry:
    service: object
    device_class: str
    kind: str


class Publisher:
    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self.config_manager = ConfigManager(config_path)
        self.discovery_log = DiscoveryLog(config_path.parent / "discovered_devices.json")
        self.config = self.config_manager.read()

        self.address_table = AddressTable(
            address_expiry_sec=self.config.get("address_expiry_sec", 8.0),
            bus_outage_threshold_sec=self.config.get("bus_outage_threshold_sec", 12.0),
        )
        self._address_to_key: dict[int, StableKey] = {}
        self.services: dict[str, _ServiceEntry] = {}

        self.bus: SocketCanBus | None = None
        self._watch_id: int | None = None
        self.mainloop = None
        self.shutdown_requested = False

        self.backoff = RestartBackoff(
            min_delay_sec=self.config.get("restart_min_delay_sec", 30),
            max_delay_sec=self.config.get("restart_max_delay_sec", 300),
            reset_after_sec=3600,
        )

        self._setup_logging()
        _LOGGER.info("venus-onecontrol-can v%s initializing", __version__)

    def _setup_logging(self) -> None:
        log_level = self.config.get("log_level", "INFO")
        log_path = self.config_path.parent / "logs" / "onecontrol-can.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        handler = RotatingFileHandler(log_path, maxBytes=10 * 1024 * 1024, backupCount=7)
        formatter = logging.Formatter(
            "%(asctime)s - %(levelname)s - %(name)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )
        handler.setFormatter(formatter)

        root_logger = logging.getLogger()
        root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
        root_logger.addHandler(handler)
        root_logger.addHandler(logging.StreamHandler())

    def _save_friendly_name(self, key: StableKey, new_name: str) -> None:
        try:
            self.config_manager.update_friendly_name(key, new_name)
        except KeyError:
            _LOGGER.warning("Cannot save name for %s: not in config", key.to_config_string())

    def _handle_frame(self, can_id: int, is_extended: bool, data: bytes, now: float) -> None:
        self.address_table.note_bus_activity(now)
        decoded_id = decode_id(can_id, is_extended)

        if isinstance(decoded_id, ExtendedId):
            return  # Phase 2 doesn't interpret point-to-point traffic

        assert isinstance(decoded_id, StandardId)
        if decoded_id.message_type == MessageType.DEVICE_ID:
            self._handle_device_id(decoded_id.source_address, data, now)
        elif decoded_id.message_type == MessageType.DEVICE_STATUS:
            self._handle_device_status(decoded_id.source_address, data, now)

    def _handle_device_id(self, source_address: int, payload: bytes, now: float) -> None:
        try:
            identity = decode_device_id(payload)
        except ValueError as e:
            _LOGGER.debug("Malformed DEVICE_ID from 0x%02X: %s", source_address, e)
            return

        key = stable_key(identity)
        self.address_table.observe_device_id(key, source_address, identity.device_type, now)
        self._address_to_key[source_address] = key

        routing = route_device_id(
            key, identity, self.config_manager, already_created=key.to_config_string() in self.services
        )

        if routing.action == DeviceIdAction.NOT_EXPOSED:
            type_label = identity.device_type.name if identity.device_type else f"RAW_{identity.device_type_raw}"
            self.discovery_log.record(key, type_label, function_name_label(identity.function_name))
        elif routing.action == DeviceIdAction.ALREADY_CREATED:
            pass
        elif routing.action == DeviceIdAction.MISSING_DEVICE_CLASS:
            _LOGGER.warning(
                "%s is exposed but has no device_class in config -- refusing to create a service",
                key.to_config_string(),
            )
        elif routing.action == DeviceIdAction.CLASS_MISMATCH:
            _LOGGER.warning(
                "%s: config declares device_class=%r but the device is broadcasting DeviceType=%r "
                "-- refusing to create a service. Fix the config entry.",
                key.to_config_string(),
                routing.device_class,
                identity.device_type,
            )
        elif routing.action == DeviceIdAction.CREATE_SERVICE:
            self._create_service(key, routing.device_class, routing.service_kind, now)

    def _create_service(self, key: StableKey, device_class: str, kind: str, now: float) -> None:
        import dbus

        friendly_name = self.config_manager.get_friendly_name(key) or "OneControl Device"

        persisted = self.config_manager.get_device_instance(key)
        already_assigned = self.config_manager.get_instances_by_kind(kind)
        device_instance = assign_device_instance(kind, key, already_assigned, persisted)
        if persisted is None:
            self.config_manager.set_device_instance(key, device_instance)

        dbusconn = dbus.SystemBus(private=True)

        try:
            if kind == "tank":
                service = TankService(
                    key, friendly_name, fluid_type_for(key), device_instance, __version__,
                    dbusconn=dbusconn, on_name_change=self._save_friendly_name,
                )
            elif kind == "switch":
                service = SwitchService(
                    key, friendly_name, device_class, device_instance, __version__,
                    dbusconn=dbusconn, on_name_change=self._save_friendly_name,
                )
            elif kind == "motor_status":
                service = MotorStatusService(
                    key, friendly_name, device_instance, __version__,
                    dbusconn=dbusconn, on_name_change=self._save_friendly_name,
                )
            else:
                _LOGGER.error("Unhandled service kind %r for %s", kind, key.to_config_string())
                return
        except Exception as e:
            _LOGGER.error("Failed to create service for %s: %s", key.to_config_string(), e, exc_info=True)
            return

        self.services[key.to_config_string()] = _ServiceEntry(service, device_class, kind)
        _LOGGER.info("Created %s service for %s (%s)", kind, key.to_config_string(), friendly_name)

    def _handle_device_status(self, source_address: int, payload: bytes, now: float) -> None:
        key = self._address_to_key.get(source_address)
        if key is None:
            return
        entry = self.services.get(key.to_config_string())
        if entry is None:
            return

        device_type = self.address_table.device_type_for(key, now)
        try:
            status = decode_status(device_type, payload)
        except (UnknownDeviceTypeError, ValueError) as e:
            _LOGGER.debug("Cannot decode DEVICE_STATUS for %s: %s", key.to_config_string(), e)
            return

        method_name = status_update_method_for(entry.kind, entry.device_class)
        getattr(entry.service, method_name)(status)

    def _check_stale_services(self) -> bool:
        threshold = self.config.get("stale_threshold_sec", 300)
        for entry in self.services.values():
            if entry.service.check_stale(threshold):
                entry.service.mark_disconnected()
        return True

    def _on_socket_readable(self, fd, condition) -> bool:
        try:
            frame = self.bus.recv()
        except OSError as e:
            _LOGGER.error("CAN bus read error: %s", e)
            self.mainloop.quit()
            return False

        try:
            self._handle_frame(frame.can_id, frame.is_extended, frame.data, time.time())
        except Exception as e:
            _LOGGER.error("Error handling frame: %s", e, exc_info=True)
        return True

    def _signal_handler(self, signum, frame) -> None:
        _LOGGER.info("Received signal %s, shutting down gracefully", signal.Signals(signum).name)
        self.shutdown_requested = True
        if self.mainloop:
            self.mainloop.quit()

    def run(self) -> None:
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

        while not self.shutdown_requested:
            try:
                self.config = self.config_manager.read()
                self.discovery_log.prune_configured(
                    {d["stable_key"] for d in self.config.get("devices", [])}
                )

                DBusGMainLoop(set_as_default=True)

                interface = self.config.get("can_interface", "vecan1")
                self.bus = SocketCanBus(interface)
                _LOGGER.info("Listening on %s", interface)

                self.mainloop = GLib.MainLoop()
                self._watch_id = GLib.io_add_watch(self.bus.fileno(), GLib.IO_IN, self._on_socket_readable)
                GLib.timeout_add(30000, self._check_stale_services)

                self.backoff.mark_success(now=time.time())
                _LOGGER.info("Service running - entering main loop")
                self.mainloop.run()
                _LOGGER.info("Main loop exited")

                self._cleanup()

                if self.shutdown_requested:
                    _LOGGER.info("Shutdown complete")
                    break

                _LOGGER.warning("Service crashed, restarting with backoff")
                time.sleep(self.backoff.next_delay_sec())
                self.backoff.reset_if_stable(now=time.time())

            except Exception as e:
                _LOGGER.error("Fatal error in main loop: %s", e, exc_info=True)
                self._cleanup()
                if self.shutdown_requested:
                    break
                time.sleep(self.backoff.next_delay_sec())

    def _cleanup(self) -> None:
        _LOGGER.info("Cleaning up resources")
        if self.bus:
            try:
                self.bus.close()
            except OSError as e:
                _LOGGER.error("Error closing CAN bus: %s", e)
            self.bus = None

        for entry in list(self.services.values()):
            try:
                entry.service.close()
            except Exception as e:
                _LOGGER.error("Error closing service: %s", e)
        self.services.clear()
        self._address_to_key.clear()
        self.mainloop = None


def main() -> None:
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/data/venus-onecontrol-can/config.json")
    Publisher(config_path).run()


if __name__ == "__main__":
    main()
