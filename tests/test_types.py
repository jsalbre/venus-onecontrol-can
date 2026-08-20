import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from can_link.types import DeviceType, StableKey, function_name_label


class StableKeyTests(unittest.TestCase):
    def test_function_name_key_round_trips_through_config_string(self):
        key = StableKey("function_name", 32, 1)
        text = key.to_config_string()
        self.assertEqual(StableKey.from_config_string(text), key)

    def test_product_id_key_round_trips_through_config_string(self):
        key = StableKey("product_id", 1234, 2)
        text = key.to_config_string()
        self.assertEqual(StableKey.from_config_string(text), key)

    def test_rejects_invalid_kind(self):
        with self.assertRaises(ValueError):
            StableKey("bogus", 1, 1)

    def test_rejects_unrecognized_config_string(self):
        with self.assertRaises(ValueError):
            StableKey.from_config_string("foo=1,bar=2")


class FunctionNameLabelTests(unittest.TestCase):
    def test_known_value_returns_label(self):
        self.assertEqual(function_name_label(67), "FRESH_TANK")

    def test_unknown_value_returns_placeholder(self):
        self.assertEqual(function_name_label(9999), "UNKNOWN_9999")


class DeviceTypeTests(unittest.TestCase):
    def test_tank_sensor_value(self):
        self.assertEqual(DeviceType.TANK_SENSOR, 10)

    def test_dimmable_light_value(self):
        self.assertEqual(DeviceType.DIMMABLE_LIGHT, 20)


if __name__ == "__main__":
    unittest.main()
