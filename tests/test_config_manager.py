import os
import sys
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
