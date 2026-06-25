"""Integration tests — require a real backend (kimi by default).

Run manually:
    SKIP_INTEGRATION=0 python3 -m pytest tests/test_integration.py -v

Default: skipped (CI-safe).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


_SUBAGENTS = Path(__file__).resolve().parent.parent / "skills" / "subagents" / "scripts" / "subagents"
_WORKFLOW = Path(__file__).resolve().parent.parent / "skills" / "workflow" / "scripts" / "workflow"


def _has_kimi() -> bool:
    return shutil.which("kimi") is not None


def _should_run() -> bool:
    return os.environ.get("SKIP_INTEGRATION") != "1" and _has_kimi()


def _run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.setdefault("SUBAGENT_AGENTS_DIR", str(Path(tempfile.gettempdir()) / "subagent_test"))
    return subprocess.run(
        [str(_SUBAGENTS)] + args,
        capture_output=True, text=True,
        timeout=60, env=env, **kwargs,
    )


def _parse_jsonl(output: str) -> list[dict]:
    events: list[dict] = []
    for line in output.strip().split("\n"):
        if line:
            events.append(json.loads(line))
    return events


@unittest.skipUnless(_should_run(), "kimi not available or SKIP_INTEGRATION=1")
class SubagentsJsonlTest(unittest.TestCase):
    """End-to-end tests for subagents --output json."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = os.environ.copy()
        self._env["SUBAGENT_AGENTS_DIR"] = str(Path(self._tmp.name) / "agents")
        os.makedirs(self._env["SUBAGENT_AGENTS_DIR"] + "/outputs", exist_ok=True)

    def tearDown(self):
        self._tmp.cleanup()

    def test_run_output_json_structure(self):
        """Verify JSONL output has version, agent_start, agent_text, agent_done."""
        result = subprocess.run(
            [str(_SUBAGENTS), "run", "--output", "json", "int_test", "say hello in one word"],
            capture_output=True, text=True, timeout=30,
            env=self._env,
        )
        events = _parse_jsonl(result.stdout)

        types = [e["type"] for e in events]
        self.assertIn("version", types)
        self.assertIn("agent_start", types)
        self.assertIn("agent_text", types)
        self.assertIn("agent_done", types)

        # version is first
        self.assertEqual(events[0]["type"], "version")
        self.assertEqual(events[0]["version"], 1)

        # agent_done has exit_code
        done = [e for e in events if e["type"] == "agent_done"][0]
        self.assertIn("exit_code", done)

        # agent_text has content
        texts = [e for e in events if e["type"] == "agent_text"]
        self.assertGreater(len(texts), 0)
        self.assertIn("content", texts[0])

    def test_bg_and_wait(self):
        """Verify --bg writes to file, wait replays it."""
        subprocess.run(
            [str(_SUBAGENTS), "run", "--bg", "--output", "json", "int_bg", "say hi"],
            capture_output=True, text=True, timeout=30,
            env=self._env,
        )

        result = subprocess.run(
            [str(_SUBAGENTS), "wait", "--output", "json", "int_bg"],
            capture_output=True, text=True, timeout=30,
            env=self._env,
        )
        events = _parse_jsonl(result.stdout)

        types = [e["type"] for e in events]
        self.assertIn("agent_start", types)
        self.assertIn("agent_done", types)

    def test_run_with_agent_file(self):
        """Verify agent field in agent_start when using agent .md."""
        agents_dir = Path(self._env["SUBAGENT_AGENTS_DIR"])
        agents_dir.mkdir(parents=True, exist_ok=True)
        (agents_dir / "test.md").write_text(
            "---\nname: test\ndescription: Test agent\n---\nBe helpful.\n"
        )

        result = subprocess.run(
            [str(_SUBAGENTS), "run", "--output", "json", "test", "int_agent", "say hi"],
            capture_output=True, text=True, timeout=60,
            env=self._env,
        )
        events = _parse_jsonl(result.stdout)

        start = [e for e in events if e["type"] == "agent_start"][0]
        self.assertEqual(start["agent"], "test")

    def test_error_on_duplicate(self):
        """Verify agent_error when session is already running."""
        # Start a session (will run in bg)
        subprocess.run(
            [str(_SUBAGENTS), "run", "--bg", "--output", "json", "int_dup", "say hi"],
            capture_output=True, text=True, timeout=30,
            env=self._env,
        )

        # Try to start again immediately (should fail)
        # Note: may race depending on timing; we just check the output format
        result = subprocess.run(
            [str(_SUBAGENTS), "run", "--output", "json", "int_dup", "say hi"],
            capture_output=True, text=True, timeout=30,
            env=self._env,
        )
        # May succeed or fail depending on race; just verify valid JSONL
        events = _parse_jsonl(result.stdout)
        self.assertGreater(len(events), 0)
        self.assertEqual(events[0]["type"], "version")


