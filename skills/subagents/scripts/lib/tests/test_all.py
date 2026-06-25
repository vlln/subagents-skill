"""Unit tests for subagents — no backend execution required."""

import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

# Add lib/ and sub-packages to path
_lib = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_lib / "backends"))
sys.path.insert(0, str(_lib / "transports"))
sys.path.insert(0, str(_lib))

from agent import parse_agent, AgentDef, list_agents
from backends.claude import ClaudeBackend
from backends.codex import CodexBackend
from backends.gemini import _GeminiCli
from backends.kimi import _KimiCli
from backends.kiro import _KiroCli
from backends.opencode import _OpencodeCli
from backends.pi import PiBackend
from backends.qwen import _QwenCli
from cli import JsonlEmitter, _parse_output_flag


# ═══════════════════════════════════════════════════════════════════════════
# agent.py
# ═══════════════════════════════════════════════════════════════════════════

class AgentParseTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name: str, content: str) -> Path:
        p = self._dir / name
        p.write_text(content)
        return p

    def test_parse_basic(self):
        p = self._write("t.md", "---\nname: test\ndescription: A tester\n---\nBe helpful.\n")
        a = parse_agent(p)
        self.assertEqual(a.name, "test")
        self.assertEqual(a.description, "A tester")
        # body must be the ENTIRE file content, including frontmatter
        self.assertIn("---", a.body)
        self.assertIn("name: test", a.body)
        self.assertIn("Be helpful.", a.body)

    def test_parse_no_body(self):
        p = self._write("t.md", "---\nname: x\ndescription: desc\n---\n")
        a = parse_agent(p)
        self.assertEqual(a.name, "x")
        self.assertIn("---", a.body)

    def test_parse_missing_name(self):
        p = self._write("t.md", "---\ndescription: d\n---\nbody\n")
        with self.assertRaises(ValueError):
            parse_agent(p)

    def test_parse_missing_description(self):
        p = self._write("t.md", "---\nname: x\n---\nbody\n")
        with self.assertRaises(ValueError):
            parse_agent(p)

    def test_parse_no_frontmatter(self):
        p = self._write("t.md", "just text\n")
        with self.assertRaises(ValueError):
            parse_agent(p)

    def test_parse_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            parse_agent("/nonexistent/path.md")

    def test_list_agents(self):
        self._write("a.md", "---\nname: a\ndescription: A\n---\n")
        self._write("b.md", "---\nname: b\ndescription: B\n---\n")
        agents = list_agents(str(self._dir))
        self.assertEqual(len(agents), 2)
        self.assertEqual({a.name for a in agents}, {"a", "b"})

    def test_list_agents_skips_invalid(self):
        self._write("bad.md", "no frontmatter")
        self._write("good.md", "---\nname: g\ndescription: G\n---\n")
        agents = list_agents(str(self._dir))
        self.assertEqual(len(agents), 1)
        self.assertEqual(agents[0].name, "g")


# ═══════════════════════════════════════════════════════════════════════════
# JsonlEmitter
# ═══════════════════════════════════════════════════════════════════════════

class JsonlEmitterTest(unittest.TestCase):
    def setUp(self):
        self._buf = io.StringIO()
        self._em = JsonlEmitter(file=self._buf)

    def _lines(self):
        return [json.loads(l) for l in self._buf.getvalue().strip().split("\n")]

    def test_agent_start(self):
        self._em.agent_start("s1", agent="rev", backend="kimi")
        evt = self._lines()[0]
        self.assertEqual(evt["type"], "agent_start")
        self.assertEqual(evt["session"], "s1")
        self.assertEqual(evt["agent"], "rev")
        self.assertEqual(evt["backend"], "kimi")

    def test_agent_start_no_agent(self):
        self._em.agent_start("s1", backend="claude")
        evt = self._lines()[0]
        self.assertIsNone(evt["agent"])

    def test_agent_text(self):
        self._em.agent_text("s1", "hello")
        evt = self._lines()[0]
        self.assertEqual(evt["type"], "agent_text")
        self.assertEqual(evt["content"], "hello")

    def test_agent_done(self):
        self._em.agent_done("s1", exit_code=0)
        evt = self._lines()[0]
        self.assertEqual(evt["type"], "agent_done")
        self.assertEqual(evt["exit_code"], 0)

    def test_agent_error(self):
        self._em.agent_error("s1", "something broke")
        evt = self._lines()[0]
        self.assertEqual(evt["type"], "agent_error")
        self.assertEqual(evt["error"], "something broke")

    def test_agent_list(self):
        self._em.agent_list([{"name": "r", "sessions": []}])
        evt = self._lines()[0]
        self.assertEqual(evt["type"], "agent_list")
        self.assertEqual(len(evt["agents"]), 1)

    def test_agent_status(self):
        self._em.agent_status("a", "s", "done", tasks=[{"prompt": "p", "status": "done"}])
        evt = self._lines()[0]
        self.assertEqual(evt["type"], "agent_status")
        self.assertEqual(evt["status"], "done")
        self.assertEqual(len(evt["tasks"]), 1)

    def test_multiple_events(self):
        self._em.agent_start("s1", backend="kimi")
        self._em.agent_text("s1", "hi")
        self._em.agent_done("s1")
        lines = self._lines()
        self.assertEqual(len(lines), 3)
        self.assertEqual([e["type"] for e in lines], ["agent_start", "agent_text", "agent_done"])


