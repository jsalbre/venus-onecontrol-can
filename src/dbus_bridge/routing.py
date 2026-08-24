"""Pure decision logic for what publisher.py should do with a decoded
DEVICE_ID or DEVICE_STATUS frame. No dbus/gi imports -- testable without
D-Bus, unlike publisher.py itself (which imports dbus/gi at module level
for the GLib main loop).

This module makes the decision; publisher.py is responsible only for
acting on it (creating services, writing the discovery log). Keeping the
decision here means the exposure safety gates (config-gated + device-class
cross-check) can be exercised by tests even though nothing else about the
D-Bus glue can run on a non-Linux dev machine.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from can_link.device_id import DeviceIdentity
from can_link.types import StableKey
from dbus_bridge.config_manager import ConfigManager
from dbus_bridge.device_mapping import service_kind_for, validate_device_class


class DeviceIdAction(Enum):
    NOT_EXPOSED = "not_exposed"
    ALREADY_CREATED = "already_created"
    MISSING_DEVICE_CLASS = "missing_device_class"
    CLASS_MISMATCH = "class_mismatch"
    CREATE_SERVICE = "create_service"


@dataclass(frozen=True)
class DeviceIdRouting:
    action: DeviceIdAction
    device_class: str | None = None
    service_kind: str | None = None


def route_device_id(
    key: StableKey,
    identity: DeviceIdentity,
    config_manager: ConfigManager,
    already_created: bool,
) -> DeviceIdRouting:
    """Decides what to do with a freshly-decoded DEVICE_ID broadcast.
    Fails closed at every step: any missing/mismatched config data results
    in no service being created, never a best-effort guess."""
    if not config_manager.is_exposed(key):
        return DeviceIdRouting(DeviceIdAction.NOT_EXPOSED)

    if already_created:
        return DeviceIdRouting(DeviceIdAction.ALREADY_CREATED)

    device_class = config_manager.get_device_class(key)
    if device_class is None:
        return DeviceIdRouting(DeviceIdAction.MISSING_DEVICE_CLASS)

    if not validate_device_class(device_class, identity.device_type):
        return DeviceIdRouting(DeviceIdAction.CLASS_MISMATCH, device_class=device_class)

    return DeviceIdRouting(
        DeviceIdAction.CREATE_SERVICE,
        device_class=device_class,
        service_kind=service_kind_for(device_class),
    )


def status_update_method_for(service_kind: str, device_class: str) -> str:
    """Which update method publisher.py should call on the service for a
    decoded DEVICE_STATUS, given the service's kind/device_class."""
    if service_kind == "tank":
        return "update"
    if service_kind == "switch":
        return "update_dimmable" if device_class == "dimmable_light" else "update_relay"
    raise ValueError(f"unhandled service_kind: {service_kind!r}")
