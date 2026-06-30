"""CLI argument parsing and command routing for subagent."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


class _CRLFWrapper:
    """Wrap a stream to ensure \r before every \n for proper terminal rendering."""

    def __init__(self, stream):
        self._stream = stream

    def write(self, s: str) -> int:
        return self._stream.write(s.replace("\n", "\r\n"))

    def flush(self) -> None:
        self._stream.flush()

    def __getattr__(self, name):
        return getattr(self._stream, name)


sys.stderr = _CRLFWrapper(sys.stderr)
sys.stdout = _CRLFWrapper(sys.stdout)
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
    cancel_goal,
    clear_goal,
    complete,
    get_all_data,
    get_goal,
    get_session_data,
    get_session_id,
    get_session_id_from_any,
    get_session_status,
    has_active_goal,
    has_active_queue,
    list_sessions,
    mark_goal_complete,
    mark_goal_failed,
    register,
    set_goal,
    update_goal_iteration,
)

if TYPE_CHECKING:
    from backends.base import BaseBackend

AGENTS_DIR = os.path.abspath(os.environ.get("SUBAGENT_AGENTS_DIR", ".agents/subagent"))
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
    cwd: str | None = None
    model: str | None = None

    while args and args[0].startswith("--"):
        flag = args.pop(0)
        if flag == "--bg":
            bg = True
        elif flag == "--cwd":
            cwd = args.pop(0) if args else _fail("--cwd requires a value")
            # Convert to absolute path
            cwd = os.path.abspath(cwd)
        elif flag == "--backend":
            backend_override = args.pop(0) if args else _fail("--backend requires a value")
        elif flag == "--model":
            model = args.pop(0) if args else _fail("--model requires a value")
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
        _fail("Usage: subagents run [--bg] [--cwd <path>] [--backend <name>] <agent> <session> <prompt>")

    arg1 = args[0]
    agent_path = Path(AGENTS_DIR) / f"{arg1}.md"

    if agent_path.is_file():
        agent_name = args.pop(0)
        session_name = args.pop(0) if args else ""
        task = " ".join(args) if args else ""
        if not session_name or not task:
            _fail("Usage: subagents run [--bg] [--cwd <path>] <agent> <session> <prompt>")
        _run_with_agent(agent_name, session_name, task, bg, backend_override, transport, system_mode, output_json, cwd, model)
    else:
        session_name = args.pop(0)
        task = " ".join(args) if args else ""
        if not task:
            _fail("Usage: subagents run [--bg] [--cwd <path>] <session> <prompt>")
        _run_no_agent(session_name, task, bg, backend_override, transport, output_json, cwd, model)


def _run_with_agent(
    agent_name: str,
    session_name: str,
    task: str,
    bg: bool,
    backend_override: str | None,
    transport: str | None,
    system_mode: str,
    output_json: bool,
    cwd: str | None = None,
    model: str | None = None,
) -> None:
    agent = parse_agent(Path(AGENTS_DIR) / f"{agent_name}.md")
    backend_name = backend_override or _detect_backend()

    if output_json:
        _run_with_agent_json(agent, agent_name, session_name, task, bg, backend_name, backend_override, transport, system_mode, cwd, model)
        return

    print(f"[subagents] Agent: {agent.name} — {agent.description}", file=sys.stderr)
    print(f"[subagents] Session name: {session_name}", file=sys.stderr)
    print(f"[subagents] Backend: {backend_name}", file=sys.stderr)
    if cwd:
        print(f"[subagents] Working directory: {cwd}", file=sys.stderr)

    if check(session_name):
        _fail(f"session '{session_name}' is already running. Wait with: subagents wait {session_name}")

    # Goal and direct run are mutually exclusive
    if has_active_goal(agent_name, session_name):
        _fail(f"session '{session_name}' has an active goal. Cancel it first with: subagents goal --clear {agent_name} {session_name}")

    lock_path = acquire(session_name)

    def _execute() -> int:
        # Change to specified working directory if provided
        original_cwd = os.getcwd()
        if cwd:
            os.chdir(cwd)

        try:
            backend = _make_backend(backend_override, transport)
            try:
                existing_sid = get_session_id(agent_name, session_name)
                if existing_sid:
                    print("[subagents] Resuming existing session...", file=sys.stderr)
                    exit_code = backend.resume_session(existing_sid, task, agent.body or None, model=model, system_mode=system_mode)
                else:
                    print("[subagents] Creating new session...", file=sys.stderr)
                    sid, exit_code = backend.create_session(task, agent.body or None, model=model, system_mode=system_mode)
                    register(agent_name, session_name, sid, cwd=cwd, background=bg)
                    print(f"[subagents] Session registered: {agent_name}/{session_name}", file=sys.stderr)
            finally:
                backend.close()

            task_status = "done" if exit_code == 0 else "failed"
            add_task(agent_name, session_name, task, task_status)
            complete(agent_name, session_name)
            release(lock_path)
            print(f"[subagents] Session {session_name} completed", file=sys.stderr)
            return exit_code
        finally:
            # Restore original working directory
            os.chdir(original_cwd)

    if bg:
        _execute_in_background(session_name, lock_path, _execute, output_json=False, agent_name=agent_name, queue_mode=True, cwd=cwd, model=model)
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
    cwd: str | None = None,
    model: str | None = None,
) -> None:
    emitter = JsonlEmitter()
    emitter.agent_start(session_name, agent=agent_name, backend=backend_name)

    if check(session_name):
        emitter.agent_error(session_name, f"session '{session_name}' is already running")
        sys.exit(1)

    lock_path = acquire(session_name)

    def _execute(emit: JsonlEmitter) -> int:
        # Change to specified working directory if provided
        original_cwd = os.getcwd()
        if cwd:
            os.chdir(cwd)

        try:
            th = _make_text_handler(emit, session_name)
            backend = _make_backend(backend_override, transport, text_handler=th)
            try:
                existing_sid = get_session_id(agent_name, session_name)
                if existing_sid:
                    exit_code = backend.resume_session(existing_sid, task, agent.body or None, model=model, system_mode=system_mode)
                else:
                    sid, exit_code = backend.create_session(task, agent.body or None, model=model, system_mode=system_mode)
                    register(agent_name, session_name, sid, cwd=cwd, background=bg)
            finally:
                backend.close()

            task_status = "done" if exit_code == 0 else "failed"
            add_task(agent_name, session_name, task, task_status)
            complete(agent_name, session_name)
            emit.agent_done(session_name, exit_code=exit_code)
            emit.close()
            release(lock_path)
            return exit_code
        finally:
            # Restore original working directory
            os.chdir(original_cwd)

    if bg:
        output_file = _jsonl_output_path(session_name)
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        file_emitter = JsonlEmitter(open(output_file, "w"))
        file_emitter.agent_start(session_name, agent=agent_name, backend=backend_name)
        emitter.close()
        _execute_in_background(session_name, lock_path, lambda: _execute(file_emitter), output_json=True, agent_name=agent_name, queue_mode=True, cwd=cwd, model=model)
    else:
        sys.exit(_execute(emitter))


def _run_no_agent(
    session_name: str,
    task: str,
    bg: bool,
    backend_override: str | None,
    transport: str | None,
    output_json: bool,
    cwd: str | None = None,
    model: str | None = None,
) -> None:
    """Run without agent file — create if new, resume if exists."""
    backend_name = backend_override or _detect_backend()

    if output_json:
        _run_no_agent_json(session_name, task, bg, backend_name, backend_override, transport, cwd, model)
        return

    print(f"[subagents] Session name: {session_name}", file=sys.stderr)
    print(f"[subagents] Backend: {backend_name}", file=sys.stderr)
    if cwd:
        print(f"[subagents] Working directory: {cwd}", file=sys.stderr)

    if check(session_name):
        _fail(f"session '{session_name}' is already running. Wait with: subagents wait {session_name}")

    # Goal and direct run are mutually exclusive
    from registry import find_agent_for_session
    owner = find_agent_for_session(session_name)
    if owner and has_active_goal(owner, session_name):
        _fail(f"session '{session_name}' has an active goal. Cancel it first with: subagents goal --clear {owner} {session_name}")

    existing_sid = get_session_id_from_any(session_name)
    lock_path = acquire(session_name)

    def _execute() -> int:
        # Change to specified working directory if provided
        original_cwd = os.getcwd()
        if cwd:
            os.chdir(cwd)

        try:
            backend = _make_backend(backend_override, transport)
            try:
                if existing_sid:
                    print("[subagents] Resuming existing session...", file=sys.stderr)
                    exit_code = backend.resume_session(existing_sid, task, model=model)
                else:
                    print("[subagents] Creating new session...", file=sys.stderr)
                    sid, exit_code = backend.create_session(task, model=model)
                    register(session_name, session_name, sid, cwd=cwd, background=bg)
                    print(f"[subagents] Session registered: {session_name}", file=sys.stderr)
            finally:
                backend.close()

            task_status = "done" if exit_code == 0 else "failed"
            add_task(session_name, session_name, task, task_status)
            complete(session_name, session_name)
            release(lock_path)
            print(f"[subagents] Session {session_name} completed", file=sys.stderr)
            return exit_code
        finally:
            # Restore original working directory
            os.chdir(original_cwd)

    if bg:
        _execute_in_background(session_name, lock_path, _execute, output_json=False, agent_name=session_name, queue_mode=True, cwd=cwd, model=model)
    else:
        sys.exit(_execute())


def _run_no_agent_json(
    session_name: str,
    task: str,
    bg: bool,
    backend_name: str,
    backend_override: str | None,
    transport: str | None,
    cwd: str | None = None,
    model: str | None = None,
) -> None:
    emitter = JsonlEmitter()
    emitter.agent_start(session_name, backend=backend_name)

    if check(session_name):
        emitter.agent_error(session_name, f"session '{session_name}' is already running")
        sys.exit(1)

    existing_sid = get_session_id_from_any(session_name)
    lock_path = acquire(session_name)

    def _execute(emit: JsonlEmitter) -> int:
        # Change to specified working directory if provided
        original_cwd = os.getcwd()
        if cwd:
            os.chdir(cwd)

        try:
            th = _make_text_handler(emit, session_name)
            backend = _make_backend(backend_override, transport, text_handler=th)
            try:
                if existing_sid:
                    exit_code = backend.resume_session(existing_sid, task)
                else:
                    sid, exit_code = backend.create_session(task)
                    register(session_name, session_name, sid, cwd=cwd, background=bg)
            finally:
                backend.close()

            task_status = "done" if exit_code == 0 else "failed"
            add_task(session_name, session_name, task, task_status)
            complete(session_name, session_name)
            emit.agent_done(session_name, exit_code=exit_code)
            emit.close()
            release(lock_path)
            return exit_code
        finally:
            # Restore original working directory
            os.chdir(original_cwd)

    if bg:
        output_file = _jsonl_output_path(session_name)
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        file_emitter = JsonlEmitter(open(output_file, "w"))
        file_emitter.agent_start(session_name, backend=backend_name)
        emitter.close()
        _execute_in_background(session_name, lock_path, lambda: _execute(file_emitter), output_json=True, agent_name=session_name, queue_mode=True, cwd=cwd, model=model)
    else:
        sys.exit(_execute(emitter))


def _jsonl_output_path(session_name: str) -> str:
    return os.path.join(OUTPUT_DIR, f"{session_name}.jsonl")


def _queue_worker(agent_name: str, session_name: str, initial_task_fn, cwd: str | None, model: str | None) -> int:
    """Background worker that processes initial task and then queue.

    Returns exit code of last executed task.
    """
    from registry import dequeue_task, set_current_task, get_session_data

    # Execute initial task
    exit_code = initial_task_fn()

    # Process queue
    while True:
        # Check if there are more tasks in queue
        task = dequeue_task(agent_name, session_name)
        if task is None:
            # Queue is empty, exit
            break

        # Update current_task in registry
        task["status"] = "running"
        task["start_time"] = datetime.now(timezone.utc).isoformat()
        set_current_task(agent_name, session_name, task)

        # Change to cwd if specified
        if cwd:
            os.chdir(cwd)

        # Execute the queued task
        from registry import get_session_id, add_task, complete

        print(f"[subagents] Processing queued task: {task['id']}", file=sys.stderr)

        # Get session data to resume
        session_data = get_session_data(agent_name, session_name)
        if not session_data:
            print(f"[subagents] Session data lost, aborting", file=sys.stderr)
            exit_code = 1
            break

        sid = session_data["session_id"]

        # Create backend and execute task
        backend = _make_backend(None, None)  # Use defaults
        try:
            exit_code = backend.resume_session(sid, task["prompt"], model=model)
        except Exception as e:
            print(f"[subagents] Task failed: {e}", file=sys.stderr)
            exit_code = 1
        finally:
            backend.close()

        # Record task completion
        task_status = "done" if exit_code == 0 else "failed"
        task_elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(task["start_time"])).total_seconds()
        add_task(agent_name, session_name, task["prompt"], task_status)

        # Clear current_task
        set_current_task(agent_name, session_name, None)

        print(f"[subagents] Task {task['id']} {task_status} ({task_elapsed:.1f}s)", file=sys.stderr)

    return exit_code


def _execute_in_background(session_name: str, lock_path: Path, fn, output_json: bool = False, agent_name: str | None = None, queue_mode: bool = False, cwd: str | None = None, model: str | None = None) -> None:
    if not hasattr(os, "fork"):
        release(lock_path)
        _fail("Background mode (--bg) requires Unix (os.fork not available on this platform)")

    pid = os.fork()
    if pid == 0:
        os.setsid()

        # If queue mode, wrap fn with queue worker
        if queue_mode and agent_name:
            exit_code = _queue_worker(agent_name, session_name, fn, cwd, model)
        else:
            exit_code = fn()

        os._exit(exit_code)
    else:
        pid_file = lock_path.parent / f"{session_name}.pid"
        pid_file.write_text(str(pid))
        if not output_json:
            output_file = Path(OUTPUT_DIR) / f"{session_name}.out"
            print(f"[subagents] Background task started: {session_name} (pid: {pid})", file=sys.stderr)
            print(f"[subagents] Output: {output_file}", file=sys.stderr)
            if queue_mode:
                print(f"[subagents] Queue mode enabled. Send more tasks with: subagents send {session_name} <prompt>", file=sys.stderr)
            print(f"[subagents] Wait with: subagents wait {session_name}", file=sys.stderr)
        release(lock_path)



# ── send ──────────────────────────────────────────────────────────────────

def cmd_send(args: list[str]) -> None:
    """Send a task to a background session's queue."""
    prompt_file: str | None = None

    # Parse flags
    while args and args[0].startswith("--"):
        flag = args.pop(0)
        if flag == "--prompt-file":
            prompt_file = args.pop(0) if args else _fail("--prompt-file requires a value")
        else:
            _fail(f"unknown flag {flag}")

    if not args:
        _fail("Usage: subagents send [--prompt-file <file>] <session> [prompt]")

    session_name = args.pop(0)

    # Determine prompt source (priority: --prompt-file > args > stdin)
    prompt_parts = []

    if prompt_file:
        # Read from file
        try:
            with open(prompt_file, 'r') as f:
                prompt_parts.append(f.read().strip())
        except IOError as e:
            _fail(f"Failed to read prompt file: {e}")

    # Add command line args
    if args:
        prompt_parts.append(" ".join(args))

    # Add stdin if available (non-blocking check)
    if not sys.stdin.isatty():
        import select
        if select.select([sys.stdin], [], [], 0)[0]:
            stdin_content = sys.stdin.read().strip()
            if stdin_content:
                prompt_parts.append(stdin_content)

    if not prompt_parts:
        _fail("No prompt provided (use --prompt-file, command args, or stdin)")

    prompt = "\n".join(prompt_parts)

    # Find which agent owns this session
    from registry import find_agent_for_session, enqueue_task, get_session_data

    agent_name = find_agent_for_session(session_name)
    if not agent_name:
        _fail(f"Session '{session_name}' not found")

    # Verify it's a background session
    session_data = get_session_data(agent_name, session_name)
    if not session_data or session_data.get("mode") != "background":
        _fail(f"Session '{session_name}' is not a background session. Use 'run --bg' to start background sessions.")

    # Goal and queue are mutually exclusive
    if has_active_goal(agent_name, session_name):
        _fail(f"Session '{session_name}' has an active goal. Cancel it first with: subagents goal --clear {agent_name} {session_name}")

    # Enqueue the task
    task_id = enqueue_task(agent_name, session_name, prompt)
    if task_id:
        # Show task preview
        prompt_preview = prompt[:60] + "..." if len(prompt) > 60 else prompt
        print(f"Task queued", file=sys.stderr)
        print(f"  Session: {session_name}", file=sys.stderr)
        print(f"  Task ID: {task_id}", file=sys.stderr)
        print(f"  Prompt:  {prompt_preview}", file=sys.stderr)

        # Show queue status
        queue_len = len(session_data.get("queue", [])) + 1  # +1 for the task we just added
        current = session_data.get("current_task")
        if current:
            print(f"  Status:  Task is #{queue_len} in queue (worker is busy)", file=sys.stderr)
        else:
            print(f"  Status:  Task is #{queue_len} in queue (worker will pick up soon)", file=sys.stderr)
    else:
        _fail(f"Failed to enqueue task for session '{session_name}'")


