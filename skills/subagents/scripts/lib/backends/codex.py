"""Codex backend — calls the `codex exec` command."""

from __future__ import annotations

from cli_backend import CliBackend


class CodexBackend(CliBackend):
    def _cmd_create(self, user: str, system: str | None, model: str | None) -> list[str]:
        prompt = f"System: {system}\n\nTask: {user}" if system else user
        cmd = ["codex", "exec", "--json", "--dangerously-bypass-approvals-and-sandbox", prompt]
        if model:
            cmd.extend(["-m", model])
        return cmd

    def _cmd_resume(self, sid: str, user: str, system: str | None, model: str | None) -> list[str]:
        cmd = ["codex", "exec", "resume", "--json", "--dangerously-bypass-approvals-and-sandbox", sid, user]
        if model:
            cmd.extend(["-m", model])
        return cmd

    def _parse_line(self, line: str) -> tuple[str | None, str | None]:
        data = self._try_parse_json(line)
        if data is None:
            return (line, None)
        tp = data.get("type", "")
        if tp == "thread.started":
            return (None, data.get("thread_id") or None)
        if tp == "item.completed":
            item = data.get("item", {})
            if isinstance(item, dict) and item.get("type") == "agent_message":
                return (item.get("text", ""), None)
        return (None, None)