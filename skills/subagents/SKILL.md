---
name: subagents
description: Use this skill when dispatching tasks to named agent sessions across multiple backends. Define agents as Markdown files with YAML frontmatter, run tasks on them, list agents and sessions, and check status. Use for parallel execution or long-running sessions.
license: MIT
metadata:
  author: vlln
  version: "0.1.0"
requires:
  bins:
    - python3
---

# Subagent

Manage agent sessions. An agent is an optional `.agents/subagents/<name>.md` file
defining a system prompt. A session is a named runtime instance. One agent can have
many sessions.

## Trigger Keywords

subagent, session, dispatch, parallel, background, goal-driven, agent, delegate,
multi-agent

## Capabilities

- **Named sessions**: Create or resume sessions that preserve context across calls.
- **Background execution**: Run agents with a sequential task queue. Queue more
  tasks while the session is running.
- **Goal-driven mode**: Give a high-level goal; the agent self-evaluates and stops
  when complete or max iterations reached.
- **Multi-backend**: Works with kimi, claude, codex, pi, opencode, qwen, kiro,
  gemini. Auto-detected; override with `--backend`.
- **Agent definitions**: Pre-built agent files in `references/agents/`.

## Agent Definition

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
- Body (optional) — system prompt. Omit to use the backend default.

Pre-built definitions live in `references/agents/`. Copy to `.agents/subagents/`
to use.

## CLI Commands

```bash
# Create or resume a session
subagents run <agent> <session> <prompt>
subagents run <session> <prompt>                      # resume only, no agent

# Background execution with task queue
subagents run --bg <agent> <session> <prompt>
subagents send <session> "additional task"
subagents send <session> --prompt-file task.txt
echo "task 3" | subagents send <session>

# Queue management
subagents cancel <session> --task 2
subagents cancel <session> --all

# Monitor
subagents wait <session>
subagents list
subagents status <agent>
subagents status <agent> <session>

# Goal-driven
subagents goal <agent> <session> "<goal>"
subagents goal --show <agent> <session>
subagents goal --clear <agent> <session>
subagents goal --max-iterations 20 <agent> <session> "<goal>"
```

First run creates the session; subsequent runs with the same session name resume
it, preserving all prior context.

### Task Queue

Background sessions (`--bg`) support sequential task queuing:

1. Initial task executes immediately when the session starts.
2. Queue more tasks with `send` while the session is running.
3. Tasks execute one after another, sharing session context.

```bash
git worktree add /tmp/worktrees/refactor -b refactor
subagents run --bg --cwd /tmp/worktrees/refactor reviewer s1 "analyze code"
subagents send s1 "refactor based on analysis"
subagents send s1 "update tests"
subagents send s1 "verify all pass"
subagents wait s1
```

### Goal-Driven Execution

The `goal` command runs an agent autonomously toward a high-level objective. The
agent self-evaluates and stops when the goal is met or max iterations (default 10)
is reached.

A goal prompt must have **verifiable completion conditions** — the agent must be
able to judge whether the goal is met.

| Good | Poor |
|------|------|
| "Implement JWT auth with login, logout, refresh. All tests must pass." | "Improve the codebase" |
| "Refactor src/auth.py to use the new token format. Existing tests must pass." | "Make auth better" |
| "Add input validation to all API endpoints. No unhandled edge cases." | "Add validation" |

If max iterations is reached, the goal is marked `failed` with reason
`max_iterations`.

## Patterns

**Parallel swarm:**

```bash
subagents run --bg reviewer r1 "Review src/auth.ts"
subagents run --bg reviewer r2 "Review src/db.ts"
subagents run --bg reviewer r3 "Review src/api.ts"
subagents wait r1 && subagents wait r2 && subagents wait r3
```

**Context accumulation:**

```bash
subagents run architect design "Design the architecture"
subagents run architect design "Add a caching layer"
subagents run architect design "Handle error recovery"
```

**Autonomous goal-driven:**

```bash
subagents goal --cwd /tmp/sandbox reviewer auth-review \
  "Refactor the authentication module: JWT-based, all tests pass, API docs updated"
subagents wait auth-review
```

## Gotchas

- A session cannot run concurrently — a lock prevents it.
- Goal and task queue are mutually exclusive. A session with an active goal cannot
  accept `send` tasks, and a session with a queue cannot set a goal.
- The registry `.agents/subagents/agents.json` is auto-maintained; do not edit it.
- `run` output goes to stdout, diagnostics to stderr.
- Goal mode requires verifiable completion conditions. Vague goals cause the agent
  to hit max iterations without finishing.
- Session names persist across runs. Reusing a name resumes the session with all
  prior context.