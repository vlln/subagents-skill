---
name: subagents
description: Dispatch tasks to named agent sessions across multiple backends. Define agents as Markdown files with YAML frontmatter, run tasks on them, list agents and sessions, and check status. Use when you need to delegate work to sub-agents for parallel execution or long-running sessions.
license: MIT
metadata:
  skit:
    version: 0.1.0
    requires:
      bins:
        - python3
      platforms:
        os:
          - linux
    keywords:
      - subagent
      - session
      - dispatch
      - parallel
---

# Subagent

Use `scripts/subagents` to manage agent sessions. An agent is an optional `.agents/subagents/<name>.md` file that defines a system prompt. A session is a named runtime instance. One agent can have many sessions.

## When To Use

- Split a large task across multiple agents working in parallel.
- Run a long-running agent that accumulates context across multiple turns.
- Assign a specific role (reviewer, architect, explorer) to a dedicated agent.

## Agent Definition (optional)

Create `.agents/subagents/<name>.md`:

```markdown
---
name: reviewer
description: Expert code reviewer
---
You are a code reviewer. Analyze code for correctness, security, and best practices.
```

- `name` (required) — unique identifier.
- `description` (required) — shown in `list` and `status`.
- Body (optional) — system prompt. Omit to use the backend's default.

## Workflow

```bash
# Create or resume a session
scripts/subagentss run <agent> <session> <prompt>
scripts/subagentss run <session> <prompt>                      # resume only, no agent

# Background execution (returns immediately, output to .agents/subagents/outputs/)
scripts/subagentss run --bg <agent> <session> <prompt>
scripts/subagentss wait <session>                               # block until done

# Monitor
scripts/subagentss list                                         # all agents and sessions
scripts/subagentss status <agent>                               # one agent's sessions
scripts/subagentss status <agent> <session>                     # session details + task history
```

First run creates the session; subsequent runs with the same session name resume it, preserving all prior context.

## Backends

`--backend <name>` selects the provider. Auto-detected if omitted. `--transport cli|acp` forces transport mode for backends that support both.

| Backend | Command | Transports | Notes |
|---------|---------|-----------|-------|
| `kimi` | `kimi` | CLI, ACP | |
| `claude` | `claude` | CLI | |
| `codex` | `codex` | CLI | |
| `pi` | `pi` | CLI | |
| `opencode` | `opencode` | CLI, ACP | |
| `qwen` | `qwen` | CLI, ACP | |
| `kiro` | `kiro-cli` | CLI, ACP | |

## Rules

- Session names are chosen by you. Use descriptive names (`review-auth`, `explore-codebase`).
- A session cannot run concurrently — the lock prevents it.
- Stale locks (30+ min) are cleaned up automatically.
- The registry `.agents/subagents/agents.json` is auto-maintained; do not edit it.
- `run` output goes to stdout, `[subagent]` diagnostics to stderr.

## Patterns

Parallel swarm:

```bash
scripts/subagentss run --bg reviewer r1 "Review src/auth.ts"
scripts/subagentss run --bg reviewer r2 "Review src/db.ts"
scripts/subagentss run --bg reviewer r3 "Review src/api.ts"
scripts/subagentss wait r1
scripts/subagentss wait r2
scripts/subagentss wait r3
```

Context accumulation:

```bash
scripts/subagentss run architect design "Design the architecture"
scripts/subagentss run architect design "Add a caching layer"
scripts/subagentss run architect design "Handle error recovery"
```