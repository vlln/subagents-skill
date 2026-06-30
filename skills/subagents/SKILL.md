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

Commands below assume `scripts/subagents` is on PATH or invoked from the skill directory. Examples use the short form `subagents` for brevity.

Use `subagents` to manage agent sessions. An agent is an optional `.agents/subagents/<name>.md` file that defines a system prompt. A session is a named runtime instance. One agent can have many sessions.

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

See [`references/example-subagents.md`](references/example-subagents.md) for ready-to-use agent templates (code reviewer, debugger, architect, test writer, etc.).

## Workflow

```bash
# Create or resume a session
subagents run <agent> <session> <prompt>
subagents run <session> <prompt>                      # resume only, no agent

# Working directory isolation
subagents run --cwd <path> <agent> <session> <prompt> # run in specified directory
subagents run --bg --cwd /tmp/sandbox reviewer s1 "task"  # background + custom cwd

# System prompt mode (default: append)
subagents run --system-mode overwrite <agent> <session> <prompt>
subagents run --system-mode append <agent> <session> <prompt>

# Background execution with task queue (returns immediately)
subagents run --bg <agent> <session> <prompt>         # starts background worker
subagents send <session> "additional task"            # queue more tasks
subagents send <session> --prompt-file task.txt       # from file
echo "task 3" | subagents send <session>              # from stdin

# Queue management
subagents cancel <session> --task 2                   # cancel specific task
subagents cancel <session> --all                      # cancel all queued tasks

# Monitor
subagents wait <session>                              # block until all tasks done
subagents list                                         # all agents and sessions
subagents status <agent>                               # one agent's sessions
subagents status <agent> <session>                     # shows cwd, queue, tasks, goal

# Goal-driven autonomous execution
subagents goal <agent> <session> "<goal>"              # set goal and start worker
subagents goal --show <agent> <session>                # show goal progress
subagents goal --clear <agent> <session>               # cancel active goal
subagents goal --max-iterations 20 <agent> <session> "<goal>"
subagents cancel --goal <session>                      # cancel goal (same as --clear)
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
subagents run --bg --cwd .claude/worktrees/refactor reviewer s1 "analyze code"

# Queue additional tasks
subagents send s1 "refactor based on analysis"
subagents send s1 "update tests"
subagents send s1 "verify all pass"

# Monitor progress
subagents status reviewer s1

# Wait for completion
subagents wait s1

# Review and merge
cd .claude/worktrees/refactor
git diff
cd ~/project
git merge refactor
```

### Goal-Driven Execution

The `goal` command runs an agent in an autonomous loop, working toward a high-level objective. The agent self-evaluates progress and stops when the goal is met.

**How it works:**

1. **Set a goal** — the agent will iterate until the goal is met or max iterations reached. New sessions are created automatically; existing sessions are resumed.
2. **Self-evaluation**: each iteration, the agent assesses progress and decides the next step
3. **Completion signal**: when the goal is fully met, the agent writes `<GOAL_MET>` to a marker file
4. **Cancellation**: cancel with `goal --clear` or `cancel --goal`; the worker stops after the current iteration

**Goal and queue are mutually exclusive.** A session with an active goal cannot accept `send` tasks, and a session with a queue cannot set a goal.

**Goal Prompt Writing Guide:**

A good goal prompt should have **verifiable completion conditions**. The agent needs to be able to judge whether the goal is met.

| Good | Poor |
|------|------|
| "Implement JWT auth with login, logout, refresh. All tests must pass." | "Improve the codebase" |
| "Refactor src/auth.py to use the new token format. Existing tests must pass." | "Make auth better" |
| "Add input validation to all API endpoints. No unhandled edge cases." | "Add validation" |
| "Migrate all React components from class to hooks. Build must succeed." | "Update components" |

Key principles:
- **Specific deliverable**: What exactly should exist when the goal is met?
- **Verifiable condition**: How can the agent check if it's done? (tests pass, build succeeds, file exists, etc.)
- **Scope boundary**: What's in scope and what's out of scope?
- **Not necessarily quantifiable**: "All tests pass" is binary; "Clean, readable code" is subjective but still verifiable by the agent

**Example workflow:**

```bash
# Goal as standalone (no prior run needed)
subagents goal --cwd /tmp/sandbox reviewer auth-system \
  "Implement complete JWT authentication: login endpoint, token refresh, logout. \
   All tests must pass. Update API docs."

# Or, resume an existing session with a goal
subagents run reviewer auth-system "Analyze the current auth module"
subagents goal reviewer auth-system "Now implement JWT auth for this module"

# Monitor goal progress
subagents status reviewer auth-system

# Cancel the goal mid-way
subagents goal --clear reviewer auth-system

# Wait for goal completion
subagents wait auth-system
```

**Max iterations** (default: 10) prevents infinite loops:

```bash
subagents goal --max-iterations 20 reviewer auth-system "Implement full test suite"
```

If max iterations is reached, the goal is marked as `failed` with reason `max_iterations`.

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
subagents run --output json <session> <prompt>
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
- Goal and queue are mutually exclusive — a session can have one or neither, not both.
- Goal completion is detected via `<GOAL_MET>` marker file written by the agent.

## Patterns

Parallel swarm:

```bash
subagents run --bg reviewer r1 "Review src/auth.ts"
subagents run --bg reviewer r2 "Review src/db.ts"
subagents run --bg reviewer r3 "Review src/api.ts"
subagents wait r1
subagents wait r2
subagents wait r3
```

Context accumulation:

```bash
subagents run architect design "Design the architecture"
subagents run architect design "Add a caching layer"
subagents run architect design "Handle error recovery"
```

Autonomous goal-driven:

```bash
# Standalone: goal creates its own session
subagents goal --cwd /tmp/sandbox reviewer auth-review \
  "Refactor the authentication module: JWT-based, all tests pass, API docs updated"
subagents wait auth-review

# Or: resume an existing session with a goal
subagents run reviewer auth-review "Initial analysis of auth module"
subagents goal reviewer auth-review \
  "Refactor: JWT-based, all tests pass, API docs updated"
subagents wait auth-review
```