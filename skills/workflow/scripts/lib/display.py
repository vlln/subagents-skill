"""Live ANSI display for workflow execution — status panel and summary table."""

from __future__ import annotations

import sys
import threading
import time
from typing import Any

# ── ANSI helpers ────────────────────────────────────────────────────────────

_CURSOR_HIDE = "\033[?25l"
_CURSOR_SHOW = "\033[?25h"
_CLEAR_BELOW = "\033[0J"
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_CYAN = "\033[36m"

_SPINNERS = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape codes and spinner characters for comparison."""
    import re
    return re.sub(r"\033\[[0-9;]*[a-zA-Z]", "", text)


def _status_icon(status: str, spinner_idx: int = 0) -> str:
    if status == "done":
        return f"{_GREEN}✓{_RESET}"
    elif status == "failed":
        return f"{_RED}✗{_RESET}"
    elif status == "running":
        return f"{_YELLOW}{_SPINNERS[spinner_idx % len(_SPINNERS)]}{_RESET}"
    else:
        return f"{_DIM}○{_RESET}"


def _fmt_elapsed(seconds: float) -> str:
    if seconds < 0:
        return ""
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m{s}s"


# ── Display ─────────────────────────────────────────────────────────────────

class Display:
    """Tracks workflow state and renders a live-updating ANSI panel.

    Thread-safe — state mutations are protected by a lock.
    """

    def __init__(self, name: str = "", run_id: str = "", width: int = 66) -> None:
        self._name = name
        self._run_id = run_id
        self._width = width
        self._lock = threading.Lock()
        self._phases: list[dict[str, Any]] = []
        self._agents: list[dict[str, Any]] = []
        self._start_time = time.time()
        self._spinner_idx = 0
        self._refresh_thread: threading.Thread | None = None
        self._running = False
        self._enabled = sys.stderr.isatty()
        self._total_phases = 0
        self._last_status = ""  # debounce: skip duplicate status lines
        self._last_line_count = 0  # for cursor-up repositioning

    # ── state tracking ──────────────────────────────────────────────────

    def set_phases(self, phases: list[dict]) -> None:
        """Pre-declare all phases and their tasks for full-tree display."""
        with self._lock:
            self._total_phases = len(phases)
            for ph in phases:
                self._phases.append({
                    "title": ph["title"],
                    "status": "pending",
                    "start_time": 0.0,
                    "end_time": 0.0,
                })
                for task in ph.get("tasks", []):
                    self._agents.append({
                        "label": task,
                        "prompt": "",
                        "phase": ph["title"],
                        "status": "pending",
                        "start_time": 0.0,
                        "elapsed": 0.0,
                    })

    def phase(self, title: str) -> None:
        with self._lock:
            # Mark any currently running phase as done
            for ph in self._phases:
                if ph["status"] == "running":
                    ph["status"] = "done"
                    ph["end_time"] = time.time()
                    break
            # Find and activate the matching pre-declared/next phase
            for ph in self._phases:
                if ph["title"] == title and ph["status"] == "pending":
                    ph["status"] = "running"
                    ph["start_time"] = time.time()
                    break
            else:
                self._phases.append({
                    "title": title,
                    "status": "running",
                    "start_time": time.time(),
                    "end_time": 0.0,
                })
        if not self._enabled:
            self._emit_status()

    def agent_start(self, label: str, prompt: str = "") -> None:
        with self._lock:
            # Find and activate matching pre-declared agent
            for a in self._agents:
                if a["label"] == label and a["status"] == "pending":
                    a["status"] = "running"
                    a["start_time"] = time.time()
                    a["prompt"] = prompt
                    break
            else:
                # Find current running phase
                phase_title = ""
                for ph in self._phases:
                    if ph["status"] == "running":
                        phase_title = ph["title"]
                        break
                self._agents.append({
                    "label": label,
                    "prompt": prompt,
                    "phase": phase_title,
                    "status": "running",
                    "start_time": time.time(),
                    "elapsed": 0.0,
                })
        if not self._enabled:
            self._emit_status()

    def agent_done(self, label: str, success: bool, elapsed: float = 0.0) -> None:
        with self._lock:
            for a in self._agents:
                if a["label"] == label and a["status"] == "running":
                    a["status"] = "done" if success else "failed"
                    a["elapsed"] = elapsed
                    break
        # Don't emit here — stop() handles final render

    def agent_skip(self, label: str) -> None:
        with self._lock:
            for a in self._agents:
                if a["label"] == label and a["status"] == "running":
                    a["status"] = "skipped"
                    break

    # ── rendering ───────────────────────────────────────────────────────

    def _render(self) -> str:
        with self._lock:
            elapsed = time.time() - self._start_time
            lines: list[str] = []

            # Header
            header = f"══ Workflow: {self._name}"
            if self._run_id:
                header += f" ({self._run_id})"
            header += f"  {_fmt_elapsed(elapsed)}"
            lines.append(_BOLD + header + _RESET)
            lines.append("")

            # Phases and agents
            total = self._total_phases or len(self._phases)
            for pi, ph in enumerate(self._phases):
                phase_num = f"{pi + 1}/{total}" if total > 0 else ""
                icon = _status_icon(ph["status"], self._spinner_idx)
                p_elapsed = ""
                if ph["status"] == "done" and ph["end_time"]:
                    p_elapsed = _fmt_elapsed(ph["end_time"] - ph["start_time"])
                elif ph["status"] == "running":
                    p_elapsed = _fmt_elapsed(time.time() - ph["start_time"])

                title = f" Phase {phase_num}: {ph['title']}" if phase_num else f" Phase: {ph['title']}"
                line = f"  {icon} {title}"
                if p_elapsed:
                    line += f"  {_DIM}{p_elapsed}{_RESET}"
                lines.append(line)

                phase_agents = [a for a in self._agents if a["phase"] == ph["title"]]
                for ai, a in enumerate(phase_agents):
                    is_last = ai == len(phase_agents) - 1
                    prefix = "   └─" if is_last else "   ├─"
                    a_icon = _status_icon(a["status"], self._spinner_idx)
                    a_elapsed = ""
                    if a["status"] in ("done", "failed"):
                        a_elapsed = _fmt_elapsed(a["elapsed"])
                    elif a["status"] == "running":
                        a_elapsed = _fmt_elapsed(time.time() - a["start_time"])

                    a_line = f" {prefix} {a_icon} {a['label']}"
                    if a_elapsed:
                        a_line += f"  {_DIM}{a_elapsed}{_RESET}"
                    if a["status"] == "failed":
                        a_line += f"  {_RED}failed{_RESET}"
                    lines.append(a_line)

                if phase_agents:
                    lines.append("")

            return "\n".join(lines)

    def _draw(self) -> None:
        self._spinner_idx += 1
        rendered = self._render()
        line_count = rendered.count("\n") + 1
        if self._last_line_count > 0:
            # Move cursor back up to panel start
            sys.stderr.write(f"\033[{self._last_line_count}A")
        else:
            # First draw: reserve space so terminal doesn't scroll
            sys.stderr.write("\n" * line_count)
            sys.stderr.write(f"\033[{line_count}A")
        sys.stderr.write(rendered)
        sys.stderr.write(_CLEAR_BELOW)
        sys.stderr.write("\n")
        sys.stderr.flush()
        self._last_line_count = line_count

    def _pad_line(self, line: str) -> str:
        """Pad a content line to fill the panel width, accounting for ANSI codes."""
        visible = len(_strip_ansi(line))
        pad = self._width - visible
        if pad > 0:
            return f"│{line}{' ' * pad}│"
        return f"│{line}│"

    def _emit_status(self) -> None:
        """Non-TTY: print the full tree on state change."""
        with self._lock:
            elapsed = _fmt_elapsed(time.time() - self._start_time)
            self._spinner_idx += 1

            lines: list[str] = []
            header = f"══ Workflow: {self._name}"
            if self._run_id:
                header += f" ({self._run_id})"
            header += f"  {elapsed}"
            lines.append(header)

            total = self._total_phases or len(self._phases)
            for pi, ph in enumerate(self._phases):
                phase_num = f"{pi + 1}/{total}" if total > 0 else ""
                icon = _status_icon(ph["status"], self._spinner_idx)
                p_elapsed = ""
                if ph["status"] == "done" and ph["end_time"]:
                    p_elapsed = _fmt_elapsed(ph["end_time"] - ph["start_time"])
                elif ph["status"] == "running":
                    p_elapsed = _fmt_elapsed(time.time() - ph["start_time"])

                title = f" Phase {phase_num}: {ph['title']}" if phase_num else f" Phase: {ph['title']}"
                line = f"  {icon} {title}"
                if p_elapsed:
                    line += f"  {p_elapsed}"
                lines.append(line)

                phase_agents = [a for a in self._agents if a["phase"] == ph["title"]]
                for ai, a in enumerate(phase_agents):
                    is_last = ai == len(phase_agents) - 1
                    prefix = "   └─" if is_last else "   ├─"
                    a_icon = _status_icon(a["status"], self._spinner_idx)
                    a_elapsed = ""
                    if a["status"] in ("done", "failed"):
                        a_elapsed = _fmt_elapsed(a["elapsed"])
                    elif a["status"] == "running":
                        a_elapsed = _fmt_elapsed(time.time() - a["start_time"])

                    a_line = f" {prefix} {a_icon} {a['label']}"
                    if a_elapsed:
                        a_line += f"  {a_elapsed}"
                    if a["status"] == "failed":
                        a_line += f"  failed"
                    lines.append(a_line)

            key = "\n".join(lines)
            if key == self._last_status:
                return
            self._last_status = key

        sys.stderr.write(key + "\n\n")
        sys.stderr.flush()

    # ── lifecycle ───────────────────────────────────────────────────────

    def start_auto_refresh(self, interval: float = 0.3) -> None:
        """Start a background thread that periodically redraws the panel.

        In TTY mode: full panel with periodic refresh.
        In non-TTY mode: no background refresh — status lines are emitted
        only on state changes (phase/agent_start/agent_done).
        """
        if self._enabled:
            sys.stderr.write(_CURSOR_HIDE)
            sys.stderr.flush()
        self._running = True

        if self._enabled:
            def _refresh() -> None:
                while self._running:
                    self._draw()
                    time.sleep(interval)

            self._refresh_thread = threading.Thread(target=_refresh, daemon=True)
            self._refresh_thread.start()

    def stop(self) -> None:
        """Stop auto-refresh, draw final frame (TTY) or tree (non-TTY)."""
        self._running = False
        if self._refresh_thread:
            self._refresh_thread.join(timeout=1.0)
        # Force any running phase to done
        with self._lock:
            for ph in self._phases:
                if ph["status"] == "running":
                    ph["status"] = "done"
                    ph["end_time"] = time.time()
        if self._enabled:
            if self._last_line_count > 0:
                sys.stderr.write(f"\033[{self._last_line_count}A")
            sys.stderr.write(self._render())
            sys.stderr.write(_CLEAR_BELOW)
            sys.stderr.write(_CURSOR_SHOW)
            sys.stderr.write("\n")
            sys.stderr.flush()
        else:
            sys.stderr.write(self._render())
            sys.stderr.write("\n")
            sys.stderr.flush()

    def summary(self) -> str:
        """Render the final summary table."""
        with self._lock:
            elapsed = time.time() - self._start_time
            lines: list[str] = []

            # Header
            lines.append(f"{_BOLD}══ Workflow Summary: {self._name} ══{_RESET}")
            lines.append("")

            # Stats
            done = sum(1 for a in self._agents if a["status"] == "done")
            failed = sum(1 for a in self._agents if a["status"] == "failed")
            skipped = sum(1 for a in self._agents if a["status"] == "skipped")
            lines.append(f"  Duration: {_fmt_elapsed(elapsed)}    "
                         f"Phases: {len(self._phases)}    "
                         f"Sessions: {len(self._agents)}")
            lines.append(f"  {_GREEN}✓{_RESET} {done} done    "
                         f"{_RED}✗{_RESET} {failed} failed    "
                         f"{_DIM}○{_RESET} {skipped} skipped")
            lines.append("")

            # Phases detail
            for ph in self._phases:
                p_elapsed = ""
                p_status = ph["status"]
                if ph["end_time"]:
                    p_elapsed = _fmt_elapsed(ph["end_time"] - ph["start_time"])
                elif p_status == "running":
                    p_status = "done"
                    p_elapsed = _fmt_elapsed(time.time() - ph["start_time"])

                icon = _status_icon(p_status, 0)
                phase_agents = [a for a in self._agents if a["phase"] == ph["title"]]
                phase_failed = sum(1 for a in phase_agents if a["status"] == "failed")

                extra = ""
                if phase_failed > 0:
                    extra = f"  ({phase_failed} failed)"

                p_line = f"  {icon} {ph['title']}  {_DIM}{p_elapsed}{_RESET}{extra}"
                lines.append(p_line)

                for a in phase_agents:
                    a_icon = _status_icon(a["status"], 0)
                    a_elapsed = _fmt_elapsed(a["elapsed"])
                    a_line = f"     {a_icon} {a['label']}  {_DIM}{a_elapsed}{_RESET}"
                    lines.append(a_line)

                lines.append("")

            return "\n".join(lines)