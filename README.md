# Subagents

Agent Skills for dispatching tasks to AI coding agents across multiple backends.

- **One interface, seven backends.** kimi, claude, codex, pi, opencode, qwen, and kiro — same `subagents run` command for all.
- **Sessions persist.** Each run creates a named session. Resume it later and the agent remembers all prior context.
- **Parallel swarms.** Run multiple agents in background with `--bg`, wait for all to finish with `subagents wait`.
- **Auto-detection.** Backend and transport (CLI or [ACP](https://agentclientprotocol.com)) are auto-detected. Override with `--backend` and `--transport`.
- **Zero dependencies.** Python 3.10+ standard library only. No pip install, no virtualenv.

## Supported Backends

| Backend | Command | Transports | Status |
|---------|---------|-----------|--------|
| `kimi` | `kimi` | CLI, ACP | Tested |
| `claude` | `claude` | CLI | Tested |
| `codex` | `codex` | CLI | Tested |
| `pi` | `pi` | CLI | Tested |
| `opencode` | `opencode` | CLI, ACP | Tested |
| `qwen` | `qwen` | CLI, ACP | Tested |
| `kiro` | `kiro-cli` | CLI, ACP | Untested |

Backend and transport are auto-detected. Override with `--backend <name>` and `--transport cli|acp`.

These skills follow the [Agent Skills specification](https://agentskills.io/specification) so they can be used by any skills-compatible agent, including Claude Code, Codex, and Open Code.

## Installation

### [skit](https://github.com/vlln/skit)

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
