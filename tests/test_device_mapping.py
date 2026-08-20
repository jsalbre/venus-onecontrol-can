import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from can_link.types import DeviceType, StableKey
from dbus_bridge.device_mapping import (
    OutputFunction,
    OutputType,
    fluid_type_for,
    output_function_for,
    output_type_for,
    service_kind_for,
    stable_id_for,
    validate_device_class,
)


class ServiceKindForTests(unittest.TestCase):
    def test_known_device_classes(self):
        self.assertEqual(service_kind_for("tank"), "tank")
        self.assertEqual(service_kind_for("relay_light"), "switch")
        self.assertEqual(service_kind_for("dimmable_light"), "switch")
        self.assertEqual(service_kind_for("relay_pump"), "switch")
        self.assertEqual(service_kind_for("relay_water_heater"), "switch")
        self.assertEqual(service_kind_for("motor_status"), "motor_status")

    def test_unknown_device_class_returns_none(self):
        self.assertIsNone(service_kind_for("bogus"))


class ValidateDeviceClassTests(unittest.TestCase):
    def test_tank_matches_tank_sensor(self):
        self.assertTrue(validate_device_class("tank", DeviceType.TANK_SENSOR))

    def test_tank_rejects_relay_type(self):
        self.assertFalse(validate_device_class("tank", DeviceType.LATCHING_RELAY_TYPE_2))

    def test_relay_light_matches_relay_types(self):
        for dt in (
            DeviceType.LATCHING_RELAY,
            DeviceType.MOMENTARY_RELAY,
            DeviceType.LATCHING_RELAY_TYPE_2,
            DeviceType.MOMENTARY_RELAY_TYPE_2,
        ):
            with self.subTest(device_type=dt):
                self.assertTrue(validate_device_class("relay_light", dt))

    def test_relay_pump_and_water_heater_share_relay_types(self):
        self.assertTrue(validate_device_class("relay_pump", DeviceType.LATCHING_RELAY_TYPE_2))
        self.assertTrue(validate_device_class("relay_water_heater", DeviceType.LATCHING_RELAY_TYPE_2))

    def test_dimmable_light_matches_only_dimmable_light(self):
        self.assertTrue(validate_device_class("dimmable_light", DeviceType.DIMMABLE_LIGHT))
        self.assertFalse(validate_device_class("dimmable_light", DeviceType.LATCHING_RELAY_TYPE_2))

    def test_relay_classes_never_match_motor_types(self):
        # The critical safety property: a config entry claiming a device is
        # a light/pump/water-heater must never validate against a motor's
        # DeviceType, even if the byte-level status struct is identical.
        for device_class in ("relay_light", "relay_pump", "relay_water_heater", "dimmable_light"):
            for motor_type in (
                DeviceType.LATCHING_H_BRIDGE,
                DeviceType.MOMENTARY_H_BRIDGE,
                DeviceType.LATCHING_H_BRIDGE_TYPE_2,
                DeviceType.MOMENTARY_H_BRIDGE_TYPE_2,
            ):
                with self.subTest(device_class=device_class, motor_type=motor_type):
                    self.assertFalse(validate_device_class(device_class, motor_type))

    def test_motor_status_matches_only_motor_types(self):
        self.assertTrue(validate_device_class("motor_status", DeviceType.MOMENTARY_H_BRIDGE_TYPE_2))
        self.assertFalse(validate_device_class("motor_status", DeviceType.LATCHING_RELAY_TYPE_2))

    def test_none_observed_type_never_validates(self):
        self.assertFalse(validate_device_class("tank", None))
        self.assertFalse(validate_device_class("relay_light", None))

    def test_unknown_device_class_never_validates(self):
        self.assertFalse(validate_device_class("bogus", DeviceType.TANK_SENSOR))


class OutputTypeAndFunctionTests(unittest.TestCase):
    def test_dimmable_light_gets_dimmable_output_type(self):
        self.assertEqual(output_type_for("dimmable_light"), OutputType.DIMMABLE)

    def test_relay_classes_get_toggle_output_type(self):
        for device_class in ("relay_light", "relay_pump", "relay_water_heater"):
            self.assertEqual(output_type_for(device_class), OutputType.TOGGLE)

    def test_relay_pump_gets_tank_pump_function(self):
        self.assertEqual(output_function_for("relay_pump"), OutputFunction.TANK_PUMP)

    def test_other_classes_get_manual_function(self):
        for device_class in ("relay_light", "relay_water_heater", "dimmable_light"):
            self.assertEqual(output_function_for(device_class), OutputFunction.MANUAL)


class FluidTypeForTests(unittest.TestCase):
    def test_fresh_tank(self):
        self.assertEqual(fluid_type_for(StableKey("function_name", 67, 0)), 1)

    def test_grey_tank(self):
        self.assertEqual(fluid_type_for(StableKey("function_name", 68, 1)), 2)

    def test_black_tank(self):
        self.assertEqual(fluid_type_for(StableKey("function_name", 69, 0)), 5)

    def test_unknown_function_name_returns_none(self):
        self.assertIsNone(fluid_type_for(StableKey("function_name", 9999, 0)))

    def test_product_id_key_returns_none(self):
        self.assertIsNone(fluid_type_for(StableKey("product_id", 232, 42)))


class StableIdForTests(unittest.TestCase):
    def test_deterministic_across_calls(self):
        # Critical: this must NOT use Python's builtin hash(), which is
        # randomized per-process (PYTHONHASHSEED) and would change the
        # D-Bus service name/instance on every restart.
        key = StableKey("function_name", 67, 0)
        self.assertEqual(stable_id_for(key), stable_id_for(key))

    def test_deterministic_across_subprocesses(self):
        import subprocess

        key_str = "function_name=67,function_instance=0"
        script = (
            "import sys; sys.path.insert(0, 'src'); "
            "from can_link.types import StableKey; "
            "from dbus_bridge.device_mapping import stable_id_for; "
            f"print(stable_id_for(StableKey.from_config_string({key_str!r})))"
        )
        results = set()
        for _ in range(3):
            out = subprocess.run(
                ["python3", "-c", script],
                cwd=os.path.join(os.path.dirname(__file__), ".."),
                capture_output=True,
                text=True,
                env={**os.environ, "PYTHONHASHSEED": "random"},
            )
            results.add(out.stdout.strip())
        self.assertEqual(len(results), 1, f"stable_id_for varied across subprocess runs: {results}")

    def test_different_keys_usually_differ(self):
        a = stable_id_for(StableKey("function_name", 67, 0))
        b = stable_id_for(StableKey("function_name", 68, 0))
        self.assertNotEqual(a, b)


if __name__ == "__main__":
    unittest.main()
