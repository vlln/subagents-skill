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
scripts/subagents run <agent> <session> <prompt>
scripts/subagents run <session> <prompt>                      # resume only, no agent

# Working directory isolation
scripts/subagents run --cwd <path> <agent> <session> <prompt> # run in specified directory
scripts/subagents run --bg --cwd /tmp/sandbox reviewer s1 "task"  # background + custom cwd

# System prompt mode (default: append)
scripts/subagents run --system-mode overwrite <agent> <session> <prompt>
scripts/subagents run --system-mode append <agent> <session> <prompt>

# Background execution with task queue (returns immediately)
scripts/subagents run --bg <agent> <session> <prompt>         # starts background worker
scripts/subagents send <session> "additional task"            # queue more tasks
scripts/subagents send <session> --prompt-file task.txt       # from file
echo "task 3" | scripts/subagents send <session>              # from stdin

# Queue management
scripts/subagents cancel <session> --task 2                   # cancel specific task
scripts/subagents cancel <session> --all                      # cancel all queued tasks

# Monitor
scripts/subagents wait <session>                              # block until all tasks done
scripts/subagents list                                         # all agents and sessions
scripts/subagents status <agent>                               # one agent's sessions
scripts/subagents status <agent> <session>                     # shows cwd, queue, tasks
```

First run creates the session; subsequent runs with the same session name resume it, preserving all prior context.

### Task Queue (Background Mode)

Background sessions (`--bg`) support task queuing:
1. **Initial task**: Executed immediately when session starts
2. **Queue tasks**: Use `send` to add tasks while session is running
3. **Sequential execution**: Tasks execute one after another in order
4. **Persistent context**: All tasks share the same session context
5. **Working directory**: All tasks in the queue execute in the session's `--cwd` (if specified)

Example workflow:
```bash
# Start background session in a worktree
git worktree add .claude/worktrees/refactor -b refactor
scripts/subagents run --bg --cwd .claude/worktrees/refactor reviewer s1 "analyze code"

# Queue additional tasks
scripts/subagents send s1 "refactor based on analysis"
scripts/subagents send s1 "update tests"
scripts/subagents send s1 "verify all pass"

# Monitor progress
scripts/subagents status reviewer s1

# Wait for completion
scripts/subagents wait s1

# Review and merge
cd .claude/worktrees/refactor
git diff
cd ~/project
git merge refactor
```

## Backends

`--backend <name>` selects the provider. Auto-detected if omitted. `--transport cli|acp` forces transport mode for backends that support both.

`--system-mode append|overwrite` controls how the agent's system prompt is handled:

- **append** (default): The agent body is appended to the backend's default system prompt. Backends that support `--append-system-prompt` (claude, pi, qwen) use it natively; others prepend to the user prompt.
- **overwrite**: The agent body replaces the backend's default system prompt. Backends that support `--system-prompt` (claude, pi, qwen) use it natively; others prepend to the user prompt.

| Backend | Command | Transports | System Prompt | Notes |
|---------|---------|-----------|---------------|-------|
| `kimi` | `kimi` | CLI, ACP | inline | |
| `claude` | `claude` | CLI | native (append + overwrite) | |
| `codex` | `codex` | CLI | inline | |
| `pi` | `pi` | CLI | native (append + overwrite) | |
| `opencode` | `opencode` | CLI, ACP | inline | |
| `qwen` | `qwen` | CLI, ACP | native (append + overwrite) | |
| `kiro` | `kiro-cli` | CLI, ACP | inline | |
| `gemini` | `gemini` | CLI, ACP | inline | |

## JSONL Output

Use `--output json` for structured, machine-readable output. Every stream starts with a version event.

```bash
scripts/subagents run --output json <session> <prompt>
# {"type":"version","version":1}
# {"type":"agent_start","session":"s1","agent":null,"backend":"kimi"}
# {"type":"agent_text","session":"s1","content":"..."}
# {"type":"agent_done","session":"s1","exit_code":0}
```

All commands support `--output json`: `run`, `run --bg`, `wait`, `list`, `status`.

See [`references/schema-v1.json`](references/schema-v1.json) for the full JSON Schema.

## Rules

- Session names are chosen by you. Use descriptive names (`review-auth`, `explore-codebase`).
- A session cannot run concurrently — the lock prevents it.
- Stale locks (30+ min) are cleaned up automatically.
- The registry `.agents/subagents/agents.json` is auto-maintained; do not edit it.
- `run` output goes to stdout, `[subagent]` diagnostics to stderr.

## Patterns

Parallel swarm:

```bash
scripts/subagents run --bg reviewer r1 "Review src/auth.ts"
scripts/subagents run --bg reviewer r2 "Review src/db.ts"
scripts/subagents run --bg reviewer r3 "Review src/api.ts"
scripts/subagents wait r1
scripts/subagents wait r2
scripts/subagents wait r3
```

Context accumulation:

```bash
scripts/subagents run architect design "Design the architecture"
scripts/subagents run architect design "Add a caching layer"
scripts/subagents run architect design "Handle error recovery"
```