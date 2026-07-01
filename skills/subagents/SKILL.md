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

Use `subagents` to manage agent sessions. An agent is an optional
`.agents/subagents/<name>.md` file that defines a system prompt. A session
is a named runtime instance. One agent can have many sessions.

## Trigger Keywords

subagent, session, dispatch, parallel, background, goal-driven, agent,
delegate, multi-agent

## When To Use

- Split a large task across multiple agents working in parallel.
- Run a long-running agent that accumulates context across multiple turns.
- Assign a specific role (reviewer, architect, explorer) to a dedicated
  agent.

## Agent Definition (optional)

Create `.agents/subagents/<name>.md`:

```markdown
---
name: reviewer
description: Expert code reviewer
---
You are a code reviewer. Analyze code for correctness, security, and best
practices.
```

- `name` (required) — unique identifier.
- `description` (required) — shown in `list` and `status`.
- Body (optional) — system prompt. Omit to use the backend's default.

Pre-built agent definitions are available in `references/agents/` — copy
them to `.agents/subagent/` to use directly.

## Prompt Writing

A good prompt has four elements: **goal**, **scope**, **steps**, and
**output format**. The agent works independently — give it everything it
needs to complete the task without asking questions.

| Element | Purpose |
|---------|---------|
| **Goal** | One sentence describing the task |
| **Scope** | What to look at, what to skip, any constraints |
| **Steps** | Ordered list guiding execution (3-10 steps) |
| **Output** | Expected structure or format |

**Granularity:** a task should be self-contained with clear boundaries. Too
small (one trivial step) causes overhead. Too large (entire project) loses
focus. Aim for 3-10 steps that the agent can complete independently.

**Good:**

```
Review src/auth/ for security vulnerabilities.

Scope: src/auth/ directory only, ignore test files.

Steps:
1. Find all files in src/auth/
2. Check for SQL injection in database queries
3. Verify token storage and handling
4. Review OAuth implementation against OWASP guidelines
5. Check for missing rate limiting on login endpoints

Return a list of findings with:
- file path and line number
- severity: critical | warning | note
- description of the issue
- concrete fix suggestion
```

**Poor:**

```
Check the code
```

**When to use structured output:**

If the result will be piped to another tool or parsed programmatically, ask
for JSON:

```
Return as JSON:
{
  "files_reviewed": [...],
  "findings": [
    {"file": "...", "line": 123, "severity": "high", "issue": "...", "fix": "..."}
  ],
  "summary": "..."
}
```

## CLI Commands

```bash
# Create or resume a session
subagents run <agent> <session> <prompt>
subagents run <session> <prompt>                      # resume only, no agent

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
subagents status <agent> <session>                     # detailed session status

# Goal-driven autonomous execution
subagents goal <agent> <session> "<goal>"              # set goal and start worker
subagents goal --show <agent> <session>                # show goal progress
subagents goal --clear <agent> <session>               # cancel active goal
subagents goal --max-iterations 20 <agent> <session> "<goal>"
```

First run creates the session; subsequent runs with the same session name
resume it, preserving all prior context.

### Task Queue (Background Mode)

Background sessions (`--bg`) support task queuing:

1. **Initial task**: Executed immediately when session starts
2. **Queue tasks**: Use `send` to add tasks while session is running
3. **Sequential execution**: Tasks execute one after another in order
4. **Persistent context**: All tasks share the same session context

Example workflow:

```bash
# Start background session in a worktree
git worktree add /tmp/worktrees/refactor -b refactor
subagents run --bg --cwd /tmp/worktrees/refactor reviewer s1 "analyze code"

# Queue additional tasks
subagents send s1 "refactor based on analysis"
subagents send s1 "update tests"
subagents send s1 "verify all pass"

# Monitor progress
subagents status reviewer s1

# Wait for completion
subagents wait s1
```

### Goal-Driven Execution

The `goal` command runs an agent autonomously toward a high-level
objective. The agent self-evaluates progress and stops when the goal is met
or max iterations reached.

**Goal Prompt Writing Guide:**

A good goal prompt must have **verifiable completion conditions**. The
agent needs to be able to judge whether the goal is met.

| Good | Poor |
|------|------|
| "Implement JWT auth with login, logout, refresh. All tests must pass." | "Improve the codebase" |
| "Refactor src/auth.py to use the new token format. Existing tests must pass." | "Make auth better" |
| "Add input validation to all API endpoints. No unhandled edge cases." | "Add validation" |
| "Migrate all React components from class to hooks. Build must succeed." | "Update components" |

Key principles:

- **Specific deliverable**: What exactly should exist when the goal is met?
- **Verifiable condition**: How can the agent check if it's done? (tests
  pass, build succeeds, file exists, etc.)
- **Scope boundary**: What's in scope and what's out of scope?

**Max iterations** (default: 10) prevents infinite loops:

```bash
subagents goal --max-iterations 20 reviewer auth-system "Implement full test suite"
```

If max iterations is reached, the goal is marked as `failed` with reason
`max_iterations`.

## Backends

Use `--backend <name>` to select the provider. Auto-detected if omitted.
`--model <name>` overrides the model for a single invocation.

Supported backends: `kimi`, `claude`, `codex`, `pi`, `opencode`, `qwen`,
`kiro`, `gemini`.

## Patterns

**Parallel swarm:**

```bash
subagents run --bg reviewer r1 "Review src/auth.ts"
subagents run --bg reviewer r2 "Review src/db.ts"
subagents run --bg reviewer r3 "Review src/api.ts"
subagents wait r1
subagents wait r2
subagents wait r3
```

**Context accumulation:**

```bash
subagents run architect design "Design the architecture"
subagents run architect design "Add a caching layer"
subagents run architect design "Handle error recovery"
```

**Autonomous goal-driven:**

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

## Gotchas

- Session names are chosen by you. Use descriptive names (`review-auth`,
  `explore-codebase`).
- A session cannot run concurrently — a lock prevents it.
- Goal and queue are mutually exclusive. A session with an active goal
  cannot accept `send` tasks, and a session with a queue cannot set a goal.
- Vague prompts ("check the code") produce poor results. Give the agent a
  clear goal, scope, steps, and output format.
- Tasks should be self-contained with clear boundaries. Too small causes
  overhead; too large loses focus. Aim for 3-10 steps.
- The registry `.agents/subagents/agents.json` is auto-maintained; do not
  edit it.
- `run` output goes to stdout, diagnostics to stderr.