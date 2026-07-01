"""Unit tests for progress.ProgressEstimator — pure data layer, no I/O."""

import math
import sys
import unittest
from pathlib import Path

_lib = Path(__file__).resolve().parent.parent / "skills" / "subagents" / "scripts" / "lib"
sys.path.insert(0, str(_lib))

from progress import Phase, ProgressEstimate, ProgressEstimator


class TestProgressLifecycle(unittest.TestCase):
    """Basic lifecycle: start → tick → complete."""

    def setUp(self):
        self.est = ProgressEstimator()

    def test_initial_state_is_pending(self):
        e = self.est.estimate("s1", 42, now_ms=0)
        self.assertEqual(e.phase, Phase.PENDING)
        self.assertEqual(e.raw_ticks, 0)
        self.assertFalse(e.boosted)

    def test_started_transitions_to_running(self):
        self.est.mark_started("s1", now_ms=1000)
        e = self.est.estimate("s1", 42, now_ms=2000)
        self.assertEqual(e.phase, Phase.RUNNING)
        self.assertEqual(e.raw_ticks, 1)  # starts at 1
        self.assertFalse(e.boosted)       # no prior yet

    def test_tool_calls_increment_raw_ticks(self):
        self.est.mark_started("s1", now_ms=0)
        self.est.record_tool_call("s1", "tc1", now_ms=1000)
        self.est.record_tool_call("s1", "tc2", now_ms=2000)
        e = self.est.estimate("s1", 42, now_ms=3000)
        # raw_ticks = 1 (from start) + 2 = 3
        self.assertEqual(e.raw_ticks, 3)

    def test_completed_transitions_to_completed(self):
        self.est.mark_started("s1", now_ms=0)
        self.est.record_tool_call("s1", "tc1", now_ms=1000)
        self.est.mark_completed("s1", now_ms=2000)
        e = self.est.estimate("s1", 42, now_ms=3000)
        self.assertEqual(e.phase, Phase.COMPLETED)

    def test_duplicate_tool_call_rejected(self):
        self.est.mark_started("s1", now_ms=0)
        ok1 = self.est.record_tool_call("s1", "tc1", now_ms=1000)
        ok2 = self.est.record_tool_call("s1", "tc1", now_ms=1500)
        self.assertTrue(ok1)
        self.assertFalse(ok2)
        e = self.est.estimate("s1", 42, now_ms=2000)
        # 1 from start + 1 unique tool call = 2
        self.assertEqual(e.raw_ticks, 2)


