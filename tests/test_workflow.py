"""Unit tests for workflow runtime — no backend execution required."""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

_lib = Path(__file__).resolve().parent.parent / "skills" / "workflow" / "scripts" / "lib"
sys.path.insert(0, str(_lib))

from runtime import (
    WorkflowContext, _extract_text, _extract_exit_code,
    parallel, pipeline, phase, log, set_context, _short_prompt,
)
from display import Display


class WorkflowContextTest(unittest.TestCase):
    def test_session_naming(self):
        ctx = WorkflowContext(run_id="abc123")
        s1 = ctx.next_session()
        s2 = ctx.next_session()
        self.assertEqual(s1, "wf_abc123_1")
        self.assertEqual(s2, "wf_abc123_2")

    def test_no_resume_returns_none(self):
        ctx = WorkflowContext(run_id="x", resume=False)
        self.assertIsNone(ctx.try_resume("wf_x_1"))

    def test_resume_missing_file(self):
        ctx = WorkflowContext(run_id="x", resume=True)
        self.assertIsNone(ctx.try_resume("wf_x_999"))

    def test_resume_cached(self):
        import os
        tmp = tempfile.TemporaryDirectory()
        try:
            # Write a fake completed session output
            out = os.path.join(tmp.name, "wf_abc_1.jsonl")
            os.makedirs(os.path.dirname(out), exist_ok=True)
            Path(out).write_text(
                '{"type":"version","version":1}\n'
                '{"type":"agent_text","session":"wf_abc_1","content":"hello"}\n'
                '{"type":"agent_done","session":"wf_abc_1","exit_code":0}\n'
            )
            ctx = WorkflowContext(run_id="abc", resume=True)
            # Patch _outputs_dir to use our temp dir
            with patch("runtime._outputs_dir", return_value=tmp.name):
                result = ctx.try_resume("wf_abc_1")
                self.assertEqual(result, "hello")
        finally:
            tmp.cleanup()


class ExtractTextTest(unittest.TestCase):
    def test_extracts_agent_text(self):
        events = [
            {"type": "version", "version": 1},
            {"type": "agent_start", "session": "s1"},
            {"type": "agent_text", "session": "s1", "content": "hello"},
            {"type": "agent_text", "session": "s1", "content": "world"},
            {"type": "agent_done", "session": "s1", "exit_code": 0},
        ]
        self.assertEqual(_extract_text(events), "hello\nworld")

    def test_no_text_events(self):
        events = [{"type": "agent_done", "session": "s1", "exit_code": 0}]
        self.assertEqual(_extract_text(events), "")


class ExtractExitCodeTest(unittest.TestCase):
    def test_success(self):
        events = [{"type": "agent_done", "exit_code": 0}]
        self.assertEqual(_extract_exit_code(events), 0)

    def test_failure(self):
        events = [{"type": "agent_done", "exit_code": 1}]
        self.assertEqual(_extract_exit_code(events), 1)

    def test_no_done(self):
        self.assertEqual(_extract_exit_code([]), 1)


class ParallelTest(unittest.TestCase):
    def test_all_succeed(self):
        results = parallel([lambda: 1, lambda: 2, lambda: 3])
        self.assertEqual(results, [1, 2, 3])

    def test_failure_returns_none(self):
        def fail():
            raise RuntimeError("boom")
        results = parallel([fail, lambda: 42])
        self.assertEqual(results, [None, 42])

    def test_empty(self):
        self.assertEqual(parallel([]), [])

    def test_order_preserved(self):
        import time
        results = parallel([
            lambda: (time.sleep(0.1), 1)[1],
            lambda: (time.sleep(0.05), 2)[1],
            lambda: 3,
        ])
        self.assertEqual(results, [1, 2, 3])

    def test_actually_concurrent(self):
        """Verify parallel runs concurrently: wall time < sum of individual times."""
        import time
        def slow(sec):
            time.sleep(sec)
            return sec

        t0 = time.time()
        results = parallel([lambda: slow(0.1), lambda: slow(0.1), lambda: slow(0.1)])
        elapsed = time.time() - t0

        self.assertEqual(results, [0.1, 0.1, 0.1])
        # Concurrent: should be ~0.1s, not 0.3s. Allow generous overhead.
        self.assertLess(elapsed, 0.25)

    def test_thread_safety_many_items(self):
        """Verify no race conditions with many concurrent threads."""
        n = 50
        results = parallel([lambda i=i: i * 2 for i in range(n)])
        self.assertEqual(results, [i * 2 for i in range(n)])

    def test_mixed_failures(self):
        """Verify some succeed, some fail, order preserved."""
        def fail():
            raise RuntimeError()
        results = parallel([
            lambda: 1, fail, lambda: 3, fail, lambda: 5,
        ])
        self.assertEqual(results, [1, None, 3, None, 5])