# ── cancel ────────────────────────────────────────────────────────────────

def cmd_cancel(args: list[str]) -> None:
    """Cancel queued tasks or goal in a background session."""
    cancel_all = False
    task_index: int | None = None
    cancel_goal_flag = False

    # Parse flags
    while args and args[0].startswith("--"):
        flag = args.pop(0)
        if flag == "--all":
            cancel_all = True
        elif flag == "--goal":
            cancel_goal_flag = True
        elif flag == "--task":
            task_str = args.pop(0) if args else _fail("--task requires a value")
            try:
                task_index = int(task_str) - 1  # Convert to 0-based index
                if task_index < 0:
                    _fail("--task index must be >= 1")
            except ValueError:
                _fail(f"--task must be a number, got '{task_str}'")
        else:
            _fail(f"unknown flag {flag}")

    if not args:
        _fail("Usage: subagents cancel [--all | --task N | --goal] <session>")

    session_name = args.pop(0)

    # Find which agent owns this session
    from registry import find_agent_for_session, cancel_task, get_session_data

    agent_name = find_agent_for_session(session_name)
    if not agent_name:
        _fail(f"Session '{session_name}' not found")

    # Cancel goal if requested
    if cancel_goal_flag:
        from registry import cancel_goal as cancel_goal_fn
        if cancel_goal_fn(agent_name, session_name):
            print(f"Goal cancelled", file=sys.stderr)
            print(f"  Session: {session_name}", file=sys.stderr)
            print(f"  The worker will stop after the current iteration", file=sys.stderr)
        else:
            print(f"No active goal", file=sys.stderr)
            print(f"  Session: {session_name}", file=sys.stderr)
        return

    # Get queue status before cancel
    session_data = get_session_data(agent_name, session_name)
    if not session_data or session_data.get("mode") != "background":
        _fail(f"Session '{session_name}' is not a background session")

    queue_before = len(session_data.get("queue", []))

    # Cancel tasks
    count = cancel_task(agent_name, session_name, task_index=task_index, cancel_all=cancel_all)

    # Pretty output
    if cancel_all:
        if count > 0:
            print(f"Cancelled all queued tasks", file=sys.stderr)
            print(f"  Session: {session_name}", file=sys.stderr)
            print(f"  Removed: {count} task(s)", file=sys.stderr)
        else:
            print(f"No tasks to cancel", file=sys.stderr)
            print(f"  Session: {session_name}", file=sys.stderr)
            print(f"  Queue:   empty", file=sys.stderr)
    elif task_index is not None:
        if count > 0:
            print(f"Task cancelled", file=sys.stderr)
            print(f"  Session: {session_name}", file=sys.stderr)
            print(f"  Task:    #{task_index + 1}", file=sys.stderr)
            print(f"  Queue:   {queue_before - count} task(s) remaining", file=sys.stderr)
        else:
            print(f"Task not found", file=sys.stderr)
            print(f"  Session: {session_name}", file=sys.stderr)
            print(f"  Task:    #{task_index + 1}", file=sys.stderr)
            print(f"  Queue:   only {queue_before} task(s) available", file=sys.stderr)
    else:
        print(f"Cancelled {count} task(s)", file=sys.stderr)
        print(f"  Session: {session_name}", file=sys.stderr)