class TestBoostWithPrior(unittest.TestCase):
    """Boost behaviour when prior samples exist."""

    def setUp(self):
        self.est = ProgressEstimator()

    def _complete_member(self, key: str, start_ms: float, ticks: int, duration_ms: float):
        """Simulate a completed member."""
        self.est.mark_started(key, now_ms=start_ms)
        for i in range(ticks - 1):  # start already counts as 1 tick
            self.est.record_tool_call(key, f"tc-{key}-{i}", now_ms=start_ms + duration_ms * (i + 1) / ticks)
        self.est.mark_completed(key, now_ms=start_ms + duration_ms)

    def test_no_boost_without_prior(self):
        self.est.mark_started("s1", now_ms=0)
        self.est.record_tool_call("s1", "tc1", now_ms=1000)
        e = self.est.estimate("s1", 42, now_ms=2000)
        self.assertFalse(e.boosted)
        self.assertIsNone(e.confidence)

    def test_boost_after_one_completed(self):
        # Complete one member: 12 ticks in 45s
        self._complete_member("s1", start_ms=0, ticks=12, duration_ms=45000)
        # Start another
        self.est.mark_started("s2", now_ms=45000)
        for i in range(3):
            self.est.record_tool_call("s2", f"tc-{i}", now_ms=45000 + (i + 1) * 5000)
        # 4 ticks total (1 start + 3), 15s elapsed
        # First call primes last_estimate_at_ms — no catch-up yet (elapsed=0)
        e1 = self.est.estimate("s2", 42, now_ms=60000)
        self.assertIsNotNone(e1.target_ticks)
        self.assertGreater(e1.target_ticks, e1.raw_ticks)  # boost is pending
        # Second call after 500ms: catch-up should advance
        e2 = self.est.estimate("s2", 42, now_ms=60500)
        self.assertTrue(e2.boosted)
        self.assertIsNotNone(e2.confidence)
        self.assertGreater(e2.confidence, 0)
        self.assertLess(e2.confidence, 1)
        self.assertGreater(e2.display_ticks, e2.raw_ticks)

    def test_boost_grows_with_more_samples(self):
        # Complete two members
        self._complete_member("s1", start_ms=0, ticks=12, duration_ms=45000)
        self._complete_member("s2", start_ms=46000, ticks=14, duration_ms=50000)
        # Start a third
        self.est.mark_started("s3", now_ms=100000)
        for i in range(4):
            self.est.record_tool_call("s3", f"tc-{i}", now_ms=100000 + (i + 1) * 4000)
        # Prime + catch-up
        self.est.estimate("s3", 42, now_ms=116000)
        e = self.est.estimate("s3", 42, now_ms=116500)
        self.assertTrue(e.boosted)
        # confidence should be higher with 2 completed samples
        self.assertGreater(e.confidence, 0.3)

    def test_estimated_total_reasonable(self):
        self._complete_member("s1", start_ms=0, ticks=12, duration_ms=45000)
        self.est.mark_started("s2", now_ms=46000)
        for i in range(4):
            self.est.record_tool_call("s2", f"tc-{i}", now_ms=46000 + (i + 1) * 4000)
        e = self.est.estimate("s2", 42, now_ms=62000)
        self.assertIsNotNone(e.estimated_total_tool_calls)
        # Should be near 12, within soft bounds [12/1.5=8, 12*1.5=18]
        self.assertGreaterEqual(e.estimated_total_tool_calls, 8)
        self.assertLessEqual(e.estimated_total_tool_calls, 18)


class TestCatchUpAnimation(unittest.TestCase):
    """display_ticks should smoothly catch up to target."""

    def setUp(self):
        self.est = ProgressEstimator(catchup_time_ms=1500)

    def _complete_member(self, key: str, start_ms: float, ticks: int, duration_ms: float):
        self.est.mark_started(key, now_ms=start_ms)
        for i in range(ticks - 1):
            self.est.record_tool_call(key, f"tc-{key}-{i}", now_ms=start_ms + duration_ms * (i + 1) / ticks)
        self.est.mark_completed(key, now_ms=start_ms + duration_ms)

    def test_display_ticks_catch_up_over_time(self):
        self._complete_member("s1", start_ms=0, ticks=12, duration_ms=45000)
        self.est.mark_started("s2", now_ms=46000)
        for i in range(4):
            self.est.record_tool_call("s2", f"tc-{i}", now_ms=46000 + (i + 1) * 4000)

        # First call primes last_estimate_at_ms (elapsed=0, no catch-up)
        e0 = self.est.estimate("s2", 42, now_ms=62000)
        self.assertFalse(e0.boosted)
        self.assertIsNotNone(e0.target_ticks)
        self.assertGreater(e0.target_ticks, e0.raw_ticks)

        # After 750ms (half the catchup time), display should have moved
        e1 = self.est.estimate("s2", 42, now_ms=62750)
        self.assertTrue(e1.boosted)
        d1 = e1.display_ticks

        # After 3s (2x catchup time), should be closer to target
        e2 = self.est.estimate("s2", 42, now_ms=65000)
        d2 = e2.display_ticks
        self.assertGreater(d2, d1)

    def test_has_pending_catchup(self):
        self._complete_member("s1", start_ms=0, ticks=12, duration_ms=45000)
        self.est.mark_started("s2", now_ms=46000)
        for i in range(4):
            self.est.record_tool_call("s2", f"tc-{i}", now_ms=46000 + (i + 1) * 4000)
        self.est.estimate("s2", 42, now_ms=62000)
        # Should have pending catchup because target > display initially
        self.assertTrue(self.est.has_pending_catchup())


