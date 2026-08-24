import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from can_link.types import DeviceType, StableKey
from dbus_bridge.device_mapping import (
    INSTANCE_BASE,
    INSTANCE_RANGE,
    OutputFunction,
    OutputType,
    assign_device_instance,
    build_addable_list,
    fluid_type_for,
    infer_device_class,
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

    def test_dimmable_light_not_dimming_capable_gets_toggle_output_type(self):
        # PID 161 (SIMULATE_ON_OFF_STYLE_LIGHT) confirmed the device behaves
        # as a plain on/off switch -- see ARCHITECTURE.md's "PID 161 Live
        # Read" note. device_class stays "dimmable_light" (protocol-correct
        # for command building); only the D-Bus presentation changes.
        self.assertEqual(output_type_for("dimmable_light", dimming_capable=False), OutputType.TOGGLE)

    def test_relay_classes_ignore_dimming_capable(self):
        # dimming_capable is only ever consulted for "dimmable_light".
        for device_class in ("relay_light", "relay_pump", "relay_water_heater"):
            self.assertEqual(output_type_for(device_class, dimming_capable=False), OutputType.TOGGLE)

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


class AssignDeviceInstanceTests(unittest.TestCase):
    # Regression coverage for a real bug found on real hardware: two tank
    # services (Grey Tank 2 and Fresh Tank) both got device_instance=86,
    # because stable_id_for() alone is evenly distributed but not
    # collision-free -- a collision occurred with as few as 4 devices.

    def test_persisted_value_always_wins(self):
        key = StableKey("function_name", 67, 0)
        # Even with a colliding already_assigned map, a persisted value must
        # be returned unchanged -- it must never move once assigned.
        result = assign_device_instance("tank", key, already_assigned={"x": 12345}, persisted=99)
        self.assertEqual(result, 99)

    def test_no_collision_uses_natural_candidate(self):
        key = StableKey("function_name", 67, 0)
        expected = INSTANCE_BASE["tank"] + stable_id_for(key, modulo=INSTANCE_RANGE)
        result = assign_device_instance("tank", key, already_assigned={}, persisted=None)
        self.assertEqual(result, expected)

    def test_collision_probes_forward_to_a_free_slot(self):
        key = StableKey("function_name", 67, 0)
        natural = INSTANCE_BASE["tank"] + stable_id_for(key, modulo=INSTANCE_RANGE)
        # Simulate another device already occupying this key's natural slot.
        already_assigned = {"other_device": natural}
        result = assign_device_instance("tank", key, already_assigned, persisted=None)
        self.assertNotEqual(result, natural)
        self.assertNotIn(result, already_assigned.values())

    def test_reproduces_the_real_world_collision_and_resolves_it(self):
        # The exact two real stable keys that collided in production
        # (Grey Tank 1 and Black Tank, both landing on instance=86).
        grey_1 = StableKey("function_name", 68, 1)
        black = StableKey("function_name", 69, 0)
        self.assertEqual(
            INSTANCE_BASE["tank"] + stable_id_for(grey_1, modulo=INSTANCE_RANGE),
            INSTANCE_BASE["tank"] + stable_id_for(black, modulo=INSTANCE_RANGE),
            "test fixture assumption broken: these two keys no longer collide naturally",
        )
        first = assign_device_instance("tank", grey_1, already_assigned={}, persisted=None)
        second = assign_device_instance(
            "tank", black, already_assigned={grey_1.to_config_string(): first}, persisted=None
        )
        self.assertNotEqual(first, second)

    def test_wraps_around_the_range_to_find_a_gap(self):
        key = StableKey("function_name", 1, 0)
        base = INSTANCE_BASE["tank"]
        natural = base + stable_id_for(key, modulo=INSTANCE_RANGE)
        # Occupy every slot except one, forcing a wrap past the range end.
        gap = base  # leave the very first slot free
        used = {
            f"filler_{i}": (base + i)
            for i in range(INSTANCE_RANGE)
            if (base + i) != gap
        }
        result = assign_device_instance("tank", key, used, persisted=None)
        self.assertEqual(result, gap)

    def test_raises_when_range_is_completely_full(self):
        key = StableKey("function_name", 1, 0)
        base = INSTANCE_BASE["tank"]
        used = {f"filler_{i}": base + i for i in range(INSTANCE_RANGE)}
        with self.assertRaises(RuntimeError):
            assign_device_instance("tank", key, used, persisted=None)

    def test_different_kinds_use_different_ranges(self):
        key = StableKey("function_name", 5, 0)
        tank_instance = assign_device_instance("tank", key, {}, None)
        switch_instance = assign_device_instance("switch", key, {}, None)
        self.assertGreaterEqual(tank_instance, INSTANCE_BASE["tank"])
        self.assertLess(tank_instance, INSTANCE_BASE["tank"] + INSTANCE_RANGE)
        self.assertGreaterEqual(switch_instance, INSTANCE_BASE["switch"])
        self.assertLess(switch_instance, INSTANCE_BASE["switch"] + INSTANCE_RANGE)


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


class InferDeviceClassTests(unittest.TestCase):
    def test_tank_sensor(self):
        key = StableKey("function_name", 67, 0)
        self.assertEqual(infer_device_class(key, "TANK_SENSOR"), "tank")

    def test_dimmable_light(self):
        key = StableKey("function_name", 38, 0)
        self.assertEqual(infer_device_class(key, "DIMMABLE_LIGHT"), "dimmable_light")

    def test_all_motor_types_are_unsupported(self):
        # Motor status support was removed 2026-08-24 (see ARCHITECTURE.md's
        # "Motor Status Support -- Removed" note) -- a motor DeviceType is
        # now unsupported the same as any other unrecognized one, not
        # mapped to a "motor_status" device_class anymore.
        key = StableKey("function_name", 105, 1)
        for label in (
            "LATCHING_H_BRIDGE",
            "MOMENTARY_H_BRIDGE",
            "LATCHING_H_BRIDGE_TYPE_2",
            "MOMENTARY_H_BRIDGE_TYPE_2",
        ):
            with self.subTest(label=label):
                self.assertIsNone(infer_device_class(key, label))

    def test_water_pump_relay(self):
        key = StableKey("function_name", 5, 0)  # Water Pump
        self.assertEqual(infer_device_class(key, "LATCHING_RELAY_TYPE_2"), "relay_pump")

    def test_fuel_pump_relay(self):
        key = StableKey("function_name", 191, 0)  # Fuel Pump
        self.assertEqual(infer_device_class(key, "LATCHING_RELAY_TYPE_2"), "relay_pump")

    def test_gas_water_heater_relay(self):
        key = StableKey("function_name", 3, 0)
        self.assertEqual(infer_device_class(key, "LATCHING_RELAY_TYPE_2"), "relay_water_heater")

    def test_electric_water_heater_relay(self):
        key = StableKey("function_name", 4, 0)
        self.assertEqual(infer_device_class(key, "LATCHING_RELAY_TYPE_2"), "relay_water_heater")

    def test_tank_heater_relay_is_not_a_water_heater(self):
        # "Tank Heater" (270) is a freeze-protection relay on a tank, not a
        # domestic water heater -- must fall back to relay_light, not be
        # confused with relay_water_heater.
        key = StableKey("function_name", 270, 0)
        self.assertEqual(infer_device_class(key, "LATCHING_RELAY_TYPE_2"), "relay_light")

    def test_generic_relay_defaults_to_relay_light(self):
        key = StableKey("function_name", 122, 0)  # Scare Light
        self.assertEqual(infer_device_class(key, "LATCHING_RELAY_TYPE_2"), "relay_light")

    def test_all_relay_device_types_are_inferable(self):
        key = StableKey("function_name", 122, 0)
        for label in ("LATCHING_RELAY", "MOMENTARY_RELAY", "LATCHING_RELAY_TYPE_2", "MOMENTARY_RELAY_TYPE_2"):
            with self.subTest(label=label):
                self.assertIsNotNone(infer_device_class(key, label))

    def test_unrecognized_raw_label_returns_none(self):
        key = StableKey("function_name", 353, 0)
        self.assertIsNone(infer_device_class(key, "RAW_44"))

    def test_device_type_unknown_returns_none(self):
        key = StableKey("product_id", 232, 42)
        self.assertIsNone(infer_device_class(key, "UNKNOWN"))

    def test_unsupported_but_recognized_device_types_return_none(self):
        key = StableKey("function_name", 95, 0)
        for label in ("GENERATOR_GENIE", "CHASSIS_INFO", "BLUETOOTH_GATEWAY", "HOUR_METER"):
            with self.subTest(label=label):
                self.assertIsNone(infer_device_class(key, label))

    def test_pump_function_name_only_applies_to_function_name_keys(self):
        # A product_id-fallback key sharing the numeric value 5 by
        # coincidence must not accidentally match the pump heuristic --
        # the heuristic only applies to kind="function_name" keys.
        key = StableKey("product_id", 5, 0)
        self.assertEqual(infer_device_class(key, "LATCHING_RELAY_TYPE_2"), "relay_light")


class BuildAddableListTests(unittest.TestCase):
    def test_excludes_already_configured_devices(self):
        discovered = {"function_name=67,function_instance=0": {"device_type": "TANK_SENSOR", "function_name": "Fresh Tank"}}
        result = build_addable_list(discovered, already_configured={"function_name=67,function_instance=0"})
        self.assertEqual(result, [])

    def test_excludes_product_id_fallback_keys(self):
        # These are the known-empty/unconfigured Unity module ports -- see
        # ARCHITECTURE.md. Never offer them, even if DEVICE_TYPE is
        # otherwise a supported one.
        discovered = {"product_id=232,instance=42": {"device_type": "LATCHING_RELAY_TYPE_2", "function_name": "UNKNOWN"}}
        result = build_addable_list(discovered, already_configured=set())
        self.assertEqual(result, [])

    def test_excludes_unsupported_device_types(self):
        discovered = {"function_name=353,function_instance=0": {"device_type": "RAW_44", "function_name": "LoCAP Gateway"}}
        result = build_addable_list(discovered, already_configured=set())
        self.assertEqual(result, [])

    def test_includes_a_valid_tank(self):
        discovered = {"function_name=67,function_instance=0": {"device_type": "TANK_SENSOR", "function_name": "Fresh Tank"}}
        result = build_addable_list(discovered, already_configured=set())
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].device_class, "tank")
        self.assertEqual(result[0].suggested_friendly_name, "Fresh Tank")

    def test_appends_instance_number_when_nonzero(self):
        # Matches Lippert's own app display convention (f"{name} {instance}").
        discovered = {"function_name=49,function_instance=1": {"device_type": "DIMMABLE_LIGHT", "function_name": "Awning Light"}}
        result = build_addable_list(discovered, already_configured=set())
        self.assertEqual(result[0].suggested_friendly_name, "Awning Light 1")

    def test_real_discovery_log_from_actual_hardware(self):
        # Verbatim discovered_devices.json from the 2026-08-20 deployment.
        discovered = {
            "function_name=105,function_instance=1": {"device_type": "MOMENTARY_H_BRIDGE_TYPE_2", "function_name": "Awning"},
            "function_name=105,function_instance=2": {"device_type": "MOMENTARY_H_BRIDGE_TYPE_2", "function_name": "Awning"},
            "function_name=122,function_instance=0": {"device_type": "LATCHING_RELAY_TYPE_2", "function_name": "Scare Light"},
            "function_name=270,function_instance=0": {"device_type": "LATCHING_RELAY_TYPE_2", "function_name": "Tank Heater"},
            "function_name=293,function_instance=0": {"device_type": "LATCHING_RELAY_TYPE_2", "function_name": "Underbody Accent Light"},
            "function_name=34,function_instance=0": {"device_type": "DIMMABLE_LIGHT", "function_name": "Kitchen Pendants Light"},
            "function_name=353,function_instance=0": {"device_type": "RAW_44", "function_name": "LoCAP Gateway"},
            "function_name=38,function_instance=0": {"device_type": "DIMMABLE_LIGHT", "function_name": "Kitchen Island Light"},
            "function_name=49,function_instance=1": {"device_type": "DIMMABLE_LIGHT", "function_name": "Awning Light"},
            "function_name=49,function_instance=2": {"device_type": "DIMMABLE_LIGHT", "function_name": "Awning Light"},
            "function_name=96,function_instance=1": {"device_type": "MOMENTARY_H_BRIDGE_TYPE_2", "function_name": "Slide"},
            "function_name=96,function_instance=2": {"device_type": "MOMENTARY_H_BRIDGE_TYPE_2", "function_name": "Slide"},
            "product_id=184,instance=1": {"device_type": "BLUETOOTH_GATEWAY", "function_name": "UNKNOWN"},
            "product_id=185,instance=249": {"device_type": "RAW_43", "function_name": "UNKNOWN"},
            "product_id=232,instance=42": {"device_type": "LATCHING_RELAY_TYPE_2", "function_name": "UNKNOWN"},
        }
        result = build_addable_list(discovered, already_configured=set())
        by_key = {d.stable_key.to_config_string(): d for d in result}

        # 8 excluded (3 product_id-fallback + 1 unsupported RAW type + 4
        # motor devices -- 2 Awnings + 2 Slides, all MOMENTARY_H_BRIDGE_TYPE_2,
        # unsupported since motor status support was removed 2026-08-24),
        # 7 remain.
        self.assertEqual(len(result), 7)
        self.assertNotIn("product_id=184,instance=1", by_key)
        self.assertNotIn("product_id=185,instance=249", by_key)
        self.assertNotIn("product_id=232,instance=42", by_key)
        self.assertNotIn("function_name=353,function_instance=0", by_key)
        self.assertNotIn("function_name=105,function_instance=1", by_key)
        self.assertNotIn("function_name=105,function_instance=2", by_key)
        self.assertNotIn("function_name=96,function_instance=1", by_key)
        self.assertNotIn("function_name=96,function_instance=2", by_key)

        self.assertEqual(by_key["function_name=270,function_instance=0"].device_class, "relay_light")
        self.assertEqual(by_key["function_name=38,function_instance=0"].device_class, "dimmable_light")


if __name__ == "__main__":
    unittest.main()