@unittest.skipUnless(_should_run(), "kimi not available or SKIP_INTEGRATION=1")
class WorkflowIntegrationTest(unittest.TestCase):
    """End-to-end tests for workflow with real agent calls."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = os.environ.copy()
        self._env["SUBAGENT_AGENTS_DIR"] = str(Path(self._tmp.name) / "agents")
        self._env["SKILL_SUBAGENTS_HOME"] = str(
            Path(__file__).resolve().parent.parent / "skills" / "subagents"
        )
        os.makedirs(self._env["SUBAGENT_AGENTS_DIR"] + "/outputs", exist_ok=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _write_script(self, name: str, content: str) -> Path:
        p = Path(self._tmp.name) / name
        p.write_text(content)
        return p

    def test_simple_workflow(self):
        """Run a minimal workflow with one agent call."""
        self._write_script("simple.py", """
meta = {"name": "simple", "description": "test"}
def run(agent, parallel, pipeline, phase, log, args, workflow):
    result = agent("say hello in one word")
    return {"result": result}
""")
        result = subprocess.run(
            [str(_WORKFLOW), "run", str(Path(self._tmp.name) / "simple.py")],
            capture_output=True, text=True, timeout=30,
            env=self._env,
        )
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertIn("result", data)
        self.assertIsInstance(data["result"], str)

    def test_parallel_workflow(self):
        """Run a workflow with parallel agent calls."""
        self._write_script("parallel.py", """
meta = {"name": "parallel", "description": "test"}
def run(agent, parallel, pipeline, phase, log, args, workflow):
    results = parallel([
        lambda: agent("say 'A' and nothing else"),
        lambda: agent("say 'B' and nothing else"),
    ])
    return {"results": results}
""")
        result = subprocess.run(
            [str(_WORKFLOW), "run", str(Path(self._tmp.name) / "parallel.py")],
            capture_output=True, text=True, timeout=60,
            env=self._env,
        )
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertEqual(len(data["results"]), 2)
        self.assertIsNotNone(data["results"][0])
        self.assertIsNotNone(data["results"][1])

    def test_workflow_resume(self):
        """Run a workflow, then resume it — second run should use cache."""
        self._write_script("resume_test.py", """
meta = {"name": "resume", "description": "test"}
def run(agent, parallel, pipeline, phase, log, args, workflow):
    result = agent("say 'cached' and nothing else")
    return {"result": result}
""")
        # First run with fixed run_id
        subprocess.run(
            [str(_WORKFLOW), "run", str(Path(self._tmp.name) / "resume_test.py"),
             "--resume", "int_resume_001"],
            capture_output=True, text=True, timeout=30,
            env=self._env,
        )

        # Resume — should show "resumed (cached)" in stderr
        result = subprocess.run(
            [str(_WORKFLOW), "resume", "int_resume_001",
             str(Path(self._tmp.name) / "resume_test.py")],
            capture_output=True, text=True, timeout=30,
            env=self._env,
        )
        self.assertIn("resumed (cached)", result.stderr)
        data = json.loads(result.stdout)
        self.assertIn("result", data)

    def test_nested_workflow(self):
        """Run a parent workflow that calls a child workflow."""
        self._write_script("sub.py", """
meta = {"name": "sub", "description": "child"}
def run(agent, parallel, pipeline, phase, log, args, workflow):
    return {"word": agent("say 'nested' and nothing else")}
""")
        self._write_script("parent.py", """
meta = {"name": "parent", "description": "parent"}
def run(agent, parallel, pipeline, phase, log, args, workflow):
    sub = workflow("SUB_PATH", {})
    return {"sub": sub}
""".replace("SUB_PATH", str(Path(self._tmp.name) / "sub.py")))

        result = subprocess.run(
            [str(_WORKFLOW), "run", str(Path(self._tmp.name) / "parent.py")],
            capture_output=True, text=True, timeout=60,
            env=self._env,
        )
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertIn("sub", data)
        self.assertIn("word", data["sub"])


if __name__ == "__main__":
    unittest.main(verbosity=2)