# ═══════════════════════════════════════════════════════════════════════════
# _parse_output_flag
# ═══════════════════════════════════════════════════════════════════════════

class ParseOutputFlagTest(unittest.TestCase):
    def test_no_flag(self):
        self.assertIsNone(_parse_output_flag(["foo", "bar"]))

    def test_json_flag(self):
        self.assertEqual(_parse_output_flag(["--output", "json", "foo"]), ["foo"])

    def test_json_flag_only(self):
        self.assertEqual(_parse_output_flag(["--output", "json"]), [])

    def test_invalid_value(self):
        with self.assertRaises(SystemExit):
            _parse_output_flag(["--output", "xml"])

    def test_missing_value(self):
        with self.assertRaises(SystemExit):
            _parse_output_flag(["--output"])


# ═══════════════════════════════════════════════════════════════════════════
# Backend _cmd_create / _cmd_resume — CLI argument assembly
# ═══════════════════════════════════════════════════════════════════════════

class BackendArgsTest(unittest.TestCase):

    # ── kimi ────────────────────────────────────────────────────────────

    def test_kimi_create_no_system(self):
        cmd = _KimiCli()._cmd_create("hello", None, None, "append")
        self.assertIn("-p", cmd)
        self.assertEqual(cmd[cmd.index("-p") + 1], "hello")

    def test_kimi_create_with_system(self):
        cmd = _KimiCli()._cmd_create("hello", "you are helpful", None, "append")
        prompt = cmd[cmd.index("-p") + 1]
        self.assertIn("System: you are helpful", prompt)

    def test_kimi_create_with_model(self):
        cmd = _KimiCli()._cmd_create("hello", None, "gpt-5", "append")
        self.assertIn("-m", cmd)
        self.assertEqual(cmd[cmd.index("-m") + 1], "gpt-5")

    def test_kimi_resume(self):
        cmd = _KimiCli()._cmd_resume("sid-123", "hello", None, None, "append")
        self.assertIn("-S", cmd)
        self.assertEqual(cmd[cmd.index("-S") + 1], "sid-123")

    # ── claude ──────────────────────────────────────────────────────────

    def test_claude_append_mode(self):
        cmd = ClaudeBackend()._cmd_create("hi", "sys", None, "append")
        self.assertIn("--append-system-prompt", cmd)
        self.assertNotIn("--system-prompt", cmd)

    def test_claude_overwrite_mode(self):
        cmd = ClaudeBackend()._cmd_create("hi", "sys", None, "overwrite")
        self.assertIn("--system-prompt", cmd)
        self.assertNotIn("--append-system-prompt", cmd)

    def test_claude_no_system(self):
        cmd = ClaudeBackend()._cmd_create("hi", None, None, "append")
        self.assertNotIn("--system-prompt", cmd)
        self.assertNotIn("--append-system-prompt", cmd)

    def test_claude_resume(self):
        cmd = ClaudeBackend()._cmd_resume("sid", "hi", "sys", None, "overwrite")
        self.assertIn("--resume", cmd)
        self.assertEqual(cmd[cmd.index("--resume") + 1], "sid")
        self.assertIn("--system-prompt", cmd)

    # ── pi ──────────────────────────────────────────────────────────────

    def test_pi_append_mode(self):
        cmd = PiBackend()._cmd_create("hi", "sys", None, "append")
        self.assertIn("--append-system-prompt", cmd)

    def test_pi_overwrite_mode(self):
        cmd = PiBackend()._cmd_create("hi", "sys", None, "overwrite")
        self.assertIn("--system-prompt", cmd)

    # ── qwen ────────────────────────────────────────────────────────────

    def test_qwen_append_mode(self):
        cmd = _QwenCli()._cmd_create("hi", "sys", None, "append")
        self.assertIn("--append-system-prompt", cmd)

    def test_qwen_overwrite_mode(self):
        cmd = _QwenCli()._cmd_create("hi", "sys", None, "overwrite")
        self.assertIn("--system-prompt", cmd)

    # ── codex ───────────────────────────────────────────────────────────

    def test_codex_create(self):
        cmd = CodexBackend()._cmd_create("hi", "sys", None, "append")
        prompt = cmd[-1] if "-m" not in cmd else cmd[-3]
        self.assertIn("System: sys", prompt)

    # ── opencode ────────────────────────────────────────────────────────

    def test_opencode_create(self):
        cmd = _OpencodeCli()._cmd_create("hi", "sys", None, "append")
        prompt = cmd[-1] if "--model" not in cmd else cmd[-3]
        self.assertIn("System: sys", prompt)

    # ── kiro ────────────────────────────────────────────────────────────

    def test_kiro_create(self):
        cmd = _KiroCli()._cmd_create("hi", None, None, "append")
        self.assertIn("kiro-cli", cmd[0])
        self.assertIn("hi", cmd)

    # ── gemini ──────────────────────────────────────────────────────────

    def test_gemini_create(self):
        cmd = _GeminiCli()._cmd_create("hi", "sys", None, "append")
        prompt = cmd[cmd.index("-p") + 1]
        self.assertIn("System: sys", prompt)
        self.assertIn("-y", cmd)
        self.assertIn("stream-json", cmd)


