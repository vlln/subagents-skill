"""Workflow runtime — agent, parallel, pipeline, phase, log, workflow.

Delegates to the subagents CLI via --output json for structured execution.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

# ── helpers ──────────────────────────────────────────────────────────────

def _subagents_path() -> str:
    home = os.environ.get("SKILL_SUBAGENTS_HOME", "")
    if not home:
        print("[workflow] SKILL_SUBAGENTS_HOME is not set.", file=sys.stderr)
        sys.exit(1)
    return os.path.join(home, "scripts", "subagents")


def _outputs_dir() -> str:
    agents_dir = os.environ.get("SUBAGENT_AGENTS_DIR", ".agents/subagent")
    return os.path.join(agents_dir, "outputs")


def _run_subagent(prompt: str, session: str, backend: str | None = None, model: str | None = None) -> list[dict]:
    sub = _subagents_path()
    cmd = [sub, "run", "--bg", "--output", "json"]
    if backend:
        cmd.extend(["--backend", backend])
    if model:
        cmd.extend(["--model", model])
    cmd.extend([session, prompt])
    subprocess.run(cmd, check=True, capture_output=True)
    result = subprocess.run(
        [sub, "wait", "--output", "json", session],
        capture_output=True, text=True, check=True,
    )
    events: list[dict] = []
    for line in result.stdout.strip().split("\n"):
        if line:
            events.append(json.loads(line))
    return events


def _extract_text(events: list[dict]) -> str:
    parts: list[str] = []
    for evt in events:
        if evt.get("type") == "agent_text":
            parts.append(evt["content"])
    return "\n".join(parts)


def _extract_exit_code(events: list[dict]) -> int:
    for evt in events:
        if evt.get("type") == "agent_done":
            return evt.get("exit_code", 0)
    return 1


# ── context ──────────────────────────────────────────────────────────────

class WorkflowContext:
    """Tracks run state: session naming, resume, nested workflow().

    Singleton — created once per `workflow run` invocation.
    """

    def __init__(self, run_id: str | None = None, resume: bool = False) -> None:
        self.run_id = run_id or uuid.uuid4().hex[:8]
        self.resume = resume
        self._counter = 0

    def next_session(self) -> str:
        self._counter += 1
        return f"wf_{self.run_id}_{self._counter}"

    def session_output_path(self, session: str) -> str:
        return os.path.join(_outputs_dir(), f"{session}.jsonl")

    def try_resume(self, session: str) -> str | None:
        """If resuming and session already completed, return cached text."""
        if not self.resume:
            return None
        path = self.session_output_path(session)
        if not os.path.isfile(path):
            return None
        try:
            events = []
            for line in Path(path).read_text().strip().split("\n"):
                if line:
                    events.append(json.loads(line))
            if _extract_exit_code(events) == 0:
                return _extract_text(events)
        except (json.JSONDecodeError, OSError):
            pass
        return None


_ctx = WorkflowContext()


def set_context(run_id: str | None = None, resume: bool = False) -> WorkflowContext:
    global _ctx
    _ctx = WorkflowContext(run_id=run_id, resume=resume)
    return _ctx


# ── display integration ──────────────────────────────────────────────────

_display: Any = None  # Display | None, set by workflow CLI


def set_display(d: Any) -> None:
    global _display
    _display = d


def get_display() -> Any:
    return _display


# ── mock agent ────────────────────────────────────────────────────────────

_mock_mode: str | None = None  # None | "shell" | "echo"


def set_mock(mode: str | None = "shell") -> None:
    """Enable mock agent mode for testing without a real backend.

    In shell mode, the prompt is executed as a shell command and stdout is
    returned. This lets workflow tests and demos run without real subagents.
    """
    global _mock_mode
    _mock_mode = mode


def _run_mock(prompt: str) -> tuple[str, int]:
    """Run prompt as shell command, return (stdout, exit_code)."""
    try:
        result = subprocess.run(
            prompt, shell=True, capture_output=True, text=True, timeout=30,
        )
        return result.stdout.strip(), result.returncode
    except subprocess.TimeoutExpired:
        return "", 1
    except Exception:
        return "", 1


# ── public API ───────────────────────────────────────────────────────────

def agent(
    prompt: str,
    *,
    schema: dict | None = None,
    label: str | None = None,
    model: str | None = None,
    backend: str | None = None,
) -> Any:
    session = _ctx.next_session()
    log_prefix = f"[{label}] " if label else ""
    display_label = label or _short_prompt(prompt)

    # Resume: skip if already completed
    if _ctx.resume:
        cached = _ctx.try_resume(session)
        if cached is not None:
            log(f"{log_prefix}resumed (cached)")
            if _display:
                _display.agent_start(display_label, prompt)
                _display.agent_skip(display_label)
            if schema:
                try:
                    return json.loads(cached)
                except json.JSONDecodeError:
                    return None
            return cached

    log(f"{log_prefix}starting...")
    if _display:
        _display.agent_start(display_label, prompt)

    t0 = time.time()

    if _mock_mode:
        text, exit_code = _run_mock(prompt)
        elapsed = time.time() - t0
    else:
        events = _run_subagent(prompt, session, backend=backend, model=model)
        elapsed = time.time() - t0
        exit_code = _extract_exit_code(events)
        text = _extract_text(events) if exit_code == 0 else ""

    if exit_code != 0:
        log(f"{log_prefix}failed (exit {exit_code})")
        if _display:
            _display.agent_done(display_label, success=False, elapsed=elapsed)
        return None

    if schema:
        try:
            parsed = json.loads(text)
            log(f"{log_prefix}done")
            if _display:
                _display.agent_done(display_label, success=True, elapsed=elapsed)
            return parsed
        except json.JSONDecodeError:
            log(f"{log_prefix}output is not valid JSON")
            if _display:
                _display.agent_done(display_label, success=False, elapsed=elapsed)
            return None

    log(f"{log_prefix}done")
    if _display:
        _display.agent_done(display_label, success=True, elapsed=elapsed)
    return text


def _short_prompt(prompt: str, max_len: int = 50) -> str:
    """Truncate a prompt for display labels."""
    first_line = prompt.split("\n")[0].strip()
    if len(first_line) > max_len:
        return first_line[:max_len - 3] + "..."
    return first_line


def parallel(thunks: list[Callable[[], Any]]) -> list[Any]:
    results: list[Any] = [None] * len(thunks)

    def _run(idx: int, fn: Callable[[], Any]) -> None:
        try:
            results[idx] = fn()
        except Exception:
            results[idx] = None

    threads: list[threading.Thread] = []
    for i, fn in enumerate(thunks):
        t = threading.Thread(target=_run, args=(i, fn), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()
    return results


def pipeline(items: list[Any], *stages: Callable) -> list[Any]:
    results: list[Any] = [None] * len(items)

    def _process(idx: int, item: Any) -> None:
        result: Any = item
        for stage in stages:
            try:
                if stage is stages[0]:
                    result = stage(item, idx)
                else:
                    result = stage(result, item, idx)
            except Exception:
                result = None
                break
        results[idx] = result

    threads: list[threading.Thread] = []
    for i, item in enumerate(items):
        t = threading.Thread(target=_process, args=(i, item), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()
    return results


def workflow(script_path: str, args: dict | None = None) -> Any:
    """Run another workflow script as a sub-workflow.

    Shares the current run context (same run_id, same session counter).

    Args:
        script_path: Path to the workflow script (.py file).
        args: Optional arguments passed to the sub-workflow's run().
    """
    path = Path(script_path)
    if not path.is_file():
        log(f"sub-workflow not found: {script_path}")
        return None

    spec = importlib.util.spec_from_file_location(
        f"wf_sub_{_ctx.run_id}_{path.stem}", path)
    if spec is None or spec.loader is None:
        log(f"cannot load sub-workflow: {script_path}")
        return None

    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    if not hasattr(mod, "run"):
        log(f"sub-workflow missing run(): {script_path}")
        return None

    meta = getattr(mod, "meta", {})
    phase(f"Sub: {meta.get('name', path.stem)}")
    return mod.run(
        agent=agent, parallel=parallel, pipeline=pipeline,
        phase=phase, log=log, args=args or {},
        workflow=workflow,
    )


def phase(title: str) -> None:
    if _display:
        _display.phase(title)
    if _display is None:
        print(f"[workflow] Phase: {title}", file=sys.stderr, flush=True)


def log(message: str) -> None:
    if _display is not None:
        return  # display panel shows status — suppress log noise
    print(f"[workflow] {message}", file=sys.stderr, flush=True)