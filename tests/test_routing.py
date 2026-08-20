import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from can_link.device_id import decode_device_id
from can_link.types import DeviceType
from dbus_bridge.config_manager import ConfigManager
from dbus_bridge.routing import DeviceIdAction, route_device_id, status_update_method_for


def _tank_identity():
    # PRODUCT_ID=0x1234, instance=1, DEVICE_TYPE=TANK_SENSOR(10), FUNCTION_NAME=67 (FRESH_TANK)
    payload = bytes([0x12, 0x34, 0x01, 10, 0x00, 67, 0x01, 0x00])
    return decode_device_id(payload)


def _light_identity():
    # DEVICE_TYPE=DIMMABLE_LIGHT(20), FUNCTION_NAME=32
    payload = bytes([0x00, 0x01, 0x00, 20, 0x00, 32, 0x01, 0x00])
    return decode_device_id(payload)


def _motor_identity():
    # DEVICE_TYPE=MOMENTARY_H_BRIDGE_TYPE_2(33), FUNCTION_NAME=105 (AWNING)
    payload = bytes([0x00, 0x01, 0x00, 33, 0x00, 105, 0x01, 0x00])
    return decode_device_id(payload)


class RouteDeviceIdTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.config = ConfigManager(Path(self.tmpdir.name) / "config.json")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_unconfigured_device_is_not_exposed(self):
        identity = _tank_identity()
        from can_link.device_id import stable_key

        key = stable_key(identity)
        routing = route_device_id(key, identity, self.config, already_created=False)
        self.assertEqual(routing.action, DeviceIdAction.NOT_EXPOSED)

    def test_already_created_short_circuits(self):
        identity = _tank_identity()
        from can_link.device_id import stable_key

        key = stable_key(identity)
        self.config.add_device(key, "Fresh Tank", "tank", expose=True)
        routing = route_device_id(key, identity, self.config, already_created=True)
        self.assertEqual(routing.action, DeviceIdAction.ALREADY_CREATED)

    def test_exposed_but_missing_device_class_in_raw_config(self):
        identity = _tank_identity()
        from can_link.device_id import stable_key

        key = stable_key(identity)
        # Bypass add_device's validation to simulate a hand-edited config
        # missing device_class entirely.
        raw = self.config.read()
        raw["devices"] = [{"stable_key": key.to_config_string(), "expose": True}]
        self.config._atomic_write(raw)

        routing = route_device_id(key, identity, self.config, already_created=False)
        self.assertEqual(routing.action, DeviceIdAction.MISSING_DEVICE_CLASS)

    def test_class_mismatch_when_config_disagrees_with_live_broadcast(self):
        identity = _tank_identity()
        from can_link.device_id import stable_key

        key = stable_key(identity)
        # Config says this is a relay_light, but the device is broadcasting
        # TANK_SENSOR -- must refuse, not guess.
        self.config.add_device(key, "Mislabeled", "relay_light", expose=True)
        routing = route_device_id(key, identity, self.config, already_created=False)
        self.assertEqual(routing.action, DeviceIdAction.CLASS_MISMATCH)
        self.assertEqual(routing.device_class, "relay_light")

    def test_create_service_for_correctly_configured_tank(self):
        identity = _tank_identity()
        from can_link.device_id import stable_key

        key = stable_key(identity)
        self.config.add_device(key, "Fresh Tank", "tank", expose=True)
        routing = route_device_id(key, identity, self.config, already_created=False)
        self.assertEqual(routing.action, DeviceIdAction.CREATE_SERVICE)
        self.assertEqual(routing.device_class, "tank")
        self.assertEqual(routing.service_kind, "tank")

    def test_create_service_for_correctly_configured_light(self):
        identity = _light_identity()
        from can_link.device_id import stable_key

        key = stable_key(identity)
        self.config.add_device(key, "Kitchen Light", "dimmable_light", expose=True)
        routing = route_device_id(key, identity, self.config, already_created=False)
        self.assertEqual(routing.action, DeviceIdAction.CREATE_SERVICE)
        self.assertEqual(routing.service_kind, "switch")

    def test_motor_configured_as_relay_light_is_rejected(self):
        # The critical safety case: someone mistakenly configures a motor's
        # stable_key with a commandable device_class. Must never create a
        # switch-shaped service for it.
        identity = _motor_identity()
        from can_link.device_id import stable_key

        key = stable_key(identity)
        self.config.add_device(key, "Awning (mislabeled)", "relay_light", expose=True)
        routing = route_device_id(key, identity, self.config, already_created=False)
        self.assertEqual(routing.action, DeviceIdAction.CLASS_MISMATCH)

    def test_motor_correctly_configured_as_motor_status(self):
        identity = _motor_identity()
        from can_link.device_id import stable_key

        key = stable_key(identity)
        self.config.add_device(key, "Awning", "motor_status", expose=True)
        routing = route_device_id(key, identity, self.config, already_created=False)
        self.assertEqual(routing.action, DeviceIdAction.CREATE_SERVICE)
        self.assertEqual(routing.service_kind, "motor_status")

    def test_expose_false_is_never_created(self):
        identity = _tank_identity()
        from can_link.device_id import stable_key

        key = stable_key(identity)
        self.config.add_device(key, "Fresh Tank", "tank", expose=False)
        routing = route_device_id(key, identity, self.config, already_created=False)
        self.assertEqual(routing.action, DeviceIdAction.NOT_EXPOSED)


class StatusUpdateMethodForTests(unittest.TestCase):
    def test_tank(self):
        self.assertEqual(status_update_method_for("tank", "tank"), "update")

    def test_motor_status(self):
        self.assertEqual(status_update_method_for("motor_status", "motor_status"), "update")

    def test_switch_relay_light(self):
        self.assertEqual(status_update_method_for("switch", "relay_light"), "update_relay")

    def test_switch_relay_pump(self):
        self.assertEqual(status_update_method_for("switch", "relay_pump"), "update_relay")

    def test_switch_dimmable_light(self):
        self.assertEqual(status_update_method_for("switch", "dimmable_light"), "update_dimmable")

    def test_unknown_kind_raises(self):
        with self.assertRaises(ValueError):
            status_update_method_for("bogus", "tank")


if __name__ == "__main__":
    unittest.main()
