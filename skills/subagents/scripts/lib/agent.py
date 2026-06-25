"""Agent definition parser for .md files with YAML frontmatter."""

from dataclasses import dataclass
from pathlib import Path


@dataclass
class AgentDef:
    """Parsed agent definition from a .md file."""

    name: str
    description: str
    body: str = ""       # entire .md file content (including frontmatter), used as system prompt
    file_path: str | None = None


def parse_agent(file_path: str | Path) -> AgentDef:
    """Parse an agent definition .md file.

    Expected format:
    ```
    ---
    name: agent-name
    description: What this agent does
    ---
    System prompt body...
    ```

    Raises:
        ValueError: if required fields are missing or frontmatter is malformed.
        FileNotFoundError: if the file does not exist.
    """
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Agent definition not found: {file_path}")

    content = path.read_text(encoding="utf-8")

    # Extract frontmatter between first and second ---
    lines = content.split("\n")
    frontmatter: dict[str, str] = {}
    in_fm = False
    fm_ended = False

    for line in lines:
        if line.strip() == "---":
            if not in_fm:
                in_fm = True
                continue
            else:
                fm_ended = True
                continue
        if in_fm and not fm_ended:
            if ":" in line:
                key, _, value = line.partition(":")
                frontmatter[key.strip()] = value.strip().strip('"').strip("'")

    if not frontmatter:
        raise ValueError(f"No YAML frontmatter found in {file_path}")

    name = frontmatter.get("name", "").strip()
    description = frontmatter.get("description", "").strip()

    if not name:
        raise ValueError(f"'name' is required in frontmatter of {file_path}")
    if not description:
        raise ValueError(f"'description' is required in frontmatter of {file_path}")

    body = content.strip()

    return AgentDef(
        name=name,
        description=description,
        body=body,
        file_path=str(path),
    )


def list_agents(agents_dir: str | Path) -> list[AgentDef]:
    """List all agent definitions in a directory.

    Returns:
        List of parsed AgentDef objects, skipping files that fail to parse.
    """
    directory = Path(agents_dir)
    if not directory.is_dir():
        return []

    agents: list[AgentDef] = []
    for f in sorted(directory.glob("*.md")):
        try:
            agents.append(parse_agent(f))
        except (ValueError, FileNotFoundError):
            pass
    return agents