class PipelineTest(unittest.TestCase):
    def test_single_stage(self):
        results = pipeline([1, 2, 3], lambda item, idx: item * 2)
        self.assertEqual(results, [2, 4, 6])

    def test_multi_stage(self):
        def s1(item, idx): return item + 1
        def s2(prev, item, idx): return prev * 2
        self.assertEqual(pipeline([1, 2, 3], s1, s2), [4, 6, 8])

    def test_failure(self):
        def ok(item, idx): return item
        def fail(prev, item, idx): raise RuntimeError()
        self.assertEqual(pipeline([1, 2], ok, fail), [None, None])

    def test_empty(self):
        self.assertEqual(pipeline([], lambda x, i: x), [])

    def test_original_item_preserved(self):
        captured = []
        def s1(item, idx): return f"p_{item}"
        def s2(prev, item, idx):
            captured.append((prev, item, idx))
            return prev
        pipeline(["a"], s1, s2)
        self.assertEqual(captured[0], ("p_a", "a", 0))

    def test_pipeline_is_concurrent(self):
        """Verify items flow independently — no inter-stage barrier."""
        import time
        order: list[str] = []

        def s1(item, idx):
            order.append(f"s1_{item}")
            time.sleep(0.05)
            return item

        def s2(prev, item, idx):
            order.append(f"s2_{item}")
            return prev

        t0 = time.time()
        pipeline(["a", "b", "c"], s1, s2)
        elapsed = time.time() - t0

        # Without barrier: all s1 run first, then all s2 — total ~0.05s, not 3×0.05
        self.assertLess(elapsed, 0.15)
        # Each item should finish s1 before s2 (same item), but items interleave
        self.assertIn("s1_a", order)
        self.assertIn("s2_a", order)
        self.assertLess(order.index("s1_a"), order.index("s2_a"))

    def test_many_items(self):
        """Stress test with many items."""
        n = 30
        def s1(item, idx): return item * 2
        def s2(prev, item, idx): return prev + 1
        results = pipeline(list(range(n)), s1, s2)
        self.assertEqual(results, [i * 2 + 1 for i in range(n)])

    def test_empty_stages(self):
        """No stages: each item is returned as-is."""
        results = pipeline([1, 2, 3])
        self.assertEqual(results, [1, 2, 3])


class SetContextTest(unittest.TestCase):
    def test_default_context(self):
        ctx = set_context()
        self.assertIsNotNone(ctx.run_id)
        self.assertFalse(ctx.resume)
        self.assertEqual(ctx.next_session(), f"wf_{ctx.run_id}_1")

    def test_resume_context(self):
        ctx = set_context(run_id="myrun", resume=True)
        self.assertEqual(ctx.run_id, "myrun")
        self.assertTrue(ctx.resume)
        self.assertEqual(ctx.next_session(), "wf_myrun_1")


# ═══════════════════════════════════════════════════════════════════════════
# display.py
# ═══════════════════════════════════════════════════════════════════════════

