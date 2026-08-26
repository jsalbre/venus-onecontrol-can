import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from can_link.types import StableKey
from dbus_bridge.config_manager import ConfigManager, DiscoveryLog

LIGHT_KEY = StableKey("function_name", 32, 1)
PUMP_KEY = StableKey("function_name", 5, 0)


class ConfigManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.tmpdir.name) / "config.json"
        self.manager = ConfigManager(self.config_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_defaults_when_no_file_exists(self):
        config = self.manager.read()
        self.assertEqual(config["can_interface"], "vecan1")
        self.assertEqual(config["devices"], [])

    def test_unconfigured_device_is_never_exposed(self):
        self.assertFalse(self.manager.is_exposed(LIGHT_KEY))

    def test_add_device_defaults_to_not_exposed(self):
        self.manager.add_device(LIGHT_KEY, "Kitchen Light", "relay_light")
        self.assertFalse(self.manager.is_exposed(LIGHT_KEY))

    def test_add_device_with_explicit_expose_true(self):
        self.manager.add_device(LIGHT_KEY, "Kitchen Light", "relay_light", expose=True)
        self.assertTrue(self.manager.is_exposed(LIGHT_KEY))

    def test_add_device_rejects_invalid_device_class(self):
        with self.assertRaises(ValueError):
            self.manager.add_device(LIGHT_KEY, "Kitchen Light", "not_a_real_class")

    def test_add_device_rejects_motor_status(self):
        # motor_status support was removed 2026-08-24 (see ARCHITECTURE.md's
        # "Motor Status Support -- Removed" note) -- it's no longer a valid
        # device_class, not just an arbitrary unrecognized string.
        with self.assertRaises(ValueError):
            self.manager.add_device(LIGHT_KEY, "Awning", "motor_status")

    def test_add_device_accepts_battery_voltage(self):
        battery_key = StableKey("device_type", 39, 0)
        self.manager.add_device(battery_key, "Battery Voltage", "battery_voltage")
        self.assertEqual(self.manager.get_device_class(battery_key), "battery_voltage")

    def test_add_device_is_idempotent(self):
        self.manager.add_device(LIGHT_KEY, "Kitchen Light", "relay_light", expose=True)
        self.manager.add_device(LIGHT_KEY, "Kitchen Light Renamed", "relay_light", expose=True)
        devices = self.manager.get_devices()
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["friendly_name"], "Kitchen Light Renamed")

    def test_set_expose_toggles_exposure(self):
        self.manager.add_device(LIGHT_KEY, "Kitchen Light", "relay_light", expose=False)
        self.assertFalse(self.manager.is_exposed(LIGHT_KEY))
        self.manager.set_expose(LIGHT_KEY, True)
        self.assertTrue(self.manager.is_exposed(LIGHT_KEY))
        self.manager.set_expose(LIGHT_KEY, False)
        self.assertFalse(self.manager.is_exposed(LIGHT_KEY))

    def test_set_expose_on_unknown_device_raises(self):
        with self.assertRaises(KeyError):
            self.manager.set_expose(LIGHT_KEY, True)

    def test_remove_device(self):
        self.manager.add_device(LIGHT_KEY, "Kitchen Light", "relay_light", expose=True)
        self.manager.remove_device(LIGHT_KEY)
        self.assertFalse(self.manager.is_exposed(LIGHT_KEY))
        self.assertEqual(self.manager.get_devices(), [])

    def test_get_device_class_and_friendly_name(self):
        self.manager.add_device(PUMP_KEY, "Fresh Water Pump", "relay_pump", expose=True)
        self.assertEqual(self.manager.get_device_class(PUMP_KEY), "relay_pump")
        self.assertEqual(self.manager.get_friendly_name(PUMP_KEY), "Fresh Water Pump")

    def test_multiple_devices_are_independent(self):
        self.manager.add_device(LIGHT_KEY, "Kitchen Light", "relay_light", expose=True)
        self.manager.add_device(PUMP_KEY, "Water Pump", "relay_pump", expose=False)
        self.assertTrue(self.manager.is_exposed(LIGHT_KEY))
        self.assertFalse(self.manager.is_exposed(PUMP_KEY))

    def test_corrupt_config_file_falls_back_to_defaults(self):
        self.config_path.write_text("{ not valid json")
        config = self.manager.read()
        self.assertEqual(config["devices"], [])

    def test_update_friendly_name(self):
        self.manager.add_device(LIGHT_KEY, "Kitchen Light", "relay_light", expose=True)
        self.manager.update_friendly_name(LIGHT_KEY, "Dining Room Light")
        self.assertEqual(self.manager.get_friendly_name(LIGHT_KEY), "Dining Room Light")

    def test_group_defaults_to_empty_string(self):
        self.manager.add_device(LIGHT_KEY, "Kitchen Light", "relay_light", expose=True)
        self.assertEqual(self.manager.get_device_group(LIGHT_KEY), "")

    def test_unconfigured_device_group_is_empty_string_not_none(self):
        self.assertEqual(self.manager.get_device_group(LIGHT_KEY), "")

    def test_set_and_get_device_group(self):
        self.manager.add_device(LIGHT_KEY, "Kitchen Light", "relay_light", expose=True)
        self.manager.set_device_group(LIGHT_KEY, "Kitchen")
        self.assertEqual(self.manager.get_device_group(LIGHT_KEY), "Kitchen")

    def test_set_device_group_on_unknown_device_raises(self):
        with self.assertRaises(KeyError):
            self.manager.set_device_group(LIGHT_KEY, "Kitchen")

    def test_group_persists_across_manager_instances(self):
        self.manager.add_device(LIGHT_KEY, "Kitchen Light", "relay_light", expose=True)
        self.manager.set_device_group(LIGHT_KEY, "Kitchen")
        reloaded = ConfigManager(self.config_path)
        self.assertEqual(reloaded.get_device_group(LIGHT_KEY), "Kitchen")

    def test_group_can_be_cleared_back_to_empty(self):
        self.manager.add_device(LIGHT_KEY, "Kitchen Light", "relay_light", expose=True)
        self.manager.set_device_group(LIGHT_KEY, "Kitchen")
        self.manager.set_device_group(LIGHT_KEY, "")
        self.assertEqual(self.manager.get_device_group(LIGHT_KEY), "")

    def test_show_ui_control_defaults_to_always(self):
        self.manager.add_device(LIGHT_KEY, "Kitchen Light", "relay_light", expose=True)
        self.assertEqual(self.manager.get_show_ui_control(LIGHT_KEY), 1)

    def test_unconfigured_device_show_ui_control_defaults_to_always(self):
        self.assertEqual(self.manager.get_show_ui_control(LIGHT_KEY), 1)

    def test_set_and_get_show_ui_control(self):
        self.manager.add_device(LIGHT_KEY, "Kitchen Light", "relay_light", expose=True)
        self.manager.set_show_ui_control(LIGHT_KEY, 2)
        self.assertEqual(self.manager.get_show_ui_control(LIGHT_KEY), 2)

    def test_set_show_ui_control_on_unknown_device_raises(self):
        with self.assertRaises(KeyError):
            self.manager.set_show_ui_control(LIGHT_KEY, 0)

    def test_show_ui_control_persists_across_manager_instances(self):
        self.manager.add_device(LIGHT_KEY, "Kitchen Light", "relay_light", expose=True)
        self.manager.set_show_ui_control(LIGHT_KEY, 4)
        reloaded = ConfigManager(self.config_path)
        self.assertEqual(reloaded.get_show_ui_control(LIGHT_KEY), 4)

    def test_device_instance_absent_by_default(self):
        self.manager.add_device(LIGHT_KEY, "Kitchen Light", "relay_light", expose=True)
        self.assertIsNone(self.manager.get_device_instance(LIGHT_KEY))

    def test_set_and_get_device_instance(self):
        self.manager.add_device(LIGHT_KEY, "Kitchen Light", "relay_light", expose=True)
        self.manager.set_device_instance(LIGHT_KEY, 742)
        self.assertEqual(self.manager.get_device_instance(LIGHT_KEY), 742)

    def test_device_instance_persists_across_manager_instances(self):
        self.manager.add_device(LIGHT_KEY, "Kitchen Light", "relay_light", expose=True)
        self.manager.set_device_instance(LIGHT_KEY, 742)
        reloaded = ConfigManager(self.config_path)
        self.assertEqual(reloaded.get_device_instance(LIGHT_KEY), 742)

    def test_set_device_instance_on_unknown_device_raises(self):
        with self.assertRaises(KeyError):
            self.manager.set_device_instance(LIGHT_KEY, 742)

    def test_get_instances_by_kind_filters_by_service_kind(self):
        tank_key = StableKey("function_name", 67, 0)
        self.manager.add_device(tank_key, "Fresh Tank", "tank", expose=True)
        self.manager.add_device(LIGHT_KEY, "Kitchen Light", "relay_light", expose=True)
        self.manager.set_device_instance(tank_key, 21)
        self.manager.set_device_instance(LIGHT_KEY, 701)

        tank_instances = self.manager.get_instances_by_kind("tank")
        self.assertEqual(tank_instances, {tank_key.to_config_string(): 21})

        switch_instances = self.manager.get_instances_by_kind("switch")
        self.assertEqual(switch_instances, {LIGHT_KEY.to_config_string(): 701})

    def test_get_instances_by_kind_excludes_unassigned_devices(self):
        # A device with no device_instance yet (never created a service)
        # must not show up as "occupying" an instance.
        self.manager.add_device(LIGHT_KEY, "Kitchen Light", "relay_light", expose=True)
        self.assertEqual(self.manager.get_instances_by_kind("switch"), {})

    def test_unconfigured_device_never_has_commands_enabled(self):
        self.assertFalse(self.manager.commands_enabled_for(LIGHT_KEY))

    def test_add_device_defaults_commands_enabled_to_false(self):
        self.manager.add_device(LIGHT_KEY, "Kitchen Light", "relay_light", expose=True)
        self.assertFalse(self.manager.commands_enabled_for(LIGHT_KEY))

    def test_add_device_with_explicit_commands_enabled_true(self):
        self.manager.add_device(LIGHT_KEY, "Kitchen Light", "relay_light", expose=True, commands_enabled=True)
        self.assertTrue(self.manager.commands_enabled_for(LIGHT_KEY))

    def test_set_commands_enabled_toggles(self):
        self.manager.add_device(LIGHT_KEY, "Kitchen Light", "relay_light", expose=True)
        self.assertFalse(self.manager.commands_enabled_for(LIGHT_KEY))
        self.manager.set_commands_enabled(LIGHT_KEY, True)
        self.assertTrue(self.manager.commands_enabled_for(LIGHT_KEY))
        self.manager.set_commands_enabled(LIGHT_KEY, False)
        self.assertFalse(self.manager.commands_enabled_for(LIGHT_KEY))

    def test_set_commands_enabled_on_unknown_device_raises(self):
        with self.assertRaises(KeyError):
            self.manager.set_commands_enabled(LIGHT_KEY, True)

    def test_commands_enabled_independent_of_expose(self):
        self.manager.add_device(LIGHT_KEY, "Kitchen Light", "relay_light", expose=False, commands_enabled=True)
        self.assertFalse(self.manager.is_exposed(LIGHT_KEY))
        self.assertTrue(self.manager.commands_enabled_for(LIGHT_KEY))

    def test_bridge_identity_tail_generated_once_and_persisted(self):
        tail = self.manager.get_or_create_bridge_identity_tail()
        self.assertEqual(len(tail), 7)
        self.assertEqual(self.manager.get_or_create_bridge_identity_tail(), tail)

    def test_bridge_identity_tail_persists_across_manager_instances(self):
        tail = self.manager.get_or_create_bridge_identity_tail()
        reloaded = ConfigManager(self.config_path)
        self.assertEqual(reloaded.get_or_create_bridge_identity_tail(), tail)

    def test_get_devices_result_is_not_shared_mutable_state(self):
        self.manager.add_device(LIGHT_KEY, "Kitchen Light", "relay_light", expose=True)
        devices = self.manager.get_devices()
        devices.append({"stable_key": "bogus"})
        devices[0]["friendly_name"] = "Tampered"
        # A fresh call must be unaffected by mutating the previous result --
        # regression test for ConfigManager._snapshot().
        fresh = self.manager.get_devices()
        self.assertEqual(len(fresh), 1)
        self.assertEqual(fresh[0]["friendly_name"], "Kitchen Light")

    def test_external_process_write_is_visible_without_a_restart(self):
        # Simulates manage-devices/manage-system: a second ConfigManager
        # instance, on the same path, writing while the first instance
        # (standing in for publisher.py) keeps running. This is the
        # concrete regression test for the property this whole caching
        # design is built to preserve -- see ARCHITECTURE.md.
        self.manager.add_device(LIGHT_KEY, "Kitchen Light", "relay_light", expose=False)
        self.assertFalse(self.manager.is_exposed(LIGHT_KEY))

        other_process = ConfigManager(self.config_path)
        other_process.set_expose(LIGHT_KEY, True)

        self.assertTrue(self.manager.is_exposed(LIGHT_KEY))

    def test_read_does_not_reload_when_file_unchanged(self):
        self.manager.add_device(LIGHT_KEY, "Kitchen Light", "relay_light", expose=True)
        with patch.object(
            self.manager, "_read_unlocked", wraps=self.manager._read_unlocked
        ) as wrapped:
            self.manager.read()
            self.manager.read()
            self.manager.is_exposed(LIGHT_KEY)
            self.assertEqual(wrapped.call_count, 0)


class DiscoveryLogTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "discovered.json"
        self.log = DiscoveryLog(self.path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_record_new_key(self):
        self.log.record(LIGHT_KEY, "DIMMABLE_LIGHT", "UNKNOWN_49")
        entries = self.log.entries()
        self.assertIn(LIGHT_KEY.to_config_string(), entries)

    def test_record_is_idempotent_first_write_wins(self):
        self.log.record(LIGHT_KEY, "DIMMABLE_LIGHT", "UNKNOWN_49")
        self.log.record(LIGHT_KEY, "SOMETHING_ELSE", "SOMETHING_ELSE")
        entries = self.log.entries()
        self.assertEqual(entries[LIGHT_KEY.to_config_string()]["device_type"], "DIMMABLE_LIGHT")

    def test_prune_configured_removes_matching_keys(self):
        self.log.record(LIGHT_KEY, "DIMMABLE_LIGHT", "UNKNOWN_49")
        self.log.record(PUMP_KEY, "LATCHING_RELAY_TYPE_2", "WATER_PUMP")
        self.log.prune_configured({LIGHT_KEY.to_config_string()})
        entries = self.log.entries()
        self.assertNotIn(LIGHT_KEY.to_config_string(), entries)
        self.assertIn(PUMP_KEY.to_config_string(), entries)

    def test_entries_empty_when_no_file(self):
        self.assertEqual(self.log.entries(), {})

    def test_external_process_write_is_visible_without_a_restart(self):
        # manage-devices calls prune_configured() from a separate process
        # while publisher.py's own DiscoveryLog instance keeps running.
        self.log.record(LIGHT_KEY, "DIMMABLE_LIGHT", "UNKNOWN_49")
        self.assertIn(LIGHT_KEY.to_config_string(), self.log.entries())

        other_process = DiscoveryLog(self.path)
        other_process.prune_configured({LIGHT_KEY.to_config_string()})

        self.assertNotIn(LIGHT_KEY.to_config_string(), self.log.entries())

    def test_read_does_not_reload_when_file_unchanged(self):
        self.log.record(LIGHT_KEY, "DIMMABLE_LIGHT", "UNKNOWN_49")
        with patch.object(self.log, "_read_unlocked", wraps=self.log._read_unlocked) as wrapped:
            self.log.entries()
            self.log.entries()
            self.assertEqual(wrapped.call_count, 0)


if __name__ == "__main__":
    unittest.main()
