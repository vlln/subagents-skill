"""CLI argument parsing and command routing for subagent."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from agent import list_agents, parse_agent
from backends.claude import ClaudeBackend
from backends.codex import CodexBackend
from backends.gemini import GeminiBackend
from backends.kimi import KimiBackend
from backends.kiro import KiroBackend
from backends.opencode import OpencodeBackend
from backends.pi import PiBackend
from backends.qwen import QwenBackend
from lock import acquire, check, release
from registry import (
    add_task,
    complete,
    get_all_data,
    get_session_id,
    get_session_id_from_any,
    get_session_status,
    list_sessions,
    register,
)

if TYPE_CHECKING:
    from backends.base import BaseBackend

AGENTS_DIR = os.environ.get("SUBAGENT_AGENTS_DIR", ".agents/subagent")
OUTPUT_DIR = os.path.join(AGENTS_DIR, "outputs")

BACKEND_MAP: dict[str, type[BaseBackend]] = {
    "kimi":     KimiBackend,
    "claude":   ClaudeBackend,
    "codex":    CodexBackend,
    "pi":       PiBackend,
    "kiro":     KiroBackend,
    "opencode": OpencodeBackend,
    "qwen":     QwenBackend,
    "gemini":   GeminiBackend,
}

# ── JSONL output ──────────────────────────────────────────────────────────

SCHEMA_VERSION = 1  # increment on breaking schema changes


class JsonlEmitter:
    """Emits JSONL events to a file (stdout or a disk file)."""

    def __init__(self, file=None) -> None:
        self._file = file or sys.stdout
        self.emit(type="version", version=SCHEMA_VERSION)

    def emit(self, **event) -> None:
        print(json.dumps(event, ensure_ascii=False), file=self._file, flush=True)

    def agent_start(self, session: str, agent: str | None = None, backend: str | None = None) -> None:
        self.emit(type="agent_start", session=session, agent=agent, backend=backend)

    def agent_text(self, session: str, content: str) -> None:
        self.emit(type="agent_text", session=session, content=content)

    def agent_done(self, session: str, exit_code: int = 0) -> None:
        self.emit(type="agent_done", session=session, exit_code=exit_code)

    def agent_error(self, session: str, error: str) -> None:
        self.emit(type="agent_error", session=session, error=error)

    def agent_list(self, agents: list[dict]) -> None:
        self.emit(type="agent_list", agents=agents)

    def agent_status(self, agent: str, session: str | None, status: str, tasks: list[dict] | None = None) -> None:
        evt: dict = {"type": "agent_status", "agent": agent, "status": status}
        if session:
            evt["session"] = session
        if tasks is not None:
            evt["tasks"] = tasks
        self.emit(**evt)

    def close(self) -> None:
        if self._file is not sys.stdout:
            self._file.close()


def _make_text_handler(emitter: JsonlEmitter, session: str) -> Callable[[str], None]:
    def handler(text: str) -> None:
        if text:
            emitter.agent_text(session, text)
    return handler


# ── backend helpers ───────────────────────────────────────────────────────

def _detect_backend() -> str:
    from backends.diagnostics import BACKEND_META, list_available_backends, format_install_guide

    available = list_available_backends()
    if available:
        print(f"[subagents] Detected backends: {', '.join(available)}", file=sys.stderr)
        return available[0]

    # No backends found — give the user clear guidance
    print("[subagents] No agent backends found on PATH.", file=sys.stderr)
    print(file=sys.stderr)
    print(format_install_guide(), file=sys.stderr)
    print(file=sys.stderr)
    print("Then run: subagents run --backend <name> <session> <prompt>", file=sys.stderr)
    sys.exit(1)


def _make_backend(
    backend_name: str | None,
    transport: str | None,
    text_handler: Callable[[str], None] | None = None,
) -> BaseBackend:
    if backend_name is None:
        backend_name = _detect_backend()
    cls = BACKEND_MAP.get(backend_name)
    if cls is None:
        print(f"Error: unknown backend '{backend_name}'.", file=sys.stderr)
        print(f"Available backends: {', '.join(BACKEND_MAP.keys())}", file=sys.stderr)
        sys.exit(1)
    import inspect
    from backends.diagnostics import check_binary, check_smoke

    # Pre-flight: check that the binary is available
    if not check_binary(backend_name):
        from backends.diagnostics import print_diagnostics
        print_diagnostics(backend_name)
        sys.exit(1)

    # Smoke test: warn if the binary seems broken (but don't abort)
    ok, msg = check_smoke(backend_name)
    if not ok:
        print(f"[subagents] Warning: {msg}", file=sys.stderr)
        from backends.diagnostics import BACKEND_META
        meta = BACKEND_META.get(backend_name, {})
        if meta.get("auth_help"):
            print(f"[subagents]   {meta['auth_help']}", file=sys.stderr)

    sig = inspect.signature(cls)
    kwargs: dict = {}
    if "transport" in sig.parameters:
        kwargs["transport"] = transport
    if "text_handler" in sig.parameters:
        kwargs["text_handler"] = text_handler
    if "backend_name" in sig.parameters:
        kwargs["backend_name"] = backend_name
    return cls(**kwargs)


def _fail(msg: str) -> None:
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(1)


# ── run ───────────────────────────────────────────────────────────────────

def cmd_run(args: list[str]) -> None:
    """Run a task on an agent session."""
    bg = False
    backend_override: str | None = None
    transport: str | None = None
    system_mode = "append"
    output_json = False

    while args and args[0].startswith("--"):
        flag = args.pop(0)
        if flag == "--bg":
            bg = True
        elif flag == "--backend":
            backend_override = args.pop(0) if args else _fail("--backend requires a value")
        elif flag == "--transport":
            transport = args.pop(0) if args else _fail("--transport requires a value")
            if transport not in ("cli", "acp"):
                _fail(f"--transport must be 'cli' or 'acp', got '{transport}'")
        elif flag == "--system-mode":
            system_mode = args.pop(0) if args else _fail("--system-mode requires a value")
            if system_mode not in ("append", "overwrite"):
                _fail(f"--system-mode must be 'append' or 'overwrite', got '{system_mode}'")
        elif flag == "--output":
            val = args.pop(0) if args else _fail("--output requires a value")
            if val != "json":
                _fail(f"--output must be 'json', got '{val}'")
            output_json = True
        else:
            _fail(f"unknown flag {flag}")

    if len(args) < 2:
        _fail("Usage: subagents run [--bg] [--backend <name>] <agent> <session> <prompt>")

    arg1 = args[0]
    agent_path = Path(AGENTS_DIR) / f"{arg1}.md"

    if agent_path.is_file():
        agent_name = args.pop(0)
        session_name = args.pop(0) if args else ""
        task = " ".join(args) if args else ""
        if not session_name or not task:
            _fail("Usage: subagents run [--bg] <agent> <session> <prompt>")
        _run_with_agent(agent_name, session_name, task, bg, backend_override, transport, system_mode, output_json)
    else:
        session_name = args.pop(0)
        task = " ".join(args) if args else ""
        if not task:
            _fail("Usage: subagents run [--bg] <session> <prompt>")
        _run_no_agent(session_name, task, bg, backend_override, transport, output_json)


def _run_with_agent(
    agent_name: str,
    session_name: str,
    task: str,
    bg: bool,
    backend_override: str | None,
    transport: str | None,
    system_mode: str,
    output_json: bool,
) -> None:
    agent = parse_agent(Path(AGENTS_DIR) / f"{agent_name}.md")
    backend_name = backend_override or _detect_backend()

    if output_json:
        _run_with_agent_json(agent, agent_name, session_name, task, bg, backend_name, backend_override, transport, system_mode)
        return

    print(f"[subagents] Agent: {agent.name} — {agent.description}", file=sys.stderr)
    print(f"[subagents] Session name: {session_name}", file=sys.stderr)
    print(f"[subagents] Backend: {backend_name}", file=sys.stderr)

    if check(session_name):
        _fail(f"session '{session_name}' is already running. Wait with: subagents wait {session_name}")

    lock_path = acquire(session_name)

    def _execute() -> int:
        backend = _make_backend(backend_override, transport)
        try:
            existing_sid = get_session_id(agent_name, session_name)
            if existing_sid:
                print("[subagents] Resuming existing session...", file=sys.stderr)
                exit_code = backend.resume_session(existing_sid, task, agent.body or None, system_mode=system_mode)
            else:
                print("[subagents] Creating new session...", file=sys.stderr)
                sid, exit_code = backend.create_session(task, agent.body or None, system_mode=system_mode)
                register(agent_name, session_name, sid)
                print(f"[subagents] Session registered: {agent_name}/{session_name}", file=sys.stderr)
        finally:
            backend.close()

        task_status = "done" if exit_code == 0 else "failed"
        add_task(agent_name, session_name, task, task_status)
        complete(agent_name, session_name)
        release(lock_path)
        print(f"[subagents] Session {session_name} completed", file=sys.stderr)
        return exit_code

    if bg:
        _execute_in_background(session_name, lock_path, _execute, output_json=False)
    else:
        sys.exit(_execute())


def _run_with_agent_json(
    agent,
    agent_name: str,
    session_name: str,
    task: str,
    bg: bool,
    backend_name: str,
    backend_override: str | None,
    transport: str | None,
    system_mode: str,
) -> None:
    emitter = JsonlEmitter()
    emitter.agent_start(session_name, agent=agent_name, backend=backend_name)

    if check(session_name):
        emitter.agent_error(session_name, f"session '{session_name}' is already running")
        sys.exit(1)

    lock_path = acquire(session_name)

    def _execute(emit: JsonlEmitter) -> int:
        th = _make_text_handler(emit, session_name)
        backend = _make_backend(backend_override, transport, text_handler=th)
        try:
            existing_sid = get_session_id(agent_name, session_name)
            if existing_sid:
                exit_code = backend.resume_session(existing_sid, task, agent.body or None, system_mode=system_mode)
            else:
                sid, exit_code = backend.create_session(task, agent.body or None, system_mode=system_mode)
                register(agent_name, session_name, sid)
        finally:
            backend.close()

        task_status = "done" if exit_code == 0 else "failed"
        add_task(agent_name, session_name, task, task_status)
        complete(agent_name, session_name)
        emit.agent_done(session_name, exit_code=exit_code)
        emit.close()
        release(lock_path)
        return exit_code

    if bg:
        output_file = _jsonl_output_path(session_name)
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        file_emitter = JsonlEmitter(open(output_file, "w"))
        file_emitter.agent_start(session_name, agent=agent_name, backend=backend_name)
        emitter.close()
        _execute_in_background(session_name, lock_path, lambda: _execute(file_emitter), output_json=True)
    else:
        sys.exit(_execute(emitter))


def _run_no_agent(
    session_name: str,
    task: str,
    bg: bool,
    backend_override: str | None,
    transport: str | None,
    output_json: bool,
) -> None:
    """Run without agent file — create if new, resume if exists."""
    backend_name = backend_override or _detect_backend()

    if output_json:
        _run_no_agent_json(session_name, task, bg, backend_name, backend_override, transport)
        return

    print(f"[subagents] Session name: {session_name}", file=sys.stderr)
    print(f"[subagents] Backend: {backend_name}", file=sys.stderr)

    if check(session_name):
        _fail(f"session '{session_name}' is already running. Wait with: subagents wait {session_name}")

    existing_sid = get_session_id_from_any(session_name)
    lock_path = acquire(session_name)

    def _execute() -> int:
        backend = _make_backend(backend_override, transport)
        try:
            if existing_sid:
                print("[subagents] Resuming existing session...", file=sys.stderr)
                exit_code = backend.resume_session(existing_sid, task)
            else:
                print("[subagents] Creating new session...", file=sys.stderr)
                sid, exit_code = backend.create_session(task)
                register(session_name, session_name, sid)
                print(f"[subagents] Session registered: {session_name}", file=sys.stderr)
        finally:
            backend.close()

        task_status = "done" if exit_code == 0 else "failed"
        add_task(session_name, session_name, task, task_status)
        complete(session_name, session_name)
        release(lock_path)
        print(f"[subagents] Session {session_name} completed", file=sys.stderr)
        return exit_code

    if bg:
        _execute_in_background(session_name, lock_path, _execute, output_json=False)
    else:
        sys.exit(_execute())


def _run_no_agent_json(
    session_name: str,
    task: str,
    bg: bool,
    backend_name: str,
    backend_override: str | None,
    transport: str | None,
) -> None:
    emitter = JsonlEmitter()
    emitter.agent_start(session_name, backend=backend_name)

    if check(session_name):
        emitter.agent_error(session_name, f"session '{session_name}' is already running")
        sys.exit(1)

    existing_sid = get_session_id_from_any(session_name)
    lock_path = acquire(session_name)

    def _execute(emit: JsonlEmitter) -> int:
        th = _make_text_handler(emit, session_name)
        backend = _make_backend(backend_override, transport, text_handler=th)
        try:
            if existing_sid:
                exit_code = backend.resume_session(existing_sid, task)
            else:
                sid, exit_code = backend.create_session(task)
                register(session_name, session_name, sid)
        finally:
            backend.close()

        task_status = "done" if exit_code == 0 else "failed"
        add_task(session_name, session_name, task, task_status)
        complete(session_name, session_name)
        emit.agent_done(session_name, exit_code=exit_code)
        emit.close()
        release(lock_path)
        return exit_code

    if bg:
        output_file = _jsonl_output_path(session_name)
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        file_emitter = JsonlEmitter(open(output_file, "w"))
        file_emitter.agent_start(session_name, backend=backend_name)
        emitter.close()
        _execute_in_background(session_name, lock_path, lambda: _execute(file_emitter), output_json=True)
    else:
        sys.exit(_execute(emitter))


def _jsonl_output_path(session_name: str) -> str:
    return os.path.join(OUTPUT_DIR, f"{session_name}.jsonl")


def _execute_in_background(session_name: str, lock_path: Path, fn, output_json: bool = False) -> None:
    if not hasattr(os, "fork"):
        release(lock_path)
        _fail("Background mode (--bg) requires Unix (os.fork not available on this platform)")

    pid = os.fork()
    if pid == 0:
        os.setsid()
        exit_code = fn()
        os._exit(exit_code)
    else:
        pid_file = lock_path.parent / f"{session_name}.pid"
        pid_file.write_text(str(pid))
        if not output_json:
            output_file = Path(OUTPUT_DIR) / f"{session_name}.out"
            print(f"[subagents] Background task started: {session_name} (pid: {pid})", file=sys.stderr)
            print(f"[subagents] Output: {output_file}", file=sys.stderr)
            print(f"[subagents] Wait with: subagents wait {session_name}", file=sys.stderr)
        release(lock_path)


# ── wait ──────────────────────────────────────────────────────────────────

def cmd_wait(args: list[str]) -> None:
    if not args:
        _fail("Usage: subagents wait <session>")

    output_json = False
    clean = _parse_output_flag(args)
    if clean is not None:
        output_json = True
        args = clean

    if not args:
        _fail("Usage: subagents wait <session>")
    session_name = args[0]

    if output_json:
        _wait_json(session_name)
        return

    output_file = Path(OUTPUT_DIR) / f"{session_name}.out"
    if not check(session_name):
        if output_file.is_file():
            print(output_file.read_text())
            print(f"[subagents] Session {session_name} already completed.", file=sys.stderr)
            return
        _fail(f"session '{session_name}' not found (never started or already cleaned up)")
    print(f"[subagents] Waiting for session '{session_name}'...", file=sys.stderr)
    while check(session_name):
        time.sleep(0.5)
    if output_file.is_file():
        print(output_file.read_text())
    print(f"[subagents] Session {session_name} finished.", file=sys.stderr)


def _wait_json(session_name: str) -> None:
    jsonl_file = _jsonl_output_path(session_name)
    if not check(session_name):
        if os.path.isfile(jsonl_file):
            with open(jsonl_file) as fh:
                for line in fh:
                    sys.stdout.write(line)
            sys.stdout.flush()
            return
        _fail(f"session '{session_name}' not found (never started or already cleaned up)")
    while check(session_name):
        time.sleep(0.5)
    if os.path.isfile(jsonl_file):
        with open(jsonl_file) as fh:
            for line in fh:
                sys.stdout.write(line)
        sys.stdout.flush()


# ── list / status ─────────────────────────────────────────────────────────

def _parse_output_flag(args: list[str]) -> list[str] | None:
    """Parse --output json from args, returning remaining args or None."""
    clean: list[str] = []
    found = False
    i = 0
    while i < len(args):
        if args[i] == "--output":
            i += 1
            if i < len(args):
                if args[i] != "json":
                    _fail(f"--output must be 'json', got '{args[i]}'")
                found = True
            else:
                _fail("--output requires a value")
        else:
            clean.append(args[i])
        i += 1
    return clean if found else None


def cmd_list(args: list[str]) -> None:
    output_json = False
    clean = _parse_output_flag(args)
    if clean is not None:
        output_json = True
        args = clean

    if output_json:
        _list_json()
        return

    agents = list_agents(AGENTS_DIR)
    print("Agents:")
    print("=======")
    if not agents:
        print("  (no agents defined — create .agents/subagents/<agent>.md files)")
        return
    for agent in agents:
        print()
        print(f"  {agent.name}")
        print(f"    description: {agent.description}")
        print(f"    definition: {agent.file_path}")
        sessions = list_sessions(agent.name)
        if sessions:
            print("    sessions:")
            for s in sessions:
                sid = get_session_id(agent.name, s) or "?"
                status = get_session_status(agent.name, s)
                print(f"      - {s} (id: {sid}, status: {status})")
        else:
            print("    sessions: (none)")


def _list_json() -> None:
    agents = list_agents(AGENTS_DIR)
    result: list[dict] = []
    for agent in agents:
        item: dict = {"name": agent.name, "description": agent.description}
        sessions = list_sessions(agent.name)
        if sessions:
            item["sessions"] = []
            for s in sessions:
                sid = get_session_id(agent.name, s) or "?"
                status = get_session_status(agent.name, s)
                item["sessions"].append({"name": s, "id": sid, "status": status})
        result.append(item)
    emitter = JsonlEmitter()
    emitter.agent_list(result)


def cmd_status(args: list[str]) -> None:
    output_json = False
    clean = _parse_output_flag(args)
    if clean is not None:
        output_json = True
        args = clean

    if not args:
        _fail("Usage: subagents status <agent> [session]")

    if output_json:
        _status_json(args)
        return

    agent_name = args[0]
    session_name = args[1] if len(args) > 1 else ""
    agent_path = Path(AGENTS_DIR) / f"{agent_name}.md"
    if not agent_path.is_file():
        print(f"Error: agent '{agent_name}' not found.", file=sys.stderr)
        agents = list_agents(AGENTS_DIR)
        if agents:
            print("Defined agents:", file=sys.stderr)
            for a in agents:
                print(f"  - {a.name}", file=sys.stderr)
        sys.exit(1)
    agent = parse_agent(agent_path)
    print(f"Agent: {agent.name}")
    print(f"  description: {agent.description}")
    print()
    if session_name:
        sid = get_session_id(agent_name, session_name)
        if not sid:
            print(f"Session '{session_name}' not found for agent '{agent_name}'.")
            sys.exit(1)
        status = get_session_status(agent_name, session_name)
        print(f"Session: {session_name}")
        print(f"  id: {sid}")
        print(f"  status: {status}")
        data = get_all_data()
        try:
            tasks = data[agent_name]["sessions"][session_name]["tasks"]
            if tasks:
                print("  tasks:")
                for t in tasks:
                    print(f"    - [{t['status']}] {t['prompt']} ({t['time']})")
        except KeyError:
            pass
    else:
        sessions = list_sessions(agent_name)
        if not sessions:
            print("No sessions.")
        else:
            print("Sessions:")
            for s in sessions:
                sid = get_session_id(agent_name, s) or "?"
                status = get_session_status(agent_name, s)
                print(f"  - {s} (id: {sid}, status: {status})")


def _status_json(args: list[str]) -> None:
    agent_name = args[0]
    session_name = args[1] if len(args) > 1 else ""
    agent_path = Path(AGENTS_DIR) / f"{agent_name}.md"
    if not agent_path.is_file():
        emitter = JsonlEmitter()
        emitter.agent_error("", f"agent '{agent_name}' not found")
        sys.exit(1)

    if session_name:
        sid = get_session_id(agent_name, session_name)
        if not sid:
            emitter = JsonlEmitter()
            emitter.agent_error(session_name, f"session '{session_name}' not found for agent '{agent_name}'")
            sys.exit(1)
        status = get_session_status(agent_name, session_name)
        tasks: list[dict] | None = None
        data = get_all_data()
        try:
            tasks = data[agent_name]["sessions"][session_name]["tasks"]
        except KeyError:
            pass
        emitter = JsonlEmitter()
        emitter.agent_status(agent_name, session_name, status, tasks=tasks)
    else:
        sessions = list_sessions(agent_name)
        if not sessions:
            emitter = JsonlEmitter()
            emitter.agent_status(agent_name, None, "no-sessions")
        else:
            result: list[dict] = []
            for s in sessions:
                sid = get_session_id(agent_name, s) or "?"
                st = get_session_status(agent_name, s)
                result.append({"name": s, "id": sid, "status": st})
            emitter = JsonlEmitter()
            emitter.agent_status(agent_name, None, "ok", tasks=result)


# ── main ──────────────────────────────────────────────────────────────────

def main() -> None:
    args = sys.argv[1:]
    if not args:
        _print_help()
        sys.exit(1)
    cmd = args.pop(0)
    if cmd in ("-h", "--help", "help"):
        _print_help()
        sys.exit(0)
    elif cmd == "run":
        cmd_run(args)
    elif cmd == "wait":
        cmd_wait(args)
    elif cmd == "list":
        cmd_list(args)
    elif cmd == "status":
        cmd_status(args)
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)


def _print_help() -> None:
    print("Usage: subagents <command> [args...]", file=sys.stderr)
    print(file=sys.stderr)
    print("Commands:", file=sys.stderr)
    print("  run [--bg] [--backend <name>] [--transport cli|acp] [--system-mode append|overwrite] [--output json] <agent> <session> <prompt>", file=sys.stderr)
    print("  run [--bg] [--output json] <session> <prompt>", file=sys.stderr)
    print("  wait [--output json] <session>", file=sys.stderr)
    print("  list [--output json]", file=sys.stderr)
    print("  status [--output json] <agent> [session]", file=sys.stderr)


if __name__ == "__main__":
    main()