import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from can_link.address_table import AddressTable
from can_link.types import DeviceType, StableKey
from dbus_bridge.command_gate import CommandGateResult, evaluate_command_request
from dbus_bridge.config_manager import ConfigManager

LIGHT_KEY = StableKey("function_name", 32, 1)
TANK_KEY = StableKey("function_name", 67, 0)


class EvaluateCommandRequestTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.config = ConfigManager(Path(self.tmpdir.name) / "config.json")
        self.address_table = AddressTable()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_not_exposed_device_is_refused(self):
        decision = evaluate_command_request(LIGHT_KEY, self.config, self.address_table, now=0.0)
        self.assertEqual(decision.result, CommandGateResult.NOT_EXPOSED)

    def test_exposed_but_commands_not_enabled_is_refused(self):
        self.config.add_device(LIGHT_KEY, "Kitchen Light", "relay_light", expose=True)
        decision = evaluate_command_request(LIGHT_KEY, self.config, self.address_table, now=0.0)
        self.assertEqual(decision.result, CommandGateResult.COMMANDS_NOT_ENABLED)

    def test_unsupported_device_class_is_refused(self):
        self.config.add_device(TANK_KEY, "Fresh Tank", "tank", expose=True, commands_enabled=True)
        decision = evaluate_command_request(TANK_KEY, self.config, self.address_table, now=0.0)
        self.assertEqual(decision.result, CommandGateResult.UNSUPPORTED_DEVICE_CLASS)

    def test_not_yet_verified_on_address_table_is_refused(self):
        self.config.add_device(LIGHT_KEY, "Kitchen Light", "relay_light", expose=True, commands_enabled=True)
        decision = evaluate_command_request(LIGHT_KEY, self.config, self.address_table, now=0.0)
        self.assertEqual(decision.result, CommandGateResult.NOT_VERIFIED)

    def test_fully_enabled_and_verified_device_is_ok(self):
        self.config.add_device(LIGHT_KEY, "Kitchen Light", "relay_light", expose=True, commands_enabled=True)
        self.address_table.observe_device_id(
            LIGHT_KEY, source_address=0x3F, device_type=DeviceType.LATCHING_RELAY, now=0.0
        )
        decision = evaluate_command_request(LIGHT_KEY, self.config, self.address_table, now=1.0)
        self.assertEqual(decision.result, CommandGateResult.OK)
        self.assertEqual(decision.device_class, "relay_light")
        self.assertEqual(decision.target_address, 0x3F)

    def test_bus_outage_revokes_verification_even_if_still_exposed_and_enabled(self):
        self.config.add_device(LIGHT_KEY, "Kitchen Light", "relay_light", expose=True, commands_enabled=True)
        self.address_table.note_bus_activity(now=0.0)
        self.address_table.observe_device_id(
            LIGHT_KEY, source_address=0x3F, device_type=DeviceType.LATCHING_RELAY, now=0.0
        )
        self.address_table.note_bus_activity(now=50.0)  # outage

        decision = evaluate_command_request(LIGHT_KEY, self.config, self.address_table, now=50.0)
        self.assertEqual(decision.result, CommandGateResult.NOT_VERIFIED)

    def test_commands_disabled_after_being_enabled_is_refused(self):
        self.config.add_device(LIGHT_KEY, "Kitchen Light", "relay_light", expose=True, commands_enabled=True)
        self.config.set_commands_enabled(LIGHT_KEY, False)
        decision = evaluate_command_request(LIGHT_KEY, self.config, self.address_table, now=0.0)
        self.assertEqual(decision.result, CommandGateResult.COMMANDS_NOT_ENABLED)


if __name__ == "__main__":
    unittest.main()
