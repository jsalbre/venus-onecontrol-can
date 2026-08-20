"""Thread-safe, atomic-write config manager for the devices[] allowlist.

This is the core safety gate for the whole project: `is_exposed()` is the
single source of truth for whether a discovered device is allowed to get a
D-Bus service at all. A device present on the bus but absent from config
(or present with expose=false) must never be published -- see
ARCHITECTURE.md's config-gated-exposure requirement.

No dbus/gi imports -- pure, testable without D-Bus.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from can_link.types import StableKey

_LOGGER = logging.getLogger(__name__)

DEFAULT_CONFIG: dict[str, Any] = {
    "can_interface": "vecan1",
    "pid_poll_interval_sec": 30,
    "address_expiry_sec": 8,
    "bus_outage_threshold_sec": 12,
    "stale_threshold_sec": 300,
    "restart_min_delay_sec": 30,
    "restart_max_delay_sec": 300,
    "log_level": "INFO",
    "devices": [],
}

VALID_DEVICE_CLASSES = frozenset(
    {"tank", "relay_light", "dimmable_light", "relay_pump", "relay_water_heater", "motor_status"}
)


class ConfigManager:
    """Thread-safe, atomic-write JSON config manager (fcntl locking, adapted
    from the pattern in govee-ble-venus-py/src/config_manager.py)."""

    def __init__(self, config_path: Path) -> None:
        self.config_path = Path(config_path)
        self.lock_path = self.config_path.with_suffix(".lock")
        self.config_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _lock(self):
        lock_fd = None
        try:
            lock_fd = open(self.lock_path, "w")
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
            yield lock_fd
        finally:
            if lock_fd:
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
                lock_fd.close()

    def read(self) -> dict:
        with self._lock():
            return self._read_unlocked()

    def _read_unlocked(self) -> dict:
        if not self.config_path.exists():
            return json.loads(json.dumps(DEFAULT_CONFIG))
        try:
            with open(self.config_path) as f:
                config = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            _LOGGER.error("Config file unreadable (%s), using defaults", e)
            return json.loads(json.dumps(DEFAULT_CONFIG))

        for key, value in DEFAULT_CONFIG.items():
            if key not in config:
                config[key] = value
        return config

    def _atomic_write(self, config: dict) -> None:
        temp_fd, temp_path = tempfile.mkstemp(
            dir=self.config_path.parent, prefix=".config_", suffix=".tmp"
        )
        try:
            with os.fdopen(temp_fd, "w") as f:
                json.dump(config, f, indent=2, sort_keys=True)
                f.write("\n")
            os.replace(temp_path, self.config_path)
        except Exception:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise

    def get_setting(self, key: str, default: Any = None) -> Any:
        return self.read().get(key, default)

    def get_devices(self) -> list[dict]:
        return self.read().get("devices", [])

    def find_device(self, stable_key: StableKey) -> dict | None:
        target = stable_key.to_config_string()
        for device in self.get_devices():
            if device.get("stable_key") == target:
                return device
        return None

    def is_exposed(self, stable_key: StableKey) -> bool:
        """The core safety gate: True only if this stable_key has an entry
        in config with expose=true. Never infer this from bus traffic."""
        device = self.find_device(stable_key)
        return bool(device is not None and device.get("expose") is True)

    def get_device_class(self, stable_key: StableKey) -> str | None:
        device = self.find_device(stable_key)
        return device.get("device_class") if device else None

    def get_friendly_name(self, stable_key: StableKey) -> str | None:
        device = self.find_device(stable_key)
        return device.get("friendly_name") if device else None

    def add_device(
        self,
        stable_key: StableKey,
        friendly_name: str,
        device_class: str,
        expose: bool = False,
    ) -> None:
        """Idempotent: updates the existing entry if stable_key is already
        present. expose defaults to False -- adding a device must never
        implicitly enable it."""
        if device_class not in VALID_DEVICE_CLASSES:
            raise ValueError(f"invalid device_class: {device_class!r}")

        target = stable_key.to_config_string()
        with self._lock():
            config = self._read_unlocked()
            devices = config.get("devices", [])

            for device in devices:
                if device.get("stable_key") == target:
                    device["friendly_name"] = friendly_name
                    device["device_class"] = device_class
                    device["expose"] = expose
                    break
            else:
                devices.append(
                    {
                        "stable_key": target,
                        "friendly_name": friendly_name,
                        "device_class": device_class,
                        "expose": expose,
                    }
                )

            config["devices"] = devices
            self._atomic_write(config)

    def remove_device(self, stable_key: StableKey) -> None:
        target = stable_key.to_config_string()
        with self._lock():
            config = self._read_unlocked()
            devices = config.get("devices", [])
            new_devices = [d for d in devices if d.get("stable_key") != target]
            if len(new_devices) < len(devices):
                config["devices"] = new_devices
                self._atomic_write(config)

    def set_expose(self, stable_key: StableKey, expose: bool) -> None:
        """The explicit enable/disable operation a CLI tool would call --
        this and add_device() are the only ways a device becomes exposed."""
        target = stable_key.to_config_string()
        with self._lock():
            config = self._read_unlocked()
            devices = config.get("devices", [])
            for device in devices:
                if device.get("stable_key") == target:
                    device["expose"] = expose
                    config["devices"] = devices
                    self._atomic_write(config)
                    return
            raise KeyError(f"stable_key not found in config: {target}")

    def update_friendly_name(self, stable_key: StableKey, friendly_name: str) -> None:
        target = stable_key.to_config_string()
        with self._lock():
            config = self._read_unlocked()
            devices = config.get("devices", [])
            for device in devices:
                if device.get("stable_key") == target:
                    device["friendly_name"] = friendly_name
                    config["devices"] = devices
                    self._atomic_write(config)
                    return
            raise KeyError(f"stable_key not found in config: {target}")


class DiscoveryLog:
    """Records stable_keys seen on the bus but not present in config, purely
    for the user's review (e.g. via a future CLI tool). Never causes
    exposure by itself -- exposure only ever comes from ConfigManager.
    Best-effort, not locked/atomic -- informational only, single writer."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            with open(self.path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def _write(self, data: dict) -> None:
        try:
            with open(self.path, "w") as f:
                json.dump(data, f, indent=2, sort_keys=True)
        except OSError as e:
            _LOGGER.warning("Could not write discovery log: %s", e)

    def record(self, stable_key: StableKey, device_type_label: str, function_name_label: str) -> None:
        data = self._read()
        key_str = stable_key.to_config_string()
        if key_str not in data:
            data[key_str] = {
                "device_type": device_type_label,
                "function_name": function_name_label,
            }
            self._write(data)

    def prune_configured(self, configured_keys: set[str]) -> None:
        data = self._read()
        pruned = {k: v for k, v in data.items() if k not in configured_keys}
        if len(pruned) != len(data):
            self._write(pruned)

    def entries(self) -> dict:
        return self._read()
