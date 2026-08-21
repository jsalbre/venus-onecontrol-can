#!/usr/bin/env python3
"""Main orchestrator: reads the OneControl CAN bus, decodes frames,
publishes only explicitly-configured devices to Venus OS's D-Bus, and (from
Phase 3 on) sends safe commands back for devices with commands_enabled.

The exposure safety gate is two-layered: ConfigManager.is_exposed()
(explicit expose=true required) and device_mapping.validate_device_class()
(config's declared device_class must match what the device is actually
broadcasting). A device failing either check is never given a D-Bus
service, only logged to the discovery log for the user's review.

The command safety gate is layered further still: command_gate.py's
evaluate_command_request() (exposed + commands_enabled + supported
device_class + address_table.resolve_for_command all pass) is the cheap
early check run synchronously from a D-Bus write; command_sequencer.py
re-runs resolve_for_command a second time immediately before the COMMAND
frame is built, since a bus outage can be detected during the ~1-3ms
handshake. Sending a command requires this bridge to hold a CAN source
address of its own -- see can_link/address_claim.py -- claimed once at
startup and held for as long as the service runs.

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
from bus.socketcan import SocketCanBus, ensure_interface_up
from can_link import address_claim
from can_link.address_table import AddressTable
from can_link.command_sequencer import CommandAttempt
from can_link.device_id import decode_device_id, stable_key
from can_link.device_status import UnknownDeviceTypeError, decode_status
from can_link.frame import CanFrame, ExtendedId, StandardId, decode_id
from can_link.types import MessageType, StableKey, function_name_label
from dbus_bridge.backoff import RestartBackoff
from dbus_bridge.command_gate import CommandGateResult, evaluate_command_request
from dbus_bridge.command_mapping import command_frame_for_switch_write
from dbus_bridge.config_manager import ConfigManager, DiscoveryLog
from dbus_bridge.device_mapping import assign_device_instance, fluid_type_for
from dbus_bridge.motor_status_service import MotorStatusService
from dbus_bridge.routing import DeviceIdAction, route_device_id, status_update_method_for
from dbus_bridge.switch_service import SwitchService
from dbus_bridge.tank_service import TankService

_LOGGER = logging.getLogger(__name__)

COMMAND_TIMEOUT_SWEEP_MS = 500
BRIDGE_ANNOUNCE_INTERVAL_MS = 1000
INTERFACE_RECOVERY_RETRY_SEC = 15.0


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

        self._identity_tail = self.config_manager.get_or_create_bridge_identity_tail()
        self._active_tracker = address_claim.ActiveAddressTracker()
        self._claimer = address_claim.AddressClaimer(identity_tail=self._identity_tail)
        self._bridge_address: int | None = None
        self._pending_commands: dict[str, CommandAttempt] = {}
        self._queued_commands: dict[str, tuple[str, bool, int | None]] = {}

        self.bus: SocketCanBus | None = None
        self._can_interface: str | None = None
        self._interface_down = False
        self._next_interface_recovery_attempt = 0.0
        self._glib_source_ids: list[int] = []
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

    def _save_group(self, key: StableKey, new_group: str) -> None:
        try:
            self.config_manager.set_device_group(key, new_group)
        except KeyError:
            _LOGGER.warning("Cannot save group for %s: not in config", key.to_config_string())

    def _add_glib_source(self, source_id: int) -> int:
        """Every GLib.timeout_add()/io_add_watch() call in this class must
        go through this wrapper. GLib sources attach to the process-wide
        default main context, not to a particular GLib.MainLoop object --
        without tracking and explicitly removing them in _cleanup(), a
        crash-restart cycle (run()'s while loop) would stack a duplicate
        set of periodic timers on top of the previous iteration's on every
        restart, compounding forever: extra CPU, extra log noise, and
        (for _send_bridge_announce/_check_pending_command_timeouts) real
        duplicate CAN bus traffic sent once per accumulated timer."""
        self._glib_source_ids.append(source_id)
        return source_id

    def _send_frame(self, frame: CanFrame) -> bool:
        """Wraps every outbound self.bus.send() call. A transient CAN write
        error (e.g. a momentarily full tx queue) must not crash the whole
        main loop and force a full service restart -- unlike a recv()
        failure (already handled in _on_socket_readable), which does mean
        the interface itself is actually gone. Returns False (logged) on
        failure, never raises.

        A write failure (e.g. the interface went administratively DOWN,
        confirmed to happen after a Venus OS firmware update -- see
        ARCHITECTURE.md) is the only way this ever gets noticed: recv()
        never fires while the interface is down, since no traffic arrives
        to trigger the GLib read watch. Logging is edge-triggered (one
        WARNING when first noticed down, one INFO on recovery) rather than
        per-frame, and recovery attempts (ensure_interface_up(), the same
        idempotent helper SocketCanBus itself uses) are rate-limited to
        once every INTERFACE_RECOVERY_RETRY_SEC -- otherwise a steady
        stream of outbound frames (e.g. the 1Hz bridge self-announce)
        would retry just as often as it logs."""
        if self.bus is None:
            return False
        try:
            self.bus.send(frame)
            if self._interface_down:
                _LOGGER.info("CAN interface %s recovered", self._can_interface)
                self._interface_down = False
            return True
        except OSError as e:
            now = time.time()
            if now < self._next_interface_recovery_attempt:
                _LOGGER.debug("CAN bus write error, dropping this frame: %s", e)
                return False

            self._interface_down = True
            self._next_interface_recovery_attempt = now + INTERFACE_RECOVERY_RETRY_SEC
            _LOGGER.warning(
                "CAN bus write error (%s) -- attempting to bring %s back up", e, self._can_interface
            )
            try:
                ensure_interface_up(self._can_interface)
            except OSError as recovery_error:
                _LOGGER.warning(
                    "Could not bring %s back up, will retry in %.0fs: %s",
                    self._can_interface, INTERFACE_RECOVERY_RETRY_SEC, recovery_error,
                )
            return False

    def _handle_frame(self, can_id: int, is_extended: bool, data: bytes, now: float) -> None:
        outage_detected = self.address_table.note_bus_activity(now)
        if outage_detected:
            self._abort_all_pending_commands("bus outage detected")

        if not is_extended and can_id == address_claim.CLAIM_FRAME_CAN_ID:
            self._handle_claim_frame(data, now)
            return

        decoded_id = decode_id(can_id, is_extended)
        if decoded_id.source_address == self._bridge_address:
            # SocketCanBus doesn't loop back our own transmitted frames (no
            # CAN_RAW_RECV_OWN_MSGS), so this can only be some other device
            # colliding with our claimed address -- ignored as defense in
            # depth rather than routed as if it were a real device's frame.
            return

        self._active_tracker.note_address(decoded_id.source_address, now)
        if self._claimer.state == address_claim.ClaimState.AWAITING_WINDOW:
            self._claimer.note_frame_seen(decoded_id.source_address)

        if isinstance(decoded_id, ExtendedId):
            self._handle_extended_frame(decoded_id, data, now)
            return

        assert isinstance(decoded_id, StandardId)
        if decoded_id.message_type == MessageType.DEVICE_ID:
            self._handle_device_id(decoded_id.source_address, data, now)
        elif decoded_id.message_type == MessageType.DEVICE_STATUS:
            self._handle_device_status(decoded_id.source_address, data, now)

    def _handle_claim_frame(self, data: bytes, now: float) -> None:
        try:
            claim = address_claim.decode_claim_frame(data)
        except ValueError:
            return
        self._active_tracker.note_address(claim.candidate_address, now)
        if self._claimer.state == address_claim.ClaimState.AWAITING_WINDOW:
            self._claimer.note_frame_seen(claim.candidate_address)

    def _handle_extended_frame(self, decoded: ExtendedId, data: bytes, now: float) -> None:
        """Phase 2 discarded all point-to-point traffic. Phase 3 narrows
        that to: act only on a RESPONSE addressed to this bridge, and only
        if it matches an in-flight CommandAttempt for that source device."""
        if decoded.message_type != MessageType.RESPONSE:
            return
        if self._bridge_address is None or decoded.target_address != self._bridge_address:
            return

        key = self._address_to_key.get(decoded.source_address)
        if key is None:
            return
        key_str = key.to_config_string()
        attempt = self._pending_commands.get(key_str)
        if attempt is None or attempt.target_address != decoded.source_address:
            return

        frames = attempt.handle_response(decoded.message_data, data, now)
        for frame in frames:
            self._send_frame(frame)
        if attempt.is_done:
            self._finalize_command_attempt(key_str, attempt)

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
                    on_command=self._on_switch_command,
                    initial_group=self.config_manager.get_device_group(key), on_group_change=self._save_group,
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

    def _on_switch_command(self, key: StableKey, desired_on: bool, desired_brightness_pct: int | None) -> None:
        """Called synchronously from a SwitchService D-Bus write callback
        (GLib main loop thread) -- must return quickly. Runs the cheap gate
        check and either sends the first handshake frame or, if a command
        for this device is already in flight (e.g. rapid writes from
        dragging a brightness slider -- a real handshake takes longer than
        the GUI's write cadence), queues this as the latest desired state
        so it's sent the moment the in-flight one finishes. Only the most
        recent queued write survives; earlier ones are superseded, never
        individually sent -- this is deliberate coalescing, not a queue.
        Never raises -- a refusal is logged, not an exception the D-Bus
        layer would have to handle."""
        now = time.time()
        key_str = key.to_config_string()

        decision = evaluate_command_request(key, self.config_manager, self.address_table, now)
        if decision.result != CommandGateResult.OK:
            _LOGGER.warning("Refusing command for %s: %s", key_str, decision.result.value)
            return
        if self._bridge_address is None:
            _LOGGER.warning("Refusing command for %s: bridge has no claimed CAN address yet", key_str)
            return

        existing = self._pending_commands.get(key_str)
        if existing is not None and not existing.is_done:
            self._queued_commands[key_str] = (decision.device_class, desired_on, desired_brightness_pct)
            # DEBUG, not INFO: expected and frequent while e.g. dragging a
            # brightness slider -- _finalize_command_attempt()'s
            # completed/did-not-complete logging is the meaningful outcome.
            _LOGGER.debug("Command for %s already in flight -- queuing latest requested state", key_str)
            return

        self._send_command(key, decision.device_class, decision.target_address, desired_on, desired_brightness_pct, now)

    def _send_command(
        self,
        key: StableKey,
        device_class: str,
        target_address: int,
        desired_on: bool,
        desired_brightness_pct: int | None,
        now: float,
    ) -> None:
        key_str = key.to_config_string()
        bridge_address = self._bridge_address

        def build_frame(source_address: int, dest_address: int) -> CanFrame:
            return command_frame_for_switch_write(
                device_class, source_address, dest_address, desired_on, desired_brightness_pct
            )

        attempt = CommandAttempt(
            key=key,
            source_address=bridge_address,
            target_address=target_address,
            build_command_frame=build_frame,
            resolve_for_command=self.address_table.resolve_for_command,
        )
        self._pending_commands[key_str] = attempt
        # DEBUG, not INFO: one per attempt, which during a slider drag can
        # be frequent -- the outcome (_finalize_command_attempt(), below)
        # is what's actually worth seeing by default.
        _LOGGER.debug(
            "Sending command for %s (device_class=%s, desired_on=%s, desired_brightness_pct=%s)",
            key_str, device_class, desired_on, desired_brightness_pct,
        )
        self._send_frame(attempt.start(now))

    def _finalize_command_attempt(self, key_str: str, attempt: CommandAttempt) -> None:
        if attempt.succeeded:
            # DEBUG, not INFO: fires once per successful switch/dimmer
            # action -- routine, and the real confirmation a human cares
            # about is the device's own state updating in the GUI via
            # update_relay()/update_dimmable(), not a log line. Failures
            # stay at WARNING since those are worth seeing by default.
            _LOGGER.debug("Command for %s completed", key_str)
        else:
            _LOGGER.warning("Command for %s did not complete: %s", key_str, attempt.failure_reason)
        self._pending_commands.pop(key_str, None)

        queued = self._queued_commands.pop(key_str, None)
        if queued is None:
            return
        _, desired_on, desired_brightness_pct = queued
        now = time.time()
        decision = evaluate_command_request(attempt.key, self.config_manager, self.address_table, now)
        if decision.result != CommandGateResult.OK:
            _LOGGER.warning("Refusing queued command for %s: %s", key_str, decision.result.value)
            return
        self._send_command(
            attempt.key, decision.device_class, decision.target_address, desired_on, desired_brightness_pct, now
        )

    def _abort_all_pending_commands(self, reason: str) -> None:
        for key_str, attempt in list(self._pending_commands.items()):
            attempt.abort(reason)
            _LOGGER.warning("Aborted in-flight command for %s: %s", key_str, reason)
        self._pending_commands.clear()
        self._queued_commands.clear()

    def _check_pending_command_timeouts(self) -> bool:
        now = time.time()
        for key_str, attempt in list(self._pending_commands.items()):
            if attempt.check_timeout(now):
                attempt.abort("timed out waiting for a response")
                self._finalize_command_attempt(key_str, attempt)
        return True

    def _start_address_claim(self) -> None:
        excluded = self._active_tracker.active_addresses(time.time())
        frame = self._claimer.begin_attempt(excluded)
        self._send_frame(frame)
        self._add_glib_source(
            GLib.timeout_add(int(address_claim.ADDRESS_CLAIM_WINDOW_SEC * 1000), self._finish_address_claim_attempt)
        )

    def _finish_address_claim_attempt(self) -> bool:
        claimed = self._claimer.resolve()
        if claimed:
            self._on_address_claimed(self._claimer.claimed_address)
        elif self._claimer.should_back_off:
            _LOGGER.warning(
                "CAN address claim contended repeatedly, backing off %.0fs before retrying",
                self._claimer.backoff_sec,
            )
            self._add_glib_source(
                GLib.timeout_add(int(self._claimer.backoff_sec * 1000), self._retry_address_claim_after_backoff)
            )
        else:
            self._start_address_claim()
        return False

    def _retry_address_claim_after_backoff(self) -> bool:
        self._start_address_claim()
        return False

    def _on_address_claimed(self, claimed_address: int) -> None:
        self._bridge_address = claimed_address
        _LOGGER.info("Bridge claimed CAN address 0x%02X", claimed_address)
        self._send_frame(address_claim.encode_bridge_device_id_frame(claimed_address))
        self._send_frame(address_claim.encode_network_announce(claimed_address, self._identity_tail))
        self._add_glib_source(GLib.timeout_add(BRIDGE_ANNOUNCE_INTERVAL_MS, self._send_bridge_announce))

    def _send_bridge_announce(self) -> bool:
        if self._bridge_address is None:
            return False
        self._send_frame(address_claim.encode_network_announce(self._bridge_address, self._identity_tail))
        self._send_frame(address_claim.encode_bridge_device_id_frame(self._bridge_address))
        return True

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
                self._can_interface = interface
                self._interface_down = False
                self._next_interface_recovery_attempt = 0.0
                self.bus = SocketCanBus(interface)
                _LOGGER.info("Listening on %s", interface)

                self.mainloop = GLib.MainLoop()
                self._add_glib_source(GLib.io_add_watch(self.bus.fileno(), GLib.IO_IN, self._on_socket_readable))
                self._add_glib_source(GLib.timeout_add(30000, self._check_stale_services))
                self._add_glib_source(GLib.timeout_add(COMMAND_TIMEOUT_SWEEP_MS, self._check_pending_command_timeouts))
                self._start_address_claim()

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

        for source_id in self._glib_source_ids:
            try:
                GLib.source_remove(source_id)
            except Exception:
                pass  # already fired/self-removed (a one-shot claim/backoff timer) -- not an error
        self._glib_source_ids.clear()

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

        self._abort_all_pending_commands("service restarting")
        self._bridge_address = None
        self._claimer = address_claim.AddressClaimer(identity_tail=self._identity_tail)
        self._active_tracker = address_claim.ActiveAddressTracker()

        self.mainloop = None


def main() -> None:
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/data/venus-onecontrol-can/config.json")
    Publisher(config_path).run()


if __name__ == "__main__":
    main()
