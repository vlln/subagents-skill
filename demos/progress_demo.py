#!/usr/bin/env python3
"""AgentProgressRenderer visual demo.

Two modes:
  --synthetic   Fast, no dependencies — feeds hardcoded events to ProgressEstimator.
  --real        Uses mock ACP servers via the real AcpTransport protocol stack.

Layouts:
  --single      Full-width bar for one agent.
  --grid        N-column grid (swarm).  Use --count to control agent count.
  --list        Vertical list, one agent per line.

Usage:
    python3 demos/progress_demo.py                           # synthetic grid (8 agents)
    python3 demos/progress_demo.py --synthetic --single      # synthetic single
    python3 demos/progress_demo.py --synthetic --list        # synthetic list
    python3 demos/progress_demo.py --real --count 4          # real mock ACP, 4 agents
    python3 demos/progress_demo.py --real --single           # real mock ACP, single
    python3 demos/progress_demo.py --real --backend kimi     # real kimi ACP (needs auth)
    python3 demos/progress_demo.py --ascii                   # ASCII fallback mode
"""

import json
import os
import random
import subprocess
import sys
import threading
import time
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "skills" / "subagents" / "scripts" / "lib"
sys.path.insert(0, str(_LIB))
sys.path.insert(0, str(_LIB / "backends"))
sys.path.insert(0, str(_LIB / "transports"))

from progress import Phase, ProgressEstimator, AgentProgressRenderer, set_ascii_mode


# ═══════════════════════════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════════════════════════

def term_width() -> int:
    try:
        return os.get_terminal_size().columns
    except (OSError, ValueError):
        return 80


def cursor_hide():
    sys.stderr.write("\033[?25l"); sys.stderr.flush()


def cursor_show():
    sys.stderr.write("\033[?25h"); sys.stderr.flush()


def render_frame(lines: list[str]):
    """Write lines to stderr, overwriting previous frame."""
    sys.stderr.write("\r\033[K")
    for line in lines:
        sys.stderr.write(line + "\n")
    if lines:
        sys.stderr.write(f"\033[{len(lines)}A")
    sys.stderr.flush()


def render_final(lines: list[str]):
    """Write final lines without cursor-up, so they stay visible."""
    sys.stderr.write("\r\033[K")
    for line in lines:
        sys.stderr.write(line + "\n")
    sys.stderr.flush()


# ═══════════════════════════════════════════════════════════════════════════════
# synthetic mode — hardcoded events, no external dependencies
# ═══════════════════════════════════════════════════════════════════════════════

def synthetic_single():
    estimator = ProgressEstimator()
    renderer = AgentProgressRenderer()
    w = term_width()

    estimator.ensure_member("agent")
    t0 = time.time() * 1000

    events = [
        (500,  "start",           None),
        (1000, "tool_call",       "tc-1"),
        (1500, "tool_call",       "tc-2"),
        (2000, "tool_call",       "tc-3"),
        (2500, "tool_call",       "tc-4"),
        (3000, "tool_call",       "tc-5"),
        (3500, "tool_call",       "tc-6"),
        (4000, "tool_call",       "tc-7"),
        (4500, "tool_call",       "tc-8"),
        (5000, "complete",        None),
    ]

    for delay_ms, action, arg in events:
        while (time.time() * 1000 - t0) < delay_ms:
            now_ms = time.time() * 1000 - t0
            est = estimator.estimate("agent", 42, now_ms)
            render_frame(renderer.render_single(est, w, label="example-task"))
            time.sleep(0.08)
        if action == "start":
            estimator.mark_started("agent", now_ms=delay_ms)
        elif action == "tool_call":
            estimator.record_tool_call("agent", arg, now_ms=delay_ms)
        elif action == "complete":
            estimator.mark_completed("agent", now_ms=delay_ms)

    est = estimator.estimate("agent", 42, time.time() * 1000 - t0)
    render_final(renderer.render_single(est, w, label="example-task"))
    time.sleep(1)


