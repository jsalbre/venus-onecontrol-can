import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from can_link.device_id import decode_device_id, stable_key
from can_link.types import DeviceType, StableKey


class DecodeDeviceIdTests(unittest.TestCase):
    def test_decodes_tank_sensor_identity(self):
        # PRODUCT_ID=0x1234, instance=1, DEVICE_TYPE=TANK_SENSOR(10),
        # FUNCTION_NAME=67 (FRESH_TANK), device_instance=0, function_instance=1,
        # capabilities=0x00
        payload = bytes([0x12, 0x34, 0x01, 10, 0x00, 67, 0x01, 0x00])
        identity = decode_device_id(payload)
        self.assertEqual(identity.product_id, 0x1234)
        self.assertEqual(identity.product_instance, 1)
        self.assertEqual(identity.device_type, DeviceType.TANK_SENSOR)
        self.assertEqual(identity.function_name, 67)
        self.assertEqual(identity.device_instance, 0)
        self.assertEqual(identity.function_instance, 1)
        self.assertEqual(identity.capabilities_raw, 0x00)

    def test_device_instance_and_function_instance_share_one_byte(self):
        # byte6 = 0xA3 -> device_instance=0xA, function_instance=0x3
        payload = bytes([0x00, 0x01, 0x00, 20, 0x00, 0x00, 0xA3, 0x00])
        identity = decode_device_id(payload)
        self.assertEqual(identity.device_instance, 0xA)
        self.assertEqual(identity.function_instance, 0x3)

    def test_unrecognized_device_type_returns_none_but_keeps_raw_value(self):
        payload = bytes([0x00, 0x01, 0x00, 200, 0x00, 0x00, 0x00, 0x00])
        identity = decode_device_id(payload)
        self.assertIsNone(identity.device_type)
        self.assertEqual(identity.device_type_raw, 200)

    def test_rejects_wrong_payload_length(self):
        with self.assertRaises(ValueError):
            decode_device_id(bytes([0x00] * 7))


class StableKeyDerivationTests(unittest.TestCase):
    def test_uses_function_name_when_populated(self):
        payload = bytes([0x12, 0x34, 0x01, 10, 0x00, 67, 0x01, 0x00])
        identity = decode_device_id(payload)
        self.assertEqual(stable_key(identity), StableKey("function_name", 67, 1))

    def test_falls_back_to_product_id_when_function_name_unset(self):
        payload = bytes([0x12, 0x34, 0x05, 10, 0x00, 0x00, 0x01, 0x00])
        identity = decode_device_id(payload)
        self.assertEqual(stable_key(identity), StableKey("product_id", 0x1234, 5))

    def test_chassis_info_keys_by_device_type_not_product_id_fallback(self):
        # DEVICE_TYPE=CHASSIS_INFO(39), FUNCTION_NAME unset, device_instance=0
        # -- must NOT fall back to (PRODUCT_ID, instance), since that's the
        # same ambiguous fallback shared by the unconfigured relay/tank pool.
        payload = bytes([0x00, 0xE8, 0x2A, 39, 0x00, 0x00, 0x00, 0x00])
        identity = decode_device_id(payload)
        self.assertEqual(stable_key(identity), StableKey("device_type", 39, 0))

    def test_other_function_name_unset_device_types_still_fall_back(self):
        # A non-singleton DEVICE_TYPE with FUNCTION_NAME unset (e.g. an
        # unconfigured relay output) must still use the product_id fallback
        # -- confirms the device_type branch is narrowly scoped, not a
        # blanket change to every FUNCTION_NAME=0 device.
        payload = bytes([0x00, 0xE8, 0x2A, 30, 0x00, 0x00, 0x30, 0x00])
        identity = decode_device_id(payload)
        self.assertEqual(stable_key(identity), StableKey("product_id", 0xE8, 0x2A))


if __name__ == "__main__":
    unittest.main()