# ═══════════════════════════════════════════════════════════════════════════
# Backend _parse_line — JSON parsing of sample output
# ═══════════════════════════════════════════════════════════════════════════

class BackendParseLineTest(unittest.TestCase):

    def test_claude_init(self):
        line = json.dumps({"type": "system", "subtype": "init", "session_id": "abc-123"})
        text, sid = ClaudeBackend()._parse_line(line)
        self.assertIsNone(text)
        self.assertEqual(sid, "abc-123")

    def test_claude_assistant(self):
        line = json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "hello"}]}})
        text, sid = ClaudeBackend()._parse_line(line)
        self.assertEqual(text, "hello")

    def test_claude_result(self):
        line = json.dumps({"type": "result", "session_id": "abc-123"})
        text, sid = ClaudeBackend()._parse_line(line)
        self.assertIsNone(text)
        self.assertEqual(sid, "abc-123")

    def test_codex_thread_started(self):
        line = json.dumps({"type": "thread.started", "thread_id": "t-456"})
        text, sid = CodexBackend()._parse_line(line)
        self.assertIsNone(text)
        self.assertEqual(sid, "t-456")

    def test_codex_agent_message(self):
        line = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "done"}})
        text, sid = CodexBackend()._parse_line(line)
        self.assertEqual(text, "done")

    def test_pi_session(self):
        line = json.dumps({"type": "session", "id": "s-789"})
        text, sid = PiBackend()._parse_line(line)
        self.assertIsNone(text)
        self.assertEqual(sid, "s-789")

    def test_pi_text_delta(self):
        line = json.dumps({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "hi"}})
        text, sid = PiBackend()._parse_line(line)
        self.assertEqual(text, "hi")

    def test_qwen_init(self):
        line = json.dumps({"type": "system", "session_id": "qw-001"})
        text, sid = _QwenCli()._parse_line(line)
        self.assertIsNone(text)
        self.assertEqual(sid, "qw-001")

    def test_qwen_assistant(self):
        line = json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "yo"}]}})
        text, sid = _QwenCli()._parse_line(line)
        self.assertEqual(text, "yo")

    def test_kimi_session_id(self):
        line = "kimi -r session_abc123-def456"
        text, sid = _KimiCli()._parse_line(line)
        self.assertIsNone(text)
        self.assertEqual(sid, "session_abc123-def456")

    def test_kimi_no_match(self):
        text, sid = _KimiCli()._parse_line("some random output")
        self.assertIsNone(text)
        self.assertIsNone(sid)

    def test_kiro_assistant(self):
        line = json.dumps({"role": "assistant", "content": "answer"})
        text, sid = _KiroCli()._parse_line(line)
        self.assertEqual(text, "answer")

    def test_kiro_meta(self):
        line = json.dumps({"role": "meta", "session_id": "kr-999"})
        text, sid = _KiroCli()._parse_line(line)
        self.assertIsNone(text)
        self.assertEqual(sid, "kr-999")

    def test_opencode_text(self):
        line = json.dumps({"type": "text", "part": {"text": "streaming"}})
        text, sid = _OpencodeCli()._parse_line(line)
        self.assertEqual(text, "streaming")

    def test_opencode_step(self):
        line = json.dumps({"type": "step_start", "sessionID": "oc-111"})
        text, sid = _OpencodeCli()._parse_line(line)
        self.assertIsNone(text)
        self.assertEqual(sid, "oc-111")

    def test_gemini_init(self):
        from backends.gemini import _GeminiCli
        line = json.dumps({"type": "system", "subtype": "init", "session_id": "gm-222"})
        text, sid = _GeminiCli()._parse_line(line)
        self.assertIsNone(text)
        self.assertEqual(sid, "gm-222")

    def test_non_json_line(self):
        text, sid = ClaudeBackend()._parse_line("plain text")
        self.assertEqual(text, "plain text")
        self.assertIsNone(sid)


