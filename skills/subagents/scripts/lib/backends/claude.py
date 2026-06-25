"""Claude backend — calls the `claude` CLI."""

from __future__ import annotations

from cli_backend import CliBackend


class ClaudeBackend(CliBackend):
    def _cmd_create(self, user: str, system: str | None, model: str | None, system_mode: str) -> list[str]:
        cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose", "--permission-mode", "bypassPermissions"]
        if system:
            if system_mode == "overwrite":
                cmd.extend(["--system-prompt", system])
            else:
                cmd.extend(["--append-system-prompt", system])
        cmd.append(user)
        if model:
            cmd.extend(["--model", model])
        return cmd

    def _cmd_resume(self, sid: str, user: str, system: str | None, model: str | None, system_mode: str) -> list[str]:
        cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose", "--resume", sid, "--permission-mode", "bypassPermissions"]
        if system:
            if system_mode == "overwrite":
                cmd.extend(["--system-prompt", system])
            else:
                cmd.extend(["--append-system-prompt", system])
        cmd.append(user)
        if model:
            cmd.extend(["--model", model])
        return cmd

    def _parse_line(self, line: str) -> tuple[str | None, str | None]:
        data = self._try_parse_json(line)
        if data is None:
            return (line, None)
        tp = data.get("type", "")
        if tp == "system" and data.get("subtype") == "init":
            return (None, data.get("session_id") or None)
        if tp == "assistant":
            content = data.get("message", {}).get("content", [])
            texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
            return ("".join(texts), data.get("session_id") or None)
        if tp == "result":
            return (None, data.get("session_id") or None)
        return (None, None)