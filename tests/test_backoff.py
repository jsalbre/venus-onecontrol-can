import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dbus_bridge.backoff import RestartBackoff


class RestartBackoffTests(unittest.TestCase):
    def test_starts_at_min_delay(self):
        backoff = RestartBackoff(min_delay_sec=30, max_delay_sec=300, reset_after_sec=3600)
        self.assertEqual(backoff.next_delay_sec(), 30)

    def test_doubles_each_call(self):
        backoff = RestartBackoff(min_delay_sec=30, max_delay_sec=300, reset_after_sec=3600)
        self.assertEqual(backoff.next_delay_sec(), 30)
        self.assertEqual(backoff.next_delay_sec(), 60)
        self.assertEqual(backoff.next_delay_sec(), 120)
        self.assertEqual(backoff.next_delay_sec(), 240)

    def test_caps_at_max_delay(self):
        backoff = RestartBackoff(min_delay_sec=30, max_delay_sec=300, reset_after_sec=3600)
        for _ in range(10):
            delay = backoff.next_delay_sec()
        self.assertEqual(delay, 300)

    def test_reset_if_stable_restores_min_delay(self):
        backoff = RestartBackoff(min_delay_sec=30, max_delay_sec=300, reset_after_sec=3600)
        backoff.next_delay_sec()
        backoff.next_delay_sec()
        backoff.mark_success(now=0.0)
        backoff.reset_if_stable(now=3700.0)
        self.assertEqual(backoff.current_delay_sec, 30)

    def test_reset_if_stable_does_nothing_before_threshold(self):
        backoff = RestartBackoff(min_delay_sec=30, max_delay_sec=300, reset_after_sec=3600)
        backoff.next_delay_sec()
        backoff.mark_success(now=0.0)
        backoff.reset_if_stable(now=100.0)
        self.assertEqual(backoff.current_delay_sec, 60)

    def test_reset_if_stable_before_any_success_does_nothing(self):
        backoff = RestartBackoff(min_delay_sec=30, max_delay_sec=300, reset_after_sec=3600)
        backoff.next_delay_sec()
        backoff.reset_if_stable(now=99999.0)
        self.assertEqual(backoff.current_delay_sec, 60)


if __name__ == "__main__":
    unittest.main()