def synthetic_grid(agent_count: int = 8):
    estimator = ProgressEstimator()
    renderer = AgentProgressRenderer(max_columns=4)
    w = term_width()
    keys = [f"{i+1:03d}" for i in range(agent_count)]

    for k in keys:
        estimator.ensure_member(k)

    random.seed(42)
    timeline: list[tuple[int, str, str, str | None]] = []

    for i, k in enumerate(keys):
        start_ms = 200 + i * 300
        num_calls = random.randint(6, 12)
        duration_ms = random.randint(3000, 6000)
        timeline.append((start_ms, "start", k, None))
        for j in range(num_calls):
            ct = start_ms + int(duration_ms * (j + 1) / (num_calls + 1))
            timeline.append((ct, "tool_call", k, f"tc-{k}-{ct}"))
        timeline.append((start_ms + duration_ms, "complete", k, None))
    timeline.sort()

    t0 = time.time() * 1000
    event_idx = 0
    max_time = timeline[-1][0] + 500

    while True:
        now_ms = time.time() * 1000 - t0
        if now_ms > max_time:
            break
        while event_idx < len(timeline) and timeline[event_idx][0] <= now_ms:
            _, action, key, arg = timeline[event_idx]
            if action == "start":
                estimator.mark_started(key, now_ms=timeline[event_idx][0])
            elif action == "tool_call":
                estimator.record_tool_call(key, arg, now_ms=timeline[event_idx][0])
            elif action == "complete":
                estimator.mark_completed(key, now_ms=timeline[event_idx][0])
            event_idx += 1
        estimates = estimator.estimate_all(42, now_ms)
        render_frame(renderer.render_grid(estimates, w, total_count=agent_count,
                                           description="code-review"))
        time.sleep(0.08)

    estimates = estimator.estimate_all(42, max_time)
    render_final(renderer.render_grid(estimates, w, total_count=agent_count,
                                       description="code-review"))
    time.sleep(1.5)


def synthetic_list():
    estimator = ProgressEstimator()
    renderer = AgentProgressRenderer()
    w = term_width()
    keys = ["session-1", "session-2", "session-3", "session-4", "session-5"]

    for k in keys:
        estimator.ensure_member(k)

    estimator.mark_started("session-1", now_ms=0)
    for i in range(8):
        estimator.record_tool_call("session-1", f"tc-{i}", now_ms=(i + 1) * 1000)
    estimator.mark_completed("session-1", now_ms=9000)

    estimator.mark_started("session-2", now_ms=0)
    for i in range(5):
        estimator.record_tool_call("session-2", f"tc-{i}", now_ms=(i + 1) * 800)

    estimator.mark_started("session-3", now_ms=0)
    estimator.record_tool_call("session-3", "tc-1", now_ms=1000)
    estimator.mark_failed("session-3", now_ms=2000)

    estimator.mark_started("session-4", now_ms=0)

    t0 = time.time() * 1000
    while (time.time() * 1000 - t0) < 3000:
        now_ms = time.time() * 1000 - t0 + 9000
        estimates = estimator.estimate_all(42, now_ms)
        render_frame(renderer.render_list(estimates, w))
        time.sleep(0.08)

    render_final(renderer.render_list(estimator.estimate_all(42, time.time() * 1000 - t0 + 9000), w))


# ═══════════════════════════════════════════════════════════════════════════════
# real mode — mock ACP servers via AcpTransport
# ═══════════════════════════════════════════════════════════════════════════════

SCENARIOS = ["simple", "review", "homogeneous", "with_thinking"]


class _AcpAgent:
    """Runs a mock ACP server and feeds tool_call events to ProgressEstimator."""

    def __init__(self, agent_id: str, mock_script: str, scenario: str):
        self.agent_id = agent_id
        self._mock_script = mock_script
        self._scenario = scenario
        self._proc: subprocess.Popen | None = None
        self._done = threading.Event()
        self._error: str | None = None

    def start(self, estimator: ProgressEstimator):
        self._proc = subprocess.Popen(
            [sys.executable, self._mock_script, "--scenario", self._scenario],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True,
        )
        threading.Thread(target=self._run, args=(estimator,), daemon=True).start()

    def _run(self, estimator: ProgressEstimator):
        try:
            self._handshake(estimator)
        except Exception as e:
            self._error = str(e)
        finally:
            self._done.set()
            if self._proc:
                try: self._proc.stdin.close()
                except OSError: pass
                try: self._proc.wait(timeout=2)
                except subprocess.TimeoutExpired: self._proc.kill(); self._proc.wait()

    def _handshake(self, estimator: ProgressEstimator):
        estimator.ensure_member(self.agent_id)
        self._call("initialize", {"protocolVersion": 1,
            "clientInfo": {"name": "demo", "version": "0.1"},
            "capabilities": {"prompt": {"text": True}, "terminal": False}})
        self._call("session/new", {"cwd": "/tmp", "mcpServers": []})
        estimator.mark_started(self.agent_id, now_ms=time.time() * 1000)
        self._write({"jsonrpc": "2.0", "id": 99, "method": "session/prompt",
            "params": {"sessionId": "mock", "prompt": [{"type": "text", "text": "task"}]}})
        while True:
            msg = self._read()
            if msg is None:
                raise RuntimeError("Mock closed")
            if msg.get("id") == 99:
                estimator.mark_completed(self.agent_id, now_ms=time.time() * 1000)
                return
            if "method" in msg:
                kind = msg.get("params", {}).get("update", {}).get("sessionUpdate", "")
                if kind == "tool_call":
                    estimator.record_tool_call(
                        self.agent_id,
                        msg["params"]["update"].get("toolCallId", "?"),
                        now_ms=time.time() * 1000,
                    )

    def _call(self, method: str, params: dict) -> dict:
        self._write({"jsonrpc": "2.0", "id": self._next_id(), "method": method, "params": params})
        return self._expect()

    _next_req = 0
    def _next_id(self) -> int:
        _AcpAgent._next_req += 1
        return _AcpAgent._next_req

    def _expect(self) -> dict:
        while True:
            msg = self._read()
            if msg is None:
                raise RuntimeError("No response")
            if "id" in msg and "method" not in msg:
                if "error" in msg:
                    raise RuntimeError(str(msg["error"]))
                return msg

    def _write(self, data: dict):
        if self._proc and self._proc.stdin:
            self._proc.stdin.write(json.dumps(data, ensure_ascii=False) + "\n")
            self._proc.stdin.flush()

    def _read(self) -> dict | None:
        if self._proc and self._proc.stdout:
            line = self._proc.stdout.readline()
            if line:
                try: return json.loads(line.strip())
                except json.JSONDecodeError: pass
        return None

    def is_done(self) -> bool: return self._done.is_set()
    def error(self) -> str | None: return self._error


