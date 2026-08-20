import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from can_link.address_table import AddressTable
from can_link.types import DeviceType, StableKey

LIGHT_KEY = StableKey("function_name", 32, 1)
MOTOR_KEY = StableKey("function_name", 105, 1)  # awning


class ResolveTests(unittest.TestCase):
    def test_unknown_key_resolves_to_none(self):
        table = AddressTable()
        self.assertIsNone(table.resolve(LIGHT_KEY, now=0.0))

    def test_resolves_recently_observed_key(self):
        table = AddressTable()
        table.observe_device_id(LIGHT_KEY, source_address=0x3F, device_type=DeviceType.DIMMABLE_LIGHT, now=0.0)
        self.assertEqual(table.resolve(LIGHT_KEY, now=1.0), 0x3F)

    def test_expires_after_address_expiry_sec(self):
        table = AddressTable(address_expiry_sec=8.0)
        table.observe_device_id(LIGHT_KEY, source_address=0x3F, device_type=DeviceType.DIMMABLE_LIGHT, now=0.0)
        self.assertEqual(table.resolve(LIGHT_KEY, now=7.9), 0x3F)
        self.assertIsNone(table.resolve(LIGHT_KEY, now=8.1))

    def test_device_type_for_returns_current_type(self):
        table = AddressTable()
        table.observe_device_id(LIGHT_KEY, source_address=0x3F, device_type=DeviceType.DIMMABLE_LIGHT, now=0.0)
        self.assertEqual(table.device_type_for(LIGHT_KEY, now=1.0), DeviceType.DIMMABLE_LIGHT)

    def test_device_type_for_expires_with_the_entry(self):
        table = AddressTable(address_expiry_sec=8.0)
        table.observe_device_id(LIGHT_KEY, source_address=0x3F, device_type=DeviceType.DIMMABLE_LIGHT, now=0.0)
        self.assertIsNone(table.device_type_for(LIGHT_KEY, now=9.0))


class ResolveForCommandTests(unittest.TestCase):
    def test_commandable_device_resolves(self):
        table = AddressTable()
        table.observe_device_id(LIGHT_KEY, source_address=0x3F, device_type=DeviceType.DIMMABLE_LIGHT, now=0.0)
        self.assertEqual(table.resolve_for_command(LIGHT_KEY, now=1.0), 0x3F)

    def test_motor_device_never_resolves_for_command(self):
        table = AddressTable()
        table.observe_device_id(
            MOTOR_KEY, source_address=0x88, device_type=DeviceType.MOMENTARY_H_BRIDGE_TYPE_2, now=0.0
        )
        self.assertIsNone(table.resolve_for_command(MOTOR_KEY, now=1.0))
        # but passive resolution still works -- reading motor status is fine
        self.assertEqual(table.resolve(MOTOR_KEY, now=1.0), 0x88)

    def test_unknown_device_type_never_resolves_for_command(self):
        table = AddressTable()
        table.observe_device_id(LIGHT_KEY, source_address=0x3F, device_type=None, now=0.0)
        self.assertIsNone(table.resolve_for_command(LIGHT_KEY, now=1.0))

    def test_expired_entry_never_resolves_for_command(self):
        table = AddressTable(address_expiry_sec=8.0)
        table.observe_device_id(LIGHT_KEY, source_address=0x3F, device_type=DeviceType.DIMMABLE_LIGHT, now=0.0)
        self.assertIsNone(table.resolve_for_command(LIGHT_KEY, now=9.0))

    def test_unknown_key_never_resolves_for_command(self):
        table = AddressTable()
        self.assertIsNone(table.resolve_for_command(LIGHT_KEY, now=0.0))


class BusOutageTests(unittest.TestCase):
    def test_first_ever_frame_does_not_trigger_outage(self):
        table = AddressTable(bus_outage_threshold_sec=12.0)
        self.assertFalse(table.note_bus_activity(now=0.0))

    def test_normal_traffic_gap_does_not_trigger_outage(self):
        table = AddressTable(bus_outage_threshold_sec=12.0)
        table.note_bus_activity(now=0.0)
        self.assertFalse(table.note_bus_activity(now=1.0))

    def test_long_gap_triggers_outage_and_unverifies_all_entries(self):
        table = AddressTable(address_expiry_sec=1000.0, bus_outage_threshold_sec=12.0)
        table.note_bus_activity(now=0.0)
        table.observe_device_id(LIGHT_KEY, source_address=0x3F, device_type=DeviceType.DIMMABLE_LIGHT, now=0.0)
        self.assertEqual(table.resolve_for_command(LIGHT_KEY, now=0.5), 0x3F)

        # long silence on the whole bus -- simulated OneControl power loss
        outage_detected = table.note_bus_activity(now=50.0)
        self.assertTrue(outage_detected)

        # entry hasn't expired (address_expiry_sec is huge) but is no longer
        # command-eligible -- this is the whole point of the outage gate
        self.assertEqual(table.resolve(LIGHT_KEY, now=50.0), 0x3F)  # passive still works
        self.assertIsNone(table.resolve_for_command(LIGHT_KEY, now=50.0))  # commands fail closed

    def test_fresh_device_id_after_outage_reverifies_the_key(self):
        table = AddressTable(address_expiry_sec=1000.0, bus_outage_threshold_sec=12.0)
        table.note_bus_activity(now=0.0)
        table.observe_device_id(LIGHT_KEY, source_address=0x3F, device_type=DeviceType.DIMMABLE_LIGHT, now=0.0)
        table.note_bus_activity(now=50.0)  # outage
        self.assertIsNone(table.resolve_for_command(LIGHT_KEY, now=50.0))

        # device re-announces itself post-recovery -- possibly at a new address
        table.note_bus_activity(now=51.0)
        table.observe_device_id(LIGHT_KEY, source_address=0x77, device_type=DeviceType.DIMMABLE_LIGHT, now=51.0)
        self.assertEqual(table.resolve_for_command(LIGHT_KEY, now=51.5), 0x77)

    def test_outage_does_not_affect_keys_not_yet_seen(self):
        table = AddressTable(bus_outage_threshold_sec=12.0)
        table.note_bus_activity(now=0.0)
        table.note_bus_activity(now=50.0)  # outage, no entries exist yet
        table.observe_device_id(LIGHT_KEY, source_address=0x3F, device_type=DeviceType.DIMMABLE_LIGHT, now=51.0)
        self.assertEqual(table.resolve_for_command(LIGHT_KEY, now=51.5), 0x3F)

    def test_process_restart_starts_with_nothing_verified(self):
        # A restart is just a new AddressTable -- in-memory only, nothing
        # persisted. Nothing is command-eligible until freshly observed.
        table = AddressTable()
        self.assertIsNone(table.resolve_for_command(LIGHT_KEY, now=0.0))


if __name__ == "__main__":
    unittest.main()