class DisplayTest(unittest.TestCase):
    def setUp(self):
        # Force display into non-tty mode so it doesn't emit ANSI codes
        from display import Display
        self._display = Display(name="test-wf", run_id="abc123")
        self._display._enabled = False

    def test_phase_tracking(self):
        self._display.phase("Scan")
        self._display.phase("Review")
        with self._display._lock:
            self.assertEqual(len(self._display._phases), 2)
            self.assertEqual(self._display._phases[0]["title"], "Scan")
            self.assertEqual(self._display._phases[0]["status"], "done")  # ended by next phase
            self.assertEqual(self._display._phases[1]["title"], "Review")
            self.assertEqual(self._display._phases[1]["status"], "running")

    def test_agent_lifecycle(self):
        self._display.phase("Test")
        self._display.agent_start("Security check", "Review security")
        with self._display._lock:
            self.assertEqual(len(self._display._agents), 1)
            self.assertEqual(self._display._agents[0]["label"], "Security check")
            self.assertEqual(self._display._agents[0]["status"], "running")
        self._display.agent_done("Security check", success=True, elapsed=2.5)
        with self._display._lock:
            self.assertEqual(self._display._agents[0]["status"], "done")
            self.assertEqual(self._display._agents[0]["elapsed"], 2.5)

    def test_agent_failed(self):
        self._display.phase("Test")
        self._display.agent_start("bad", "fails")
        self._display.agent_done("bad", success=False, elapsed=1.0)
        with self._display._lock:
            self.assertEqual(self._display._agents[0]["status"], "failed")

    def test_agent_skip(self):
        self._display.phase("Test")
        self._display.agent_start("cached", "skip me")
        self._display.agent_skip("cached")
        with self._display._lock:
            self.assertEqual(self._display._agents[0]["status"], "skipped")

    def test_set_total_phases(self):
        self._display.set_total_phases(3)
        self.assertEqual(self._display._total_phases, 3)

    def test_summary_renders(self):
        self._display.phase("Scan")
        self._display.agent_start("find files", "Find all files")
        self._display.agent_done("find files", success=True, elapsed=1.2)
        self._display.phase("Review")
        self._display.agent_start("security", "Review security")
        self._display.agent_done("security", success=False, elapsed=3.0)
        summary = self._display.summary()
        self.assertIn("Workflow Summary: test-wf", summary)
        self.assertIn("Scan", summary)
        self.assertIn("Review", summary)
        self.assertIn("find files", summary)
        self.assertIn("security", summary)

    def test_agent_phase_association(self):
        self._display.phase("Phase A")
        self._display.agent_start("a1", "task a1")
        self._display.agent_done("a1", success=True, elapsed=1.0)
        self._display.phase("Phase B")
        self._display.agent_start("b1", "task b1")
        self._display.agent_done("b1", success=True, elapsed=2.0)
        with self._display._lock:
            a_agents = [a for a in self._display._agents if a["phase"] == "Phase A"]
            b_agents = [a for a in self._display._agents if a["phase"] == "Phase B"]
            self.assertEqual(len(a_agents), 1)
            self.assertEqual(a_agents[0]["label"], "a1")
            self.assertEqual(len(b_agents), 1)
            self.assertEqual(b_agents[0]["label"], "b1")

    def test_thread_safety(self):
        """Multiple threads updating agent state concurrently."""
        import threading
        self._display.phase("Parallel")

        def _agent_work(label: str, duration: float, success: bool):
            self._display.agent_start(label, f"task {label}")
            time.sleep(duration)
            self._display.agent_done(label, success=success, elapsed=duration)

        threads = [
            threading.Thread(target=_agent_work, args=(f"a{i}", 0.05, i % 2 == 0))
            for i in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        with self._display._lock:
            self.assertEqual(len(self._display._agents), 10)
            done = sum(1 for a in self._display._agents if a["status"] == "done")
            failed = sum(1 for a in self._display._agents if a["status"] == "failed")
            self.assertEqual(done + failed, 10)

    def test_render_does_not_crash(self):
        self._display.phase("Test")
        self._display.agent_start("ok", "does something")
        self._display.agent_done("ok", success=True, elapsed=1.0)
        rendered = self._display._render()
        self.assertIn("Test", rendered)
        self.assertIn("ok", rendered)


class ShortPromptTest(unittest.TestCase):
    def test_short_prompt(self):
        from runtime import _short_prompt
        self.assertEqual(_short_prompt("Hello"), "Hello")
        self.assertEqual(_short_prompt("Hello\nWorld"), "Hello")
        long_prompt = "a" * 100
        result = _short_prompt(long_prompt)
        self.assertLessEqual(len(result), 50)
        self.assertTrue(result.endswith("..."))


if __name__ == "__main__":
    unittest.main(verbosity=2)