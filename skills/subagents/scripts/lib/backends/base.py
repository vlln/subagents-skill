"""Abstract backend interface for subagent."""

from abc import ABC, abstractmethod


class BaseBackend(ABC):
    """Abstract backend for running subagent sessions.

    Each backend implements how to create, resume, and list sessions
    for a specific agent provider (e.g. kimi, claude, codex, pi, kiro).
    """

    @abstractmethod
    def create_session(
        self, user: str, system: str | None = None, model: str | None = None, system_mode: str = "append"
    ) -> tuple[str, int]:
        """Create a new session and run the prompt.

        Args:
            user: The user prompt.
            system: Optional system prompt (agent definition body).
            model: Optional model name.
            system_mode: 'append' (default) or 'overwrite'. Controls how the
                system prompt is handled relative to the backend's default.
        """

    @abstractmethod
    def resume_session(
        self, session_id: str, user: str, system: str | None = None, model: str | None = None, system_mode: str = "append"
    ) -> int:
        """Resume an existing session and run the prompt.

        Args:
            session_id: The backend session ID to resume.
            user: The user prompt.
            system: Optional system prompt (agent definition body).
            model: Optional model name.
            system_mode: 'append' (default) or 'overwrite'. Controls how the
                system prompt is handled relative to the backend's default.
        """

    @abstractmethod
    def list_sessions(self) -> list[dict]:
        """List all sessions known to this backend."""

    def close(self) -> None:
        """Clean up backend resources. Optional override."""