# Subagents

Agent Skills for dispatching tasks to AI coding agents across multiple backends.

- **One interface, eight backends.** kimi, claude, codex, pi, opencode, qwen, kiro, and gemini — same `subagents run` command for all.
- **Sessions persist.** Each run creates a named session. Resume it later and the agent remembers all prior context.
- **Parallel swarms.** Run multiple agents in background with `--bg`, wait for all to finish with `subagents wait`.
- **Workflow orchestration.** `pipeline()`, `parallel()`, and nested `workflow()` for multi-agent workflows with resume support.
- **Auto-detection.** Backend and transport (CLI or [ACP](https://agentclientprotocol.com)) are auto-detected. Override with `--backend` and `--transport`.
- **JSONL output.** `--output json` for structured, streamable, versioned JSONL output.
- **Zero dependencies.** Python 3.10+ standard library only. No pip install, no virtualenv.

## Supported Backends

| Backend | Command | Transports | System Prompt | Status |
|---------|---------|-----------|---------------|--------|
| `kimi` | `kimi` | CLI, ACP | inline | Tested |
| `claude` | `claude` | CLI | native (append + overwrite) | Tested |
| `codex` | `codex` | CLI | inline | Tested |
| `pi` | `pi` | CLI | native (append + overwrite) | Tested |
| `opencode` | `opencode` | CLI, ACP | inline | Tested |
| `qwen` | `qwen` | CLI, ACP | native (append + overwrite) | Tested |
| `kiro` | `kiro-cli` | CLI, ACP | inline | Untested |
| `gemini` | `gemini` | CLI, ACP | inline | Untested |

Backend and transport are auto-detected. Override with `--backend <name>` and `--transport cli|acp`.
Use `--system-mode append` (default) or `--system-mode overwrite` to control how the agent's system prompt is applied.

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
| [subagents](skills/subagents/SKILL.md) | Dispatch tasks to named agent sessions across multiple backends. Supports session resume, parallel swarms, and JSONL output. |
| [workflow](skills/workflow/SKILL.md) | Multi-agent orchestration with pipeline, parallel, and phase-based workflows. Nested sub-workflows, resume, and structured output. |

## Development

Python 3.10+, zero dependencies. Tests use only the standard library.

```bash
# Unit tests (no backend required, CI-safe)
python3 -m pytest tests/ --ignore=tests/test_integration.py

# Integration tests (requires kimi CLI)
SKIP_INTEGRATION=0 python3 -m pytest tests/test_integration.py -v

# All tests
SKIP_INTEGRATION=0 python3 -m pytest tests/ -v
```

| Layer | File | Count | Trigger | Time |
|-------|------|-------|---------|------|
| Unit | `tests/test_subagents.py` | 67 | `pytest` auto | <1s |
| Unit | `tests/test_workflow.py` | 20 | `pytest` auto | <1s |
| Integration | `tests/test_integration.py` | 8 | `SKIP_INTEGRATION=0` | ~3min |

## License

MIT