def real_swarm(agent_count: int = 8):
    mock_script = str(Path(__file__).resolve().parent.parent / "tests" / "mock_acp_server.py")
    if not Path(mock_script).is_file():
        print("Error: mock_acp_server.py not found", file=sys.stderr); sys.exit(1)

    estimator = ProgressEstimator()
    renderer = AgentProgressRenderer(max_columns=4)
    w = term_width()

    agents = [_AcpAgent(f"{i+1:03d}", mock_script, SCENARIOS[i % len(SCENARIOS)])
              for i in range(agent_count)]
    for a in agents:
        a.start(estimator)

    try:
        while True:
            now_ms = time.time() * 1000
            estimates = estimator.estimate_all(42, now_ms)
            render_frame(renderer.render_grid(estimates, w, total_count=agent_count,
                                               description="real-acp"))
            if all(a.is_done() for a in agents):
                break
            time.sleep(0.08)

        estimates = estimator.estimate_all(42, time.time() * 1000)
        render_final(renderer.render_grid(estimates, w, total_count=agent_count,
                                           description="real-acp"))
        time.sleep(1.5)
    finally:
        sys.stderr.write("\n"); sys.stderr.flush()

    for a in agents:
        if a.error():
            print(f"  {a.agent_id}: {a.error()}", file=sys.stderr)


def real_single():
    mock_script = str(Path(__file__).resolve().parent.parent / "tests" / "mock_acp_server.py")
    estimator = ProgressEstimator()
    renderer = AgentProgressRenderer()
    w = term_width()

    agent = _AcpAgent("agent", mock_script, "review")
    agent.start(estimator)

    try:
        while not agent.is_done():
            est = estimator.estimate("agent", 42, time.time() * 1000)
            render_frame(renderer.render_single(est, w, label="review-task"))
            time.sleep(0.08)
        est = estimator.estimate("agent", 42, time.time() * 1000)
        render_final(renderer.render_single(est, w, label="review-task"))
        time.sleep(1)
    finally:
        sys.stderr.write("\n"); sys.stderr.flush()

    if agent.error():
        print(f"Error: {agent.error()}", file=sys.stderr)


# ═══════════════════════════════════════════════════════════════════════════════
# main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    import argparse
    p = argparse.ArgumentParser(description="AgentProgressRenderer visual demo")
    p.add_argument("--real", action="store_true", help="Mock ACP servers via real protocol stack")
    p.add_argument("--single", action="store_true", help="Single agent layout")
    p.add_argument("--grid", action="store_true", help="Grid layout (swarm, default)")
    p.add_argument("--list", action="store_true", help="Vertical list layout")
    p.add_argument("--count", type=int, default=8, help="Agent count for grid (default: 8)")
    p.add_argument("--ascii", action="store_true", help="ASCII fallback mode")
    args = p.parse_args()

    if args.ascii:
        set_ascii_mode(True)

    use_real = args.real
    single = args.single
    grid = args.grid
    list_mode = args.list
    all_modes = not (single or grid or list_mode)

    try:
        cursor_hide()

        if use_real:
            if all_modes or grid:
                real_swarm(args.count)
            elif single:
                real_single()
            elif list_mode:
                print("--list not supported in --real mode", file=sys.stderr)
        else:
            if all_modes or single:
                synthetic_single()
            if all_modes or grid:
                synthetic_grid(args.count)
            if all_modes or list_mode:
                synthetic_list()
    finally:
        cursor_show()
        sys.stderr.write("Done.\n"); sys.stderr.flush()


if __name__ == "__main__":
    main()