# ── goal ──────────────────────────────────────────────────────────────────

def cmd_goal(args: list[str]) -> None:
    """Set, show, or clear a goal on a session.

    Usage:
        subagents goal <agent> <session> "<goal>" [--max-iterations N]
        subagents goal --show <agent> <session>
        subagents goal --clear <agent> <session>
    """
    clear = False
    show = False
    max_iterations = 10
    cwd: str | None = None
    backend_override: str | None = None
    transport: str | None = None
    model: str | None = None

    while args and args[0].startswith("--"):
        flag = args.pop(0)
        if flag == "--clear":
            clear = True
        elif flag == "--show":
            show = True
        elif flag == "--max-iterations":
            val = args.pop(0) if args else _fail("--max-iterations requires a value")
            try:
                max_iterations = int(val)
                if max_iterations < 1:
                    _fail("--max-iterations must be >= 1")
            except ValueError:
                _fail(f"--max-iterations must be a number, got '{val}'")
        elif flag == "--cwd":
            cwd = args.pop(0) if args else _fail("--cwd requires a value")
            cwd = os.path.abspath(cwd)
        elif flag == "--model":
            model = args.pop(0) if args else _fail("--model requires a value")
        elif flag == "--backend":
            backend_override = args.pop(0) if args else _fail("--backend requires a value")
        elif flag == "--transport":
            transport = args.pop(0) if args else _fail("--transport requires a value")
            if transport not in ("cli", "acp"):
                _fail(f"--transport must be 'cli' or 'acp', got '{transport}'")
        else:
            _fail(f"unknown flag {flag}")

    if len(args) < 2:
        _fail("Usage: subagents goal [--clear|--show] [--max-iterations N] [--cwd <path>] <agent> <session> [goal]")

    agent_name = args[0]
    session_name = args[1]
    goal_text = " ".join(args[2:]) if len(args) > 2 else ""

    # Verify agent exists
    agent_path = Path(AGENTS_DIR) / f"{agent_name}.md"
    if not agent_path.is_file():
        _fail(f"agent '{agent_name}' not found (create .agents/subagent/{agent_name}.md first)")

    # --clear: cancel active goal
    if clear:
        from registry import cancel_goal as cancel_goal_fn
        if cancel_goal_fn(agent_name, session_name):
            print(f"Goal cancelled", file=sys.stderr)
            print(f"  Session: {session_name}", file=sys.stderr)
            print(f"  The worker will stop after the current iteration", file=sys.stderr)
        else:
            print(f"No active goal to cancel", file=sys.stderr)
            print(f"  Session: {session_name}", file=sys.stderr)
        return

    # --show or no goal text: display current goal
    if show or not goal_text:
        goal = get_goal(agent_name, session_name)
        if not goal:
            print(f"No active goal for {agent_name}/{session_name}", file=sys.stderr)
        else:
            status_icon = {"active": ">", "completed": "+", "failed": "x", "cancelled": "-"}.get(goal["status"], "?")
            print(f"Goal: {goal['text']}", file=sys.stderr)
            print(f"  Status:   {status_icon} {goal['status']}", file=sys.stderr)
            print(f"  Progress: {goal['current_iteration']}/{goal['max_iterations']} iterations", file=sys.stderr)
            if goal.get("failed_reason"):
                print(f"  Reason:   {goal['failed_reason']}", file=sys.stderr)
        return

    # Set goal: handle new or existing session
    session_data = get_session_data(agent_name, session_name)
    sid = get_session_id(agent_name, session_name)
    is_new_session = not session_data or not sid

    if is_new_session:
        # Check for lock conflicts
        if check(session_name):
            _fail(f"session '{session_name}' is already running. Wait for it to finish first.")
        # Use --cwd if passed, otherwise default to current directory
        if not cwd:
            cwd = os.getcwd()
    else:
        # Check for queue conflict
        if has_active_queue(agent_name, session_name):
            _fail(f"Session '{session_name}' has an active task queue. Goal and queue are mutually exclusive.")

        # Check if session has a running background worker (pid file)
        lock_dir = Path(os.environ.get("SU BAGENT_LOCKS", ".agents/subagents/locks"))
        pid_file = lock_dir / f"{session_name}.pid"
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                os.kill(pid, 0)
                _fail(f"Session '{session_name}' has a running background worker (pid {pid}). Stop it first.")
            except (OSError, ValueError):
                pid_file.unlink()

        # Check if session is locked (running)
        if check(session_name):
            _fail(f"session '{session_name}' is already running. Wait for it to finish first.")

        # If --cwd not explicitly passed, inherit from session
        if not cwd:
            cwd = session_data.get("cwd")

    # Check for existing goal
    existing = get_goal(agent_name, session_name)
    if existing and existing.get("status") == "active":
        _fail(f"Session '{session_name}' already has an active goal. Clear it first with: subagents goal --clear {agent_name} {session_name}")

    # For new sessions, create a placeholder registry entry so set_goal works
    if is_new_session:
        register(agent_name, session_name, "", cwd=cwd, background=False)

    # Set goal in registry
    if not set_goal(agent_name, session_name, goal_text, max_iterations):
        _fail(f"Failed to set goal on session '{session_name}'")

    agent = parse_agent(agent_path)
    backend_name = backend_override or _detect_backend()

    print(f"[subagents] Goal set: {goal_text[:60]}{'...' if len(goal_text) > 60 else ''}", file=sys.stderr)
    print(f"[subagents] Session: {session_name}{' (new)' if is_new_session else ''}", file=sys.stderr)
    print(f"[subagents] Max iterations: {max_iterations}", file=sys.stderr)
    if cwd:
        print(f"[subagents] Working directory: {cwd}", file=sys.stderr)

    # Acquire lock and start goal worker in background
    lock_path = acquire(session_name)

    def _goal_fn() -> int:
        return _goal_worker(
            agent_name=agent_name,
            session_name=session_name,
            session_id=sid,  # None for new sessions
            goal_text=goal_text,
            max_iterations=max_iterations,
            agent_body=agent.body,
            backend_override=backend_override,
            transport=transport,
            cwd=cwd,
            model=model,
        )

    _execute_in_background(session_name, lock_path, _goal_fn, output_json=False, agent_name=agent_name, queue_mode=False, cwd=cwd, model=model)


def _goal_worker(
    agent_name: str,
    session_name: str,
    session_id: str | None,
    goal_text: str,
    max_iterations: int,
    agent_body: str | None,
    backend_override: str | None,
    transport: str | None,
    cwd: str | None,
    model: str | None = None,
) -> int:
    """Goal worker: runs the agent in a loop until goal is met or max iterations reached.

    session_id may be None for new sessions; the worker creates the session on first iteration.
    Detection: after each iteration, checks a marker file for <GOAL_MET>.
    """
    marker_dir = Path(AGENTS_DIR) / "goals"
    marker_dir.mkdir(parents=True, exist_ok=True)
    marker_path = marker_dir / f"{session_name}.met"

    # Build the goal prompt template
    def _build_prompt(iteration: int) -> str:
        return (
            f"GOAL: {goal_text}\n\n"
            f"Iteration {iteration}/{max_iterations}\n\n"
            f"Work toward the goal above. You can see previous work from prior iterations "
            f"in this session. When the goal is FULLY accomplished, write exactly "
            f"<GOAL_MET> to the file: {marker_path}\n\n"
            f"If the goal is not yet met, make as much progress as you can in this "
            f"iteration. The next iteration will continue from where you left off."
        )

    exit_code = 0
    for iteration in range(1, max_iterations + 1):
        # Check if goal was cancelled
        goal = get_goal(agent_name, session_name)
        if not goal or goal.get("status") == "cancelled":
            print(f"[subagents] Goal cancelled at iteration {iteration}", file=sys.stderr)
            update_goal_iteration(agent_name, session_name, iteration - 1, "cancelled")
            return 0

        # Update registry with current iteration
        update_goal_iteration(agent_name, session_name, iteration, "active")

        # Delete any existing marker file before this iteration
        if marker_path.exists():
            marker_path.unlink()

        # Change to cwd if specified
        original_cwd = os.getcwd()
        if cwd:
            os.chdir(cwd)

        try:
            prompt = _build_prompt(iteration)
            print(f"[subagents] Goal iteration {iteration}/{max_iterations}...", file=sys.stderr)

            backend = _make_backend(backend_override, transport)
            try:
                if not session_id:
                    # New session: create on first iteration
                    session_id, exit_code = backend.create_session(prompt, agent_body, model=model, system_mode="append")
                    register(agent_name, session_name, session_id, cwd=cwd, background=False)
                    print(f"[subagents] Session created: {agent_name}/{session_name}", file=sys.stderr)
                else:
                    exit_code = backend.resume_session(session_id, prompt, agent_body, model=model, system_mode="append")
            finally:
                backend.close()

            # Check for GOAL_MET marker
            if marker_path.exists():
                content = marker_path.read_text()
                if "<GOAL_MET>" in content:
                    print(f"[subagents] Goal met at iteration {iteration}!", file=sys.stderr)
                    add_task(agent_name, session_name, goal_text, "done")
                    mark_goal_complete(agent_name, session_name, iteration)
                    complete(agent_name, session_name)
                    return 0

            # Record task for this iteration
            add_task(agent_name, session_name, f"[goal:{iteration}/{max_iterations}] {goal_text[:80]}", "done" if exit_code == 0 else "failed")

        except Exception as e:
            print(f"[subagents] Goal iteration {iteration} error: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
            exit_code = 1
        finally:
            os.chdir(original_cwd)

    # Max iterations reached
    print(f"[subagents] Goal max iterations ({max_iterations}) reached", file=sys.stderr)
    mark_goal_failed(agent_name, session_name, max_iterations, "max_iterations")
    complete(agent_name, session_name)
    return exit_code


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

    # Find agent for this session to show queue progress
    from registry import find_agent_for_session, get_session_data

    agent_name = find_agent_for_session(session_name)
    last_status = ""
    is_tty = sys.stderr.isatty()

    print(f"Waiting for session '{session_name}'...", file=sys.stderr)

    while check(session_name):
        # Only show live progress in TTY mode
        if is_tty and agent_name:
            session_data = get_session_data(agent_name, session_name)
            if session_data and session_data.get("mode") == "background":
                current = session_data.get("current_task")
                queue = session_data.get("queue", [])
                completed = len(session_data.get("tasks", []))

                if current:
                    prompt = current.get('prompt', 'N/A')[:40] + "..."
                    status = f"  > {prompt} | Queue: {len(queue)} | Done: {completed}"
                elif queue:
                    status = f"  {len(queue)} task{'s' if len(queue) != 1 else ''} remaining | Done: {completed}"
                else:
                    status = f"  Finishing up... | Done: {completed}"

                if status != last_status:
                    print(f"{status}", file=sys.stderr)
                    last_status = status

        time.sleep(1)

    if output_file.is_file():
        print(output_file.read_text())
    print(f"Session '{session_name}' finished.", file=sys.stderr)


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
        if sid is None:
            print(f"Session '{session_name}' not found for agent '{agent_name}'.")
            sys.exit(1)

        if not sid:
            # Session exists but backend hasn't created it yet (goal worker just started)
            print(f"Session '{session_name}' is initializing...")
            print(f"  Agent:  {agent.name}")
            print(f"  Status: initializing")
            sys.exit(0)
        status = get_session_status(agent_name, session_name)

        # Simple header
        print(f"Session: {session_name}")
        print(f"  Agent:  {agent.name}")
        print(f"  ID:     {sid}")
        print(f"  Status: {status}")

        data = get_all_data()
        try:
            session_data = data[agent_name]["sessions"][session_name]

            # Show cwd if present
            if "cwd" in session_data:
                print(f"  CWD:    {session_data['cwd']}")

            # Show current task if running
            current_task = session_data.get("current_task")
            if current_task:
                prompt = current_task.get('prompt', 'N/A')
                if len(prompt) > 70:
                    prompt = prompt[:67] + "..."
                print(f"\n  > Current: {prompt}")
                if "start_time" in current_task:
                    print(f"    Started: {current_task['start_time']}")

            # Show queue if present
            queue = session_data.get("queue", [])
            if queue:
                print(f"\n  Queue: {len(queue)} task{'s' if len(queue) != 1 else ''}")
                for i, task in enumerate(queue[:5], 1):  # Show first 5
                    prompt = task.get("prompt", "N/A")
                    if len(prompt) > 65:
                        prompt = prompt[:62] + "..."
                    print(f"     {i}. {prompt}")
                if len(queue) > 5:
                    print(f"     ... and {len(queue) - 5} more")

            # Show completed tasks history
            tasks = session_data.get("tasks", [])
            if tasks:
                print(f"\n  Completed: {len(tasks)} task{'s' if len(tasks) != 1 else ''}")
                for t in tasks[-3:]:  # Show last 3
                    prompt = t['prompt']
                    if len(prompt) > 60:
                        prompt = prompt[:57] + "..."
                    status_icon = "+" if t['status'] == 'done' else "x"
                    print(f"     {status_icon} {prompt}")
                if len(tasks) > 3:
                    print(f"     ... and {len(tasks) - 3} more")

            # Show goal if present
            goal = session_data.get("goal")
            if goal:
                print(f"\n  Goal: {goal['text']}")
                print(f"    Status:   {goal['status']}")
                print(f"    Progress: {goal['current_iteration']}/{goal['max_iterations']} iterations")
                if goal.get("failed_reason"):
                    print(f"    Reason:   {goal['failed_reason']}")
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
        data = get_all_data()

        # Build status response with extended fields
        status_data: dict = {"status": status}
        try:
            session_data = data[agent_name]["sessions"][session_name]
            if "cwd" in session_data:
                status_data["cwd"] = session_data["cwd"]
            if "current_task" in session_data:
                status_data["current_task"] = session_data["current_task"]
            if "queue" in session_data:
                status_data["queue"] = session_data["queue"]
            if "tasks" in session_data:
                status_data["tasks"] = session_data["tasks"]
            if "goal" in session_data:
                status_data["goal"] = session_data["goal"]
        except KeyError:
            pass

        emitter = JsonlEmitter()
        emitter.emit(type="agent_status", agent=agent_name, session=session_name, **status_data)
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
    elif cmd == "send":
        cmd_send(args)
    elif cmd == "cancel":
        cmd_cancel(args)
    elif cmd == "goal":
        cmd_goal(args)
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
    print("  run [--bg] [--cwd <path>] [--backend <name>] [--model <name>] [--transport cli|acp] [--system-mode append|overwrite] [--output json] <agent> <session> <prompt>", file=sys.stderr)
    print("  run [--bg] [--cwd <path>] [--output json] <session> <prompt>", file=sys.stderr)
    print("  send [--prompt-file <file>] <session> [prompt]    # Add task to background session queue", file=sys.stderr)
    print("  cancel [--all | --task N | --goal] <session>          # Cancel queued tasks or goal", file=sys.stderr)
    print("  goal [--clear|--show] [--max-iterations N] [--cwd <path>] [--model <name>] <agent> <session> [goal]", file=sys.stderr)
    print("  wait [--output json] <session>", file=sys.stderr)
    print("  list [--output json]", file=sys.stderr)
    print("  status [--output json] <agent> [session]", file=sys.stderr)


if __name__ == "__main__":
    main()