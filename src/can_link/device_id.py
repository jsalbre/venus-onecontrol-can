"""DEVICE_ID broadcast payload decoding (MessageType.DEVICE_ID, 11-bit ID
0x200 | source_address, ~1Hz). See dev-notes/ARCHITECTURE.md for the byte
layout this is transcribed from.

Decodes only the 8-byte payload -- the sender's CAN SourceAddress comes from
the frame's StandardId (frame.py), not from this payload, and is combined
with a DeviceIdentity by address_table.py.
"""

from __future__ import annotations

from dataclasses import dataclass

from can_link.types import DeviceType, StableKey

DEVICE_ID_PAYLOAD_LENGTH = 8


@dataclass(frozen=True)
class DeviceIdentity:
    product_id: int
    product_instance: int
    device_type_raw: int
    function_name: int
    device_instance: int
    function_instance: int
    capabilities_raw: int

    @property
    def device_type(self) -> DeviceType | None:
        """The recognized DeviceType, or None if device_type_raw isn't in
        our enum (an unrecognized/undocumented device on the bus)."""
        try:
            return DeviceType(self.device_type_raw)
        except ValueError:
            return None


def decode_device_id(payload: bytes) -> DeviceIdentity:
    if len(payload) != DEVICE_ID_PAYLOAD_LENGTH:
        raise ValueError(
            f"DEVICE_ID payload must be {DEVICE_ID_PAYLOAD_LENGTH} bytes, got {len(payload)}"
        )
    product_id = (payload[0] << 8) | payload[1]
    product_instance = payload[2]
    device_type_raw = payload[3]
    function_name = (payload[4] << 8) | payload[5]
    device_instance = payload[6] >> 4
    function_instance = payload[6] & 0x0F
    capabilities_raw = payload[7]
    return DeviceIdentity(
        product_id=product_id,
        product_instance=product_instance,
        device_type_raw=device_type_raw,
        function_name=function_name,
        device_instance=device_instance,
        function_instance=function_instance,
        capabilities_raw=capabilities_raw,
    )


def stable_key(identity: DeviceIdentity) -> StableKey:
    """The persistent identifier for this device, independent of its
    volatile CAN SourceAddress. Falls back to (PRODUCT_ID, instance) when
    FUNCTION_NAME is unpopulated (0)."""
    if identity.function_name != 0:
        return StableKey("function_name", identity.function_name, identity.function_instance)
    return StableKey("product_id", identity.product_id, identity.product_instance)
