"""Pi backend — calls the `pi` CLI."""

from __future__ import annotations

from cli_backend import CliBackend


class PiBackend(CliBackend):
    def _cmd_create(self, user: str, system: str | None, model: str | None, system_mode: str) -> list[str]:
        cmd = ["pi", "-p", "--mode", "json"]
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
        cmd = ["pi", "-p", "--mode", "json", "--session-id", sid, user]
        if model:
            cmd.extend(["--model", model])
        return cmd

    def _parse_line(self, line: str) -> tuple[str | None, str | None]:
        data = self._try_parse_json(line)
        if data is None:
            return (line, None)
        tp = data.get("type", "")
        if tp == "session":
            return (None, data.get("id") or None)
        if tp == "message_update":
            event = data.get("assistantMessageEvent", {})
            if event.get("type") == "text_delta":
                return (event.get("delta", ""), None)
        return (None, None)