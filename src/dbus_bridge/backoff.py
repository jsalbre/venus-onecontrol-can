"""Exponential backoff for service restarts, adapted from
govee-ble-venus-py's RestartBackoff. Pure -- takes an explicit `now`
rather than calling time.time()/time.sleep() itself, so it's testable
without real delays; the caller (publisher.py) does the actual sleep.
"""

from __future__ import annotations


class RestartBackoff:
    def __init__(self, min_delay_sec: float, max_delay_sec: float, reset_after_sec: float) -> None:
        self.min_delay_sec = min_delay_sec
        self.max_delay_sec = max_delay_sec
        self.reset_after_sec = reset_after_sec
        self.current_delay_sec = min_delay_sec
        self._last_success_time: float | None = None

    def next_delay_sec(self) -> float:
        """Returns the delay to wait before the next restart attempt, then
        doubles current_delay_sec (capped at max_delay_sec) for next time."""
        delay = self.current_delay_sec
        self.current_delay_sec = min(self.current_delay_sec * 2, self.max_delay_sec)
        return delay

    def mark_success(self, now: float) -> None:
        self._last_success_time = now

    def reset_if_stable(self, now: float) -> None:
        """Resets current_delay_sec back to min_delay_sec if enough time has
        passed since the last mark_success()."""
        if self._last_success_time is None:
            return
        if (now - self._last_success_time) > self.reset_after_sec:
            self.current_delay_sec = self.min_delay_sec
