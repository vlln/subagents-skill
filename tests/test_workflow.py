"""Unit tests for workflow runtime — no backend execution required."""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

_lib = Path(__file__).resolve().parent.parent / "skills" / "workflow" / "scripts" / "lib"
sys.path.insert(0, str(_lib))

from runtime import _extract_text, _extract_exit_code, parallel, pipeline, phase, log


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
        events = [
            {"type": "version", "version": 1},
            {"type": "agent_done", "session": "s1", "exit_code": 0},
        ]
        self.assertEqual(_extract_text(events), "")


class ExtractExitCodeTest(unittest.TestCase):
    def test_success(self):
        events = [{"type": "agent_done", "session": "s1", "exit_code": 0}]
        self.assertEqual(_extract_exit_code(events), 0)

    def test_failure(self):
        events = [{"type": "agent_done", "session": "s1", "exit_code": 1}]
        self.assertEqual(_extract_exit_code(events), 1)

    def test_no_done_event(self):
        events = [{"type": "agent_start", "session": "s1"}]
        self.assertEqual(_extract_exit_code(events), 1)


class ParallelTest(unittest.TestCase):
    def test_all_succeed(self):
        def make_fn(val):
            return lambda: val

        results = parallel([make_fn(1), make_fn(2), make_fn(3)])
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
        def slow(val, delay):
            def fn():
                time.sleep(delay)
                return val
            return fn

        results = parallel([slow(1, 0.1), slow(2, 0.05), slow(3, 0)])
        self.assertEqual(results, [1, 2, 3])


class PipelineTest(unittest.TestCase):
    def test_single_stage(self):
        def stage(item, idx):
            return item * 2

        results = pipeline([1, 2, 3], stage)
        self.assertEqual(results, [2, 4, 6])

    def test_multi_stage(self):
        def stage1(item, idx):
            return item + 1

        def stage2(prev, item, idx):
            return prev * 2

        results = pipeline([1, 2, 3], stage1, stage2)
        # item=1: stage1→2, stage2→4
        # item=2: stage1→3, stage2→6
        # item=3: stage1→4, stage2→8
        self.assertEqual(results, [4, 6, 8])

    def test_stage_failure_returns_none(self):
        def ok(item, idx):
            return item

        def fail(prev, item, idx):
            raise RuntimeError("fail")

        results = pipeline([1, 2], ok, fail)
        self.assertEqual(results, [None, None])

    def test_empty(self):
        self.assertEqual(pipeline([], lambda x, i: x), [])

    def test_original_item_preserved(self):
        captured = []

        def stage1(item, idx):
            return f"processed_{item}"

        def stage2(prev, item, idx):
            captured.append((prev, item, idx))
            return prev

        pipeline(["a"], stage1, stage2)
        self.assertEqual(captured[0], ("processed_a", "a", 0))


if __name__ == "__main__":
    unittest.main(verbosity=2)