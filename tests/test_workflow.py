"""Unit tests for workflow runtime — no backend execution required."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

_lib = Path(__file__).resolve().parent.parent / "skills" / "workflow" / "scripts" / "lib"
sys.path.insert(0, str(_lib))

from runtime import (
    WorkflowContext, _extract_text, _extract_exit_code,
    parallel, pipeline, phase, log, set_context,
)


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


if __name__ == "__main__":
    unittest.main(verbosity=2)