# ═══════════════════════════════════════════════════════════════════════════
# registry.py
# ═══════════════════════════════════════════════════════════════════════════

class RegistryTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._reg_path = Path(self._tmp.name) / "agents.json"
        self._old_env = os.environ.get("SU BAGENT_REGISTRY")
        os.environ["SU BAGENT_REGISTRY"] = str(self._reg_path)
        # need to reload registry module to pick up the env change
        import importlib
        import registry
        importlib.reload(registry)
        # also reload lock since it shares env
        import lock
        importlib.reload(lock)
        self.registry = registry
        self.lock = lock

    def tearDown(self):
        self._tmp.cleanup()
        if self._old_env is not None:
            os.environ["SU BAGENT_REGISTRY"] = self._old_env
        else:
            os.environ.pop("SU BAGENT_REGISTRY", None)

    def test_register_and_get_id(self):
        self.registry.register("agent1", "sess1", "sid-001")
        sid = self.registry.get_session_id("agent1", "sess1")
        self.assertEqual(sid, "sid-001")

    def test_get_id_missing(self):
        self.assertIsNone(self.registry.get_session_id("no", "no"))

    def test_get_id_from_any(self):
        self.registry.register("a", "s", "sid-x")
        sid = self.registry.get_session_id_from_any("s")
        self.assertEqual(sid, "sid-x")

    def test_complete(self):
        self.registry.register("a", "s", "sid")
        self.registry.complete("a", "s")
        self.assertEqual(self.registry.get_session_status("a", "s"), "done")

    def test_add_task(self):
        self.registry.register("a", "s", "sid")
        self.registry.add_task("a", "s", "do X", "done")
        data = self.registry.get_all_data()
        tasks = data["a"]["sessions"]["s"]["tasks"]
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["prompt"], "do X")

    def test_list_sessions(self):
        self.registry.register("a", "s1", "id1")
        self.registry.register("a", "s2", "id2")
        sessions = self.registry.list_sessions("a")
        self.assertEqual(set(sessions), {"s1", "s2"})

    def test_status_running(self):
        self.registry.register("a", "s", "sid")
        # lock is not acquired → status is "crashed" (registry says running, no lock)
        self.assertEqual(self.registry.get_session_status("a", "s"), "crashed")

    def test_status_unknown(self):
        self.assertEqual(self.registry.get_session_status("no", "no"), "unknown")


# ═══════════════════════════════════════════════════════════════════════════
# lock.py
# ═══════════════════════════════════════════════════════════════════════════

class LockTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_env = os.environ.get("SU BAGENT_LOCKS")
        os.environ["SU BAGENT_LOCKS"] = str(Path(self._tmp.name) / "locks")
        import importlib
        import lock
        importlib.reload(lock)
        self.lock = lock

    def tearDown(self):
        self._tmp.cleanup()
        if self._old_env is not None:
            os.environ["SU BAGENT_LOCKS"] = self._old_env
        else:
            os.environ.pop("SU BAGENT_LOCKS", None)

    def test_acquire_release(self):
        p = self.lock.acquire("test-session")
        self.assertTrue(p.exists())
        self.assertTrue(self.lock.check("test-session"))
        self.lock.release(p)
        self.assertFalse(self.lock.check("test-session"))

    def test_double_acquire(self):
        p = self.lock.acquire("s")
        try:
            with self.assertRaises(RuntimeError):
                self.lock.acquire("s")
        finally:
            self.lock.release(p)

    def test_check_nonexistent(self):
        self.assertFalse(self.lock.check("nonexistent"))

    def test_stale_detection(self):
        import lock as lock_mod
        p = self.lock.acquire("stale-session")
        # Manually set mtime to 31 minutes ago
        stale_time = time.time() - (31 * 60)
        os.utime(p, (stale_time, stale_time))
        # check should clean up stale lock
        self.assertFalse(self.lock.check("stale-session"))
        self.assertFalse(p.exists())

    def test_get_age(self):
        p = self.lock.acquire("age-session")
        age = self.lock.get_age("age-session")
        self.assertIsNotNone(age)
        self.assertLess(age, 2)  # just created
        self.lock.release(p)
        self.assertIsNone(self.lock.get_age("age-session"))


if __name__ == "__main__":
    unittest.main(verbosity=2)