class TestPauseResume(unittest.TestCase):
    """Suspended/queued state should not affect active elapsed time."""

    def setUp(self):
        self.est = ProgressEstimator()

    def test_suspended_phase(self):
        self.est.mark_started("s1", now_ms=1000)
        self.est.record_tool_call("s1", "tc1", now_ms=2000)
        self.est.mark_queued("s1", now_ms=3000)  # suspend
        e = self.est.estimate("s1", 42, now_ms=4000)
        self.assertEqual(e.phase, Phase.SUSPENDED)

    def test_paused_time_not_counted_in_active_elapsed(self):
        self.est.mark_started("s1", now_ms=0)
        self.est.record_tool_call("s1", "tc1", now_ms=5000)
        self.est.mark_queued("s1", now_ms=5000)  # pause at 5s
        # Resume at 35s (30s paused)
        self.est.mark_started("s1", now_ms=35000)
        self.est.record_tool_call("s1", "tc2", now_ms=40000)
        # Active elapsed should be ~10s (5s before pause + 5s after), not 40s
        e = self.est.estimate("s1", 42, now_ms=40000)
        # active_duration_ms should be around 10000, not 40000
        self.assertLess(e.active_duration_ms, 15000)


class TestEstimateAll(unittest.TestCase):
    def test_estimate_all_returns_all_members(self):
        self.est = ProgressEstimator()
        self.est.mark_started("a", now_ms=0)
        self.est.mark_started("b", now_ms=0)
        self.est.mark_started("c", now_ms=0)
        results = self.est.estimate_all(42, now_ms=1000)
        self.assertEqual(set(results.keys()), {"a", "b", "c"})
        for e in results.values():
            self.assertIsInstance(e, ProgressEstimate)


class TestEdgeCases(unittest.TestCase):
    def setUp(self):
        self.est = ProgressEstimator()

    def test_zero_capacity(self):
        self.est.mark_started("s1", now_ms=0)
        e = self.est.estimate("s1", 0, now_ms=1000)
        self.assertGreaterEqual(e.display_ticks, 0)

    def test_no_started_member(self):
        e = self.est.estimate("nonexistent", 42, now_ms=0)
        self.assertEqual(e.phase, Phase.PENDING)
        self.assertEqual(e.raw_ticks, 0)

    def test_failed_member_not_used_as_prior(self):
        self.est.mark_started("s1", now_ms=0)
        self.est.record_tool_call("s1", "tc1", now_ms=1000)
        self.est.mark_failed("s1", now_ms=2000)
        # No completed samples, so no prior
        self.est.mark_started("s2", now_ms=3000)
        e = self.est.estimate("s2", 42, now_ms=4000)
        self.assertFalse(e.boosted)

    def test_cancelled_member_not_used_as_prior(self):
        self.est.mark_started("s1", now_ms=0)
        self.est.record_tool_call("s1", "tc1", now_ms=1000)
        self.est.mark_cancelled("s1", now_ms=2000)
        self.est.mark_started("s2", now_ms=3000)
        e = self.est.estimate("s2", 42, now_ms=4000)
        self.assertFalse(e.boosted)

    def test_ensure_member_creates_pending(self):
        self.est.ensure_member("s1")
        e = self.est.estimate("s1", 42, now_ms=0)
        self.assertEqual(e.phase, Phase.PENDING)

    def test_display_ticks_never_negative(self):
        self.est.mark_started("s1", now_ms=0)
        e = self.est.estimate("s1", 42, now_ms=1000)
        self.assertGreaterEqual(e.display_ticks, 0)

    def test_raw_ticks_never_exceeds_display_on_complete(self):
        self.est.mark_started("s1", now_ms=0)
        for i in range(10):
            self.est.record_tool_call("s1", f"tc{i}", now_ms=(i + 1) * 1000)
        self.est.mark_completed("s1", now_ms=11000)
        e = self.est.estimate("s1", 42, now_ms=12000)
        self.assertGreaterEqual(e.display_ticks, e.raw_ticks)


if __name__ == "__main__":
    unittest.main()