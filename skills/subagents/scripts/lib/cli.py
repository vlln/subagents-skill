"""CLI argument parsing and command routing for subagent."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from agent import list_agents, parse_agent
from backends.claude import ClaudeBackend
from backends.codex import CodexBackend
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
}


def _detect_backend() -> str:
    import shutil
    for name in ["kimi", "kiro-cli", "claude", "codex", "pi"]:
        if shutil.which(name):
            return "kiro" if name == "kiro-cli" else name
    return "kimi"


def _make_backend(backend_name: str | None, transport: str | None) -> BaseBackend:
    if backend_name is None:
        backend_name = _detect_backend()
    cls = BACKEND_MAP.get(backend_name)
    if cls is None:
        print(f"Error: unknown backend '{backend_name}'.", file=sys.stderr)
        print(f"Available backends: {', '.join(BACKEND_MAP.keys())}", file=sys.stderr)
        sys.exit(1)
    import inspect
    if "transport" in inspect.signature(cls).parameters:
        return cls(transport=transport)
    return cls()


def cmd_run(args: list[str]) -> None:
    """Run a task on an agent session.

    Usage:
        subagents run [--bg] [--backend <name>] [--transport <mode>] [--system-mode <mode>] <agent> <session> <prompt>
        subagents run [--bg] <session> <prompt>                      # resume only
    """
    bg = False
    backend_override: str | None = None
    transport: str | None = None
    system_mode = "append"

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
        else:
            _fail(f"unknown flag {flag}")

    if len(args) < 2:
        _fail("Usage: subagentss run [--bg] [--backend <name>] <agent> <session> <prompt>")

    arg1 = args[0]
    agent_path = Path(AGENTS_DIR) / f"{arg1}.md"

    if agent_path.is_file():
        agent_name = args.pop(0)
        session_name = args.pop(0) if args else ""
        task = " ".join(args) if args else ""
        if not session_name or not task:
            _fail("Usage: subagentss run [--bg] <agent> <session> <prompt>")
        _run_with_agent(agent_name, session_name, task, bg, backend_override, transport, system_mode)
    else:
        session_name = args.pop(0)
        task = " ".join(args) if args else ""
        if not task:
            _fail("Usage: subagentss run [--bg] <session> <prompt>")
        _run_no_agent(session_name, task, bg, backend_override, transport)


def _fail(msg: str) -> None:
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(1)


def _run_with_agent(
    agent_name: str,
    session_name: str,
    task: str,
    bg: bool,
    backend_override: str | None,
    transport: str | None,
    system_mode: str,
) -> None:
    agent = parse_agent(Path(AGENTS_DIR) / f"{agent_name}.md")

    print(f"[subagents] Agent: {agent.name} — {agent.description}", file=sys.stderr)
    print(f"[subagents] Session name: {session_name}", file=sys.stderr)
    print(f"[subagents] Backend: {backend_override or _detect_backend()}", file=sys.stderr)

    if check(session_name):
        _fail(f"session '{session_name}' is already running. Wait with: subagents wait {session_name}")

    lock_path = acquire(session_name)

    def _execute() -> int:
        backend = _make_backend(backend_override, transport)
        try:
            existing_sid = get_session_id(agent_name, session_name)
            if existing_sid:
                print(f"[subagents] Resuming existing session (id: {existing_sid})...", file=sys.stderr)
                exit_code = backend.resume_session(existing_sid, task, agent.body or None, system_mode=system_mode)
            else:
                print("[subagents] Creating new session...", file=sys.stderr)
                sid, exit_code = backend.create_session(task, agent.body or None, system_mode=system_mode)
                register(agent_name, session_name, sid)
                print(f"[subagents] Session registered: {agent_name}/{session_name} (id: {sid})", file=sys.stderr)
        finally:
            backend.close()

        task_status = "done" if exit_code == 0 else "failed"
        add_task(agent_name, session_name, task, task_status)
        complete(agent_name, session_name)
        release(lock_path)
        print(f"[subagents] Session {session_name} completed (exit: {exit_code})", file=sys.stderr)
        return exit_code

    if bg:
        _execute_in_background(session_name, lock_path, _execute)
    else:
        sys.exit(_execute())


def _run_no_agent(
    session_name: str,
    task: str,
    bg: bool,
    backend_override: str | None,
    transport: str | None,
) -> None:
    """Run without agent file — create if new, resume if exists."""
    print(f"[subagents] Session name: {session_name}", file=sys.stderr)

    if check(session_name):
        _fail(f"session '{session_name}' is already running. Wait with: subagents wait {session_name}")

    existing_sid = get_session_id_from_any(session_name)
    lock_path = acquire(session_name)

    def _execute() -> int:
        backend = _make_backend(backend_override, transport)
        try:
            if existing_sid:
                print(f"[subagents] Resuming existing session (id: {existing_sid})...", file=sys.stderr)
                exit_code = backend.resume_session(existing_sid, task)
            else:
                print("[subagents] Creating new session...", file=sys.stderr)
                sid, exit_code = backend.create_session(task)
                register(session_name, session_name, sid)
                print(f"[subagents] Session registered: {session_name} (id: {sid})", file=sys.stderr)
        finally:
            backend.close()

        task_status = "done" if exit_code == 0 else "failed"
        add_task(session_name, session_name, task, task_status)
        complete(session_name, session_name)
        release(lock_path)
        print(f"[subagents] Session {session_name} completed (exit: {exit_code})", file=sys.stderr)
        return exit_code

    if bg:
        _execute_in_background(session_name, lock_path, _execute)
    else:
        sys.exit(_execute())


def _execute_in_background(session_name: str, lock_path: Path, fn) -> None:
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
        output_file = Path(OUTPUT_DIR) / f"{session_name}.out"
        print(f"[subagents] Background task started: {session_name} (pid: {pid})", file=sys.stderr)
        print(f"[subagents] Output: {output_file}", file=sys.stderr)
        print(f"[subagents] Wait with: subagents wait {session_name}", file=sys.stderr)
        release(lock_path)


def cmd_wait(args: list[str]) -> None:
    if not args:
        _fail("Usage: subagentss wait <session>")
    session_name = args[0]
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


def cmd_list(args: list[str]) -> None:
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


def cmd_status(args: list[str]) -> None:
    if not args:
        _fail("Usage: subagentss status <agent> [session]")
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
    print("  run [--bg] [--backend <name>] [--transport cli|acp] [--system-mode append|overwrite] <agent> <session> <prompt>", file=sys.stderr)
    print("  run [--bg] <session> <prompt>", file=sys.stderr)
    print("  wait <session>", file=sys.stderr)
    print("  list", file=sys.stderr)
    print("  status <agent> [session]", file=sys.stderr)


if __name__ == "__main__":
    main()