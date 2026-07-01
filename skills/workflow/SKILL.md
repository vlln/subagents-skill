---
name: workflow
description: Use this skill when orchestrating multiple AI agents with parallel, pipeline, or phase-based workflows. Dispatch sub-tasks to agents, run them concurrently or in pipelines, and synthesize results.
license: MIT
metadata:
  author: vlln
  version: "0.1.0"
requires:
  skills:
    - subagents
  bins:
    - python3
---

# Workflow

Use `workflow` to orchestrate multiple AI agents in parallel or pipeline
patterns. A workflow script is a Python file that defines
`run(agent, parallel, pipeline, phase, log, args, workflow)` — write the
script, then execute it.

## Trigger Keywords

workflow, orchestration, pipeline, parallel, multi-agent, fan-out, bulk
processing, multi-stage, phase-based

## When To Use

- Multi-perspective analysis: fan out across dimensions (security,
  performance, style) then synthesize.
- Multi-stage pipelines: scan, analyze, fix, verify — each stage builds on
  the previous.
- Bulk operations: migrate, audit, or transform many files at once.
- Adversarial verification: generate a solution, have another agent find
  flaws, refine.
- Discovery-driven work: first find what needs doing, then process each
  item.

Don't use for simple single-file edits or tasks a single agent call can
handle.

## Quick Start

```bash
# 1. Write the script
cat > .agents/workflow/hello.py << 'EOF'
meta = {"name": "hello", "description": "Simple workflow"}
def run(agent, parallel, pipeline, phase, log, args, workflow):
    phase("Greet")
    return {"result": agent("say hello")}
EOF

# 2. Execute
workflow run .agents/workflow/hello.py
```

## Script Structure

```python
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
    return {"status": "done", "summary": agent(f"Synthesize:\n{reviews}")}
```

`meta` must be a pure literal — no variables, function calls, or template
strings. `phase()` titles must match `meta.phases` titles exactly.

## API Reference

### agent(prompt, *, schema=None, label=None, backend=None, model=None)

Run a single subagent. Returns the agent's text output as a string, or a
validated dict if `schema` is provided.

| Param | Type | Description |
|-------|------|-------------|
| `prompt` | `str` | The task to run |
| `schema` | `dict` | Optional JSON Schema — the agent is forced to produce structured output matching this schema, and the return value is validated |
| `label` | `str` | Display label in the live progress panel |
| `backend` | `str` | Override the backend (e.g. `"claude"`, `"kimi"`) |
| `model` | `str` | Override the model for this agent call |

Returns `None` if the agent fails. Always filter results:
`[r for r in results if r is not None]`.

```python
# Simple text
result = agent("Find all test files")

# Structured output
result = agent("Find test files", schema={
    "type": "object",
    "properties": {"files": {"type": "array", "items": {"type": "string"}}},
    "required": ["files"],
})
# result["files"] → validated list of strings

# Specific backend and model
result = agent("Review code", backend="claude", model="sonnet")
```

### parallel(thunks)

Run multiple thunks concurrently. Each thunk is a zero-argument callable
(typically a `lambda:`). Returns a list of results in input order. Failed
thunks produce `None` instead of raising.

```python
results = parallel([
    lambda: agent("Security review"),
    lambda: agent("Performance review"),
    lambda: agent("Style review"),
])
# results = ["security ok", "perf: 3 issues", None]  # style review failed
```

### pipeline(items, *stages)

Process each item through all stages independently. No barrier: item A can
be in stage 3 while item B is still in stage 1. Each stage callback
receives `(prev_result, original_item, index)`. If a stage raises or
returns `None`, remaining stages for that item are skipped.

```python
results = pipeline(
    ["file1.py", "file2.py"],

    # Stage 1: receives (item, index)
    lambda item, idx: agent(f"Analyze {item}"),

    # Stage 2: receives (prev_result, original_item, index)
    lambda analysis, item, idx: agent(f"Fix {item}: {analysis}"),

    # Stage 3: receives (prev_result, original_item, index)
    lambda fix, item, idx: agent(f"Verify {item}"),
)
```

### phase(title) / log(message)

Emit progress to stderr. `phase` groups subsequent agent calls under a
heading in the live panel; `log` prints a single narrator line.

```python
phase("Scan")
log(f"Processing {len(files)} files")
```

### args

Value passed via `--args` on the CLI. Access as a dict in the script:

```bash
workflow run script.py --args '{"target": "src/", "depth": 3}'
```

```python
def run(agent, parallel, pipeline, phase, log, args, workflow):
    target = args["target"]  # "src/"
```

### workflow(script_path, args)

Invoke another workflow script as a sub-step. Returns the child workflow's
result. Nesting is limited to one level.

```python
result = workflow("other_workflow.py", {"param": "value"})
```

## CLI Commands

```bash
workflow run <script.py> [--args '<json>'] [--resume <id>]
workflow resume <run_id> <script.py>
workflow list
workflow status <run_id>
workflow stop <run_id>
```

## Prompt Writing

Each `agent()` call is a self-contained task. Four elements make a good
prompt:

- **Goal**: one sentence describing the task.
- **Scope**: what to examine, what to skip, constraints.
- **Steps**: ordered list guiding execution.
- **Output**: expected format — plain text, or a JSON Schema for structured
  data.

## Patterns

**Fan-out gather:** run parallel reviews across dimensions (security,
performance, style), then synthesize a single summary.

**Pipeline:** process each file through parse, transform, validate stages.
Files flow independently with no barrier.

**Adversarial verify:** generate a solution, have another agent find flaws,
refine if needed. Repeat until clean.

**Discover then deep-dive:** first scan to find work items, then pipeline
each item through processing stages.

**Loop until dry:** repeatedly search for issues with diverse finders,
deduplicate, fix, and continue until no new findings appear for two
consecutive rounds.

## Resume

If a workflow crashes, re-run with `--resume <id>`. Completed sessions are
skipped.

```bash
workflow run script.py --resume myrun001
workflow resume myrun001 script.py
```

## Gotchas

- Vague prompts ("check the code") produce poor results. Give each agent a
  clear goal, scope, steps, and output format.
- Passing file content instead of file paths bloats context and wastes
  tokens.
- Always handle `None` returns from failed agents — filter with
  `[r for r in results if r is not None]`.
- Use `parallel()` only when you need all results together before
  proceeding. Nesting parallel barriers is a common performance mistake —
  use `pipeline()` to eliminate barriers.
- Scan cheaply first to identify targets, then apply expensive processing
  only to what matters.
- `meta` must be a pure literal — no variables, function calls, or template
  strings.
- `phase()` titles must match `meta.phases` titles exactly.
- Return `None` from `run()` to suppress JSON output.
- `pipeline()` has no inter-stage barrier — items flow independently.
- Each `agent()` call creates a session. Sessions are cleaned up
  automatically.