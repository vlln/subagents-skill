# Subagents

Agent Skills for dispatching tasks to AI coding agents across multiple backends.

These skills follow the [Agent Skills specification](https://agentskills.io/specification) so they can be used by any skills-compatible agent, including Claude Code, Codex, and Open Code.

## Installation

### skit

```bash
skit install ./subagents-skills --all
```

### Manually — Claude Code

Copy the `skills/` directory into your Claude Code skills path (typically `.claude/skills/`):

```bash
cp -r skills/subagents .claude/skills/
```

### Manually — Codex

Copy the `skills/` directory into your Codex skills path (typically `~/.codex/skills`):

```bash
cp -r skills/subagents ~/.codex/skills/
```

### Manually — OpenCode

Clone the repo into the OpenCode skills directory:

```bash
git clone https://github.com/your-org/subagents-skills.git ~/.opencode/skills/subagents-skills
```

## Skills

| Skill | Description |
|-------|-------------|
| [subagents](skills/subagents/SKILL.md) | Dispatch tasks to named agent sessions across multiple backends (kimi, claude, codex, pi, opencode, qwen, kiro). Supports session resume, parallel swarms, and background execution. |

## License

MIT