import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from can_link.types import DeviceType, StableKey, function_name_label, search_function_names


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
    # Real vendor strings, extracted from the LippertConnect app's own
    # FUNCTION_NAME lookup table -- see dev-notes/ARCHITECTURE.md. Spot
    # checks match what the user's phone app actually displays.
    def test_known_value_returns_label(self):
        self.assertEqual(function_name_label(67), "Fresh Tank")

    def test_zero_is_a_real_table_entry_not_the_fallback(self):
        self.assertEqual(function_name_label(0), "UNKNOWN")

    def test_kitchen_island_light(self):
        self.assertEqual(function_name_label(38), "Kitchen Island Light")

    def test_awning(self):
        self.assertEqual(function_name_label(105), "Awning")

    def test_scare_light(self):
        self.assertEqual(function_name_label(122), "Scare Light")

    def test_two_distinct_codes_share_the_leveler_name(self):
        # Not a transcription error -- the vendor table genuinely has two
        # different FUNCTION_NAME codes both named "Leveler".
        self.assertEqual(function_name_label(109), "Leveler")
        self.assertEqual(function_name_label(142), "Leveler")

    def test_unknown_value_returns_placeholder(self):
        self.assertEqual(function_name_label(9999), "UNKNOWN_9999")


class SearchFunctionNamesTests(unittest.TestCase):
    def test_exact_name_matches(self):
        results = search_function_names("Kitchen Island Light")
        self.assertIn((38, "Kitchen Island Light"), results)

    def test_case_insensitive(self):
        results = search_function_names("kitchen island light")
        self.assertIn((38, "Kitchen Island Light"), results)

    def test_substring_matches_multiple_real_entries(self):
        # Both Leveler codes are real, distinct vendor entries -- see
        # FunctionNameLabelTests.
        results = search_function_names("Leveler")
        codes = {value for value, _ in results}
        self.assertIn(109, codes)
        self.assertIn(142, codes)

    def test_results_sorted_by_name(self):
        results = search_function_names("Tank")
        names = [name for _, name in results]
        self.assertEqual(names, sorted(names))

    def test_no_match_returns_empty_list(self):
        self.assertEqual(search_function_names("not a real function name at all"), [])

    def test_empty_query_matches_everything(self):
        results = search_function_names("")
        self.assertGreater(len(results), 400)  # full table is 446 entries


class DeviceTypeTests(unittest.TestCase):
    def test_tank_sensor_value(self):
        self.assertEqual(DeviceType.TANK_SENSOR, 10)

    def test_dimmable_light_value(self):
        self.assertEqual(DeviceType.DIMMABLE_LIGHT, 20)


if __name__ == "__main__":
    unittest.main()
