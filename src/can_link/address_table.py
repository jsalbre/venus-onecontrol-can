"""Stable-key <-> live CAN SourceAddress resolution, with bus-outage safety
gating for command targets. See ARCHITECTURE.md's "Stable-Key Device
Discovery" and "Bus-Outage Safety Gate" sections for the rationale -- this
module is the safety-critical core of that design.

CAN SourceAddress is a reclaimable pool assignment, not fixed per physical
device, and can silently change across a OneControl power cycle. This
module never lets a command be sent against a mapping that hasn't been
freshly re-confirmed since the last time the bus went quiet -- including
this service's own restart, since that is indistinguishable from a real
OneControl power-loss/recovery from inside the process.
"""

from __future__ import annotations

from dataclasses import dataclass

from can_link.types import COMMANDABLE_DEVICE_TYPES, DeviceType, StableKey

DEFAULT_ADDRESS_EXPIRY_SEC = 8.0
DEFAULT_BUS_OUTAGE_THRESHOLD_SEC = 12.0


@dataclass
class _Entry:
    source_address: int
    device_type: DeviceType | None
    last_seen: float
    verified: bool


class AddressTable:
    """In-memory only -- never persisted or reloaded across a process
    restart, by design. Every process start begins with nothing verified."""

    def __init__(
        self,
        address_expiry_sec: float = DEFAULT_ADDRESS_EXPIRY_SEC,
        bus_outage_threshold_sec: float = DEFAULT_BUS_OUTAGE_THRESHOLD_SEC,
    ) -> None:
        self._address_expiry_sec = address_expiry_sec
        self._bus_outage_threshold_sec = bus_outage_threshold_sec
        self._entries: dict[StableKey, _Entry] = {}
        self._last_seen_any: float | None = None

    def note_bus_activity(self, now: float) -> bool:
        """Call on every received frame, regardless of type. Returns True
        if the gap since the previous frame exceeded the outage threshold
        -- in which case every existing entry has just been marked
        unverified (it may still be used for passive decoding via
        resolve(), but is no longer command-eligible until it re-announces
        itself)."""
        outage_detected = False
        if (
            self._last_seen_any is not None
            and (now - self._last_seen_any) > self._bus_outage_threshold_sec
        ):
            for entry in self._entries.values():
                entry.verified = False
            outage_detected = True
        self._last_seen_any = now
        return outage_detected

    def observe_device_id(
        self, key: StableKey, source_address: int, device_type: DeviceType | None, now: float
    ) -> None:
        """Call when a DEVICE_ID broadcast is decoded. This is the only way
        an entry becomes (re-)verified."""
        self._entries[key] = _Entry(
            source_address=source_address, device_type=device_type, last_seen=now, verified=True
        )

    def resolve(self, key: StableKey, now: float) -> int | None:
        """Current SourceAddress for passive use (decoding this device's
        DEVICE_STATUS frames), or None if unknown/expired. Does not require
        the entry to be verified -- outages don't block passive display."""
        entry = self._entries.get(key)
        if entry is None or (now - entry.last_seen) > self._address_expiry_sec:
            return None
        return entry.source_address

    def device_type_for(self, key: StableKey, now: float) -> DeviceType | None:
        entry = self._entries.get(key)
        if entry is None or (now - entry.last_seen) > self._address_expiry_sec:
            return None
        return entry.device_type

    def resolve_for_command(self, key: StableKey, now: float) -> int | None:
        """Fail-closed resolution for command targets. Returns the live
        SourceAddress only if ALL hold: the entry exists and hasn't expired,
        it is verified (re-confirmed since the last detected outage or
        service restart), and its current DEVICE_TYPE is in
        COMMANDABLE_DEVICE_TYPES (never a motor, regardless of config).
        Returns None -- never a guess -- otherwise."""
        entry = self._entries.get(key)
        if entry is None:
            return None
        if (now - entry.last_seen) > self._address_expiry_sec:
            return None
        if not entry.verified:
            return None
        if entry.device_type not in COMMANDABLE_DEVICE_TYPES:
            return None
        return entry.source_address
