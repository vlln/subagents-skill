"""Mock ACP server that emits realistic kimi-code session/update notifications.

Usage:
    python3 mock_acp_server.py --port 0  # picks a free port, prints it
    python3 mock_acp_server.py --scenario review  # predefined scenario

Protocol: JSON-RPC 2.0 over stdio (same as kimi acp).
"""

import json
import sys
import time
import threading
import argparse
from typing import Any


# ── predefined scenarios ─────────────────────────────────────────────────────

# Each scenario is a list of (delay_ms, notification) tuples.
# delay_ms=0 means "emit immediately, no wait".

SCENARIOS: dict[str, list[tuple[int, dict]]] = {
    # Simple: 3 tool calls, quick completion
    "simple": [
        (0,   {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "I'll run a command."}}),
        (100, {"sessionUpdate": "tool_call", "toolCallId": "tc-1", "toolName": "Bash"}),
        (200, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Running..."}}),
        (100, {"sessionUpdate": "tool_result", "toolCallId": "tc-1"}),
        (150, {"sessionUpdate": "tool_call", "toolCallId": "tc-2", "toolName": "Read"}),
        (200, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Reading file..."}}),
        (100, {"sessionUpdate": "tool_result", "toolCallId": "tc-2"}),
        (150, {"sessionUpdate": "tool_call", "toolCallId": "tc-3", "toolName": "Write"}),
        (200, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Writing result."}}),
        (100, {"sessionUpdate": "tool_result", "toolCallId": "tc-3"}),
        (300, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Done."}}),
        (0,   {"sessionUpdate": "turn_complete"}),
    ],

    # Longer: 8 tool calls, mimics a code review
    "review": [
        (0,   {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Let me review the code."}}),
        (100, {"sessionUpdate": "tool_call", "toolCallId": "tc-1", "toolName": "Glob"}),
        (150, {"sessionUpdate": "tool_result", "toolCallId": "tc-1"}),
        (80,  {"sessionUpdate": "tool_call", "toolCallId": "tc-2", "toolName": "Read"}),
        (200, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Reading src/auth.ts..."}}),
        (100, {"sessionUpdate": "tool_result", "toolCallId": "tc-2"}),
        (100, {"sessionUpdate": "tool_call", "toolCallId": "tc-3", "toolName": "Read"}),
        (150, {"sessionUpdate": "tool_result", "toolCallId": "tc-3"}),
        (80,  {"sessionUpdate": "tool_call", "toolCallId": "tc-4", "toolName": "Grep"}),
        (200, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Searching for patterns..."}}),
        (100, {"sessionUpdate": "tool_result", "toolCallId": "tc-4"}),
        (100, {"sessionUpdate": "tool_call", "toolCallId": "tc-5", "toolName": "Read"}),
        (150, {"sessionUpdate": "tool_result", "toolCallId": "tc-5"}),
        (100, {"sessionUpdate": "tool_call", "toolCallId": "tc-6", "toolName": "Edit"}),
        (200, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Fixing issues..."}}),
        (100, {"sessionUpdate": "tool_result", "toolCallId": "tc-6"}),
        (100, {"sessionUpdate": "tool_call", "toolCallId": "tc-7", "toolName": "Bash"}),
        (300, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Running tests..."}}),
        (100, {"sessionUpdate": "tool_result", "toolCallId": "tc-7"}),
        (100, {"sessionUpdate": "tool_call", "toolCallId": "tc-8", "toolName": "Bash"}),
        (200, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "All tests pass. Review complete."}}),
        (100, {"sessionUpdate": "tool_result", "toolCallId": "tc-8"}),
        (0,   {"sessionUpdate": "turn_complete"}),
    ],

    # 同质化 swarm: 3 个完全相同的简单任务
    "homogeneous": [
        (0,   {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Processing."}}),
        (100, {"sessionUpdate": "tool_call", "toolCallId": "tc-1", "toolName": "Read"}),
        (150, {"sessionUpdate": "tool_result", "toolCallId": "tc-1"}),
        (80,  {"sessionUpdate": "tool_call", "toolCallId": "tc-2", "toolName": "Edit"}),
        (150, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Editing..."}}),
        (100, {"sessionUpdate": "tool_result", "toolCallId": "tc-2"}),
        (200, {"sessionUpdate": "tool_call", "toolCallId": "tc-3", "toolName": "Bash"}),
        (150, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Done."}}),
        (100, {"sessionUpdate": "tool_result", "toolCallId": "tc-3"}),
        (0,   {"sessionUpdate": "turn_complete"}),
    ],

    # 带 thinking 事件
    "with_thinking": [
        (0,   {"sessionUpdate": "agent_message_chunk", "content": {"type": "thinking", "text": "Let me think about this..."}}),
        (100, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "I'll search for it."}}),
        (100, {"sessionUpdate": "tool_call", "toolCallId": "tc-1", "toolName": "Grep"}),
        (150, {"sessionUpdate": "tool_result", "toolCallId": "tc-1"}),
        (100, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Found it."}}),
        (0,   {"sessionUpdate": "turn_complete"}),
    ],
}


# ── mock server ───────────────────────────────────────────────────────────────

class MockAcpServer:
    """JSON-RPC 2.0 server that simulates kimi ACP behavior."""

    def __init__(self, scenario: str = "simple"):
        if scenario not in SCENARIOS:
            raise ValueError(f"Unknown scenario: {scenario}. Available: {list(SCENARIOS)}")
        self._events = SCENARIOS[scenario]
        self._sessions: dict[str, dict] = {}
        self._next_sid = 1

    def run(self):
        """Run the server on stdio (blocking)."""
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                continue
            self._handle(request)

    def _handle(self, request: dict) -> None:
        method = request.get("method", "")
        req_id = request.get("id")
        params = request.get("params", {})

        if method == "initialize":
            self._respond(req_id, {
                "protocolVersion": 1,
                "agentCapabilities": {
                    "loadSession": True,
                    "promptCapabilities": {"image": True, "audio": False, "embeddedContext": True},
                    "mcpCapabilities": {"http": True, "sse": True},
                    "sessionCapabilities": {"list": {}, "resume": {}},
                },
                "authMethods": [],
            })

        elif method == "session/new":
            sid = f"session-mock-{self._next_sid}"
            self._next_sid += 1
            self._sessions[sid] = {"cwd": params.get("cwd", "/tmp")}
            self._respond(req_id, {"sessionId": sid})

        elif method == "session/load":
            sid = params.get("sessionId", "")
            if sid in self._sessions:
                self._respond(req_id, {})
            else:
                self._error(req_id, -32602, f"Unknown sessionId: {sid}")

        elif method == "session/prompt":
            sid = params.get("sessionId", "")
            # Emit scenario events then respond
            self._emit_scenario(sid)
            self._respond(req_id, {})

        elif method == "session/list":
            self._respond(req_id, {
                "sessions": [
                    {"sessionId": sid, "cwd": s.get("cwd", "/tmp"), "title": "Mock Session"}
                    for sid, s in self._sessions.items()
                ]
            })

        else:
            self._error(req_id, -32601, f"Method not found: {method}")

    def _emit_scenario(self, sid: str):
        """Emit all events in the scenario with delays."""
        for delay_ms, update in self._events:
            if delay_ms > 0:
                time.sleep(delay_ms / 1000.0)
            self._notify("session/update", {
                "sessionId": sid,
                "update": update,
            })

    def _respond(self, req_id: Any, result: dict) -> None:
        self._write({"jsonrpc": "2.0", "id": req_id, "result": result})

    def _error(self, req_id: Any, code: int, message: str) -> None:
        self._write({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})

    def _notify(self, method: str, params: dict) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _write(self, data: dict) -> None:
        sys.stdout.write(json.dumps(data, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mock ACP server for kimi-code")
    parser.add_argument("--scenario", default="simple",
                        choices=list(SCENARIOS),
                        help="Predefined scenario to run")
    args = parser.parse_args()
    server = MockAcpServer(scenario=args.scenario)
    server.run()