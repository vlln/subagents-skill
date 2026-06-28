---
name: workflow
description: Multi-agent orchestration with pipeline, parallel, and phase-based workflows. Dispatch sub-tasks to agents, run them concurrently or in pipelines, and synthesize results.
license: MIT
metadata:
  skit:
    version: 0.1.0
    requires:
      skills:
        - subagents
      env:
        SKILL_SUBAGENTS_HOME: "Path to the subagents skill installation directory"
      bins:
        - python3
      platforms:
        os:
          - linux
    keywords:
      - workflow
      - orchestration
      - pipeline
      - parallel
      - multi-agent
---

# Workflow

Use `scripts/workflow` to orchestrate multiple AI agents in parallel or pipeline patterns.

**Quick start:**

```bash
# 1. Agent writes the script
cat > .agents/workflow/hello.py << 'EOF'
meta = {"name": "hello", "description": "Simple workflow"}
def run(agent, parallel, pipeline, phase, log, args, workflow):
    phase("Greet")
    return {"result": agent("say hello")}
EOF

# 2. Agent executes it
scripts/workflow run .agents/workflow/hello.py
```

## Dependencies

Requires the [subagents](https://github.com/vlln/subagents-skill/blob/main/skills/subagents/SKILL.md) skill. Set `SKILL_SUBAGENTS_HOME` to its installation path.

## When To Use

- Split a large task across multiple agents working in parallel.
- Run multi-stage pipelines (scan → analyze → fix → verify).
- Fan-out analysis across dimensions (security, performance, style) then synthesize.
- Discover work items dynamically, then process them in batches.
- Adversarial verify: generate → review → refine cycles.

## How To Use

### 1. Write the script

The agent writes the workflow script to a file. Use `.agents/workflow/` for project-scoped scripts or `/tmp/` for one-off runs.

```python
# .agents/workflow/code-review.py
meta = {"name": "code-review", "description": "..."}

def run(agent, parallel, pipeline, phase, log, args, workflow):
    ...
```

### 2. Execute

```bash
scripts/workflow run .agents/workflow/code-review.py
```

With arguments:

```bash
scripts/workflow run .agents/workflow/code-review.py --args '{"target": "src/"}'
```

### 3. Resume on failure

If the workflow crashes (timeout, agent failure), re-run with the same `--resume` ID. Completed sessions are skipped.

```bash
# First run
scripts/workflow run script.py --resume myrun001

# Crash... resume:
scripts/workflow resume myrun001 script.py
```

### 4. Check results

`run()` returns a dict which is printed as JSON to stdout. Diagnostics go to stderr.

```json
{"status": "complete", "findings": 3, "summary": "..."}
```

## Workflow Script

A workflow script is a Python file that defines `run()`:

```python
# my_workflow.py

meta = {
    "name": "code-review",
    "description": "Multi-perspective code review",
    "phases": [
        {"title": "Scan", "detail": "Identify changed files"},
        {"title": "Review", "detail": "Parallel review across dimensions"},
        {"title": "Synthesis", "detail": "Consolidate findings"},
    ],
}

def run(agent, parallel, pipeline, phase, log, args, workflow):
    phase("Scan")
    files = agent("List changed files in git diff")

    phase("Review")
    reviews = parallel([
        lambda: agent("Review for security issues"),
        lambda: agent("Review for performance issues"),
        lambda: agent("Review for code quality"),
    ])

    phase("Synthesis")
    summary = agent(f"Synthesize:\n{reviews}")
    return {"status": "done", "summary": summary}
```

## API Reference

### agent(prompt, *, schema=None, label=None, model=None, backend=None)

Run a single subagent. Returns text output, or structured dict if `schema` is provided.

| Param | Type | Description |
|-------|------|-------------|
| `prompt` | `str` | The task to run |
| `schema` | `dict` | Optional JSON Schema for structured output |
| `label` | `str` | Display label in the live panel |
| `model` | `str` | Override the model |
| `backend` | `str` | Override the backend (e.g. `"claude"`, `"kimi"`) |

```python
# Simple text
result = agent("Find all test files")

# With a specific backend
result = agent("Review for security", backend="claude")

# Structured output
result = agent("Find all test files", schema={
    "type": "object",
    "properties": {"files": {"type": "array", "items": {"type": "string"}}}
})
```

### parallel(thunks)

Run thunks concurrently. Returns list of results in input order. Failed thunks return `None`.

```python
results = parallel([
    lambda: agent("Security review"),
    lambda: agent("Performance review"),
    lambda: agent("Style review"),
])
# results = ["...", "...", "..."]
```

### pipeline(items, *stages)

Process items through stages. Each item flows independently — no barrier between stages.

```python
results = pipeline(
    ["file1.py", "file2.py"],

    # Stage 1: receives (item, index)
    lambda item, idx: agent(f"Analyze {item}"),

    # Stage 2: receives (prev_result, original_item, index)
    lambda prev, item, idx: agent(f"Fix {item}: {prev}"),
)
```

### phase(title)

Log a phase transition to stderr.

```python
phase("Scan")
```

### log(message)

Log a progress message to stderr.

```python
log(f"Processing {len(items)} files")
```

## Patterns

### Fan-out Gather

```python
phase("Analyze")
results = parallel([
    lambda: agent("Check security", backend="claude"),
    lambda: agent("Check performance", backend="kimi"),
])

phase("Summarize")
summary = agent(f"Summarize: {results}")
```

### Pipeline

```python
results = pipeline(
    file_list,
    lambda f, i: agent(f"Parse {f}"),
    lambda ast, f, i: agent(f"Transform {f}"),
    lambda t, f, i: agent(f"Validate {f}"),
)
```

### Adversarial Verify

```python
phase("Generate")
solution = agent("Generate solution")

phase("Verify")
flaws = agent(f"Find flaws in: {solution}")

if flaws:
    phase("Refine")
    solution = agent(f"Fix: {flaws}")
```

## Monitoring

### Live Display

During execution, workflow renders a live status panel (TTY) or compact status lines (non-TTY):

```
[workflow] code-review (wf_a3f2) 0.0s | ⠙ Scan
[workflow] code-review (wf_a3f2) 1.5s | ✓ Scan | ⠼ Review
[workflow] code-review (wf_a3f2) 4.3s | ✓ Scan | ✓ Review | ⠸ Synthesize
```

On completion, a summary table is printed:

```
══ Workflow Summary: code-review ══

  Duration: 5.3s    Phases: 3    Sessions: 6
  ✓ 5 done    ✗ 1 failed    ○ 0 skipped

  ✓ Scan  1.5s
     ✓ Scan files  1.5s

  ✓ Review  2.8s  (1 failed)
     ✓ Security review  1.0s
     ✗ Performance review  2.3s
     ✓ Style review  1.5s
     ✓ Architecture review  2.8s

  ✓ Synthesize  1.0s
     ✓ Synthesize  1.0s
```

### CLI Commands

```bash
# List all workflow runs with session status
scripts/workflow list
# Run: abc123
#   ✓ wf_abc123_1  [done]  Review security
#   … wf_abc123_2  [running]  Review performance

# Detailed status of a single run
scripts/workflow status abc123
# Run: abc123
# Sessions: 1 done, 1 running, 0 failed (total 2)

# Stop a running workflow
scripts/workflow stop abc123
# Stopped 1 session(s) in run 'abc123'.
```

## Mock Mode (Testing)

For testing without a real backend, enable mock mode at the top of your workflow script:

```python
from runtime import set_mock
set_mock("shell")

# agent() now executes the prompt as a shell command
agent("echo security ok")          # → "security ok"
agent("sleep 2 && echo done")      # → "done" (2s delay)
```

Set `set_mock(None)` to restore real subagent execution.

## Rules

- Each `agent()` call creates a session via `subagents run --bg --output json`.
- Use `backend` to select a specific backend per agent: `agent("...", backend="claude")`.
- Sessions are named `wf_<uuid>` and cleaned up automatically.
- `parallel()` uses threads; all agents run concurrently.
- `pipeline()` has no inter-stage barrier — items flow independently.
- Return `None` from `run()` to suppress JSON output.
- Diagnostics and live display go to stderr; workflow output goes to stdout.
