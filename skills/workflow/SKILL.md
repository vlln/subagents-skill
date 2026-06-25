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

## Dependencies

Requires the [subagents](../subagents/SKILL.md) skill. Set `SKILL_SUBAGENTS_HOME` to its installation path.

## When To Use

- Split a large task across multiple agents working in parallel.
- Run multi-stage pipelines (scan → analyze → fix → verify).
- Fan-out analysis across dimensions (security, performance, style) then synthesize.
- Discover work items dynamically, then process them in batches.
- Adversarial verify: generate → review → refine cycles.

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

def run(agent, parallel, pipeline, phase, log, args):
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

### agent(prompt, *, schema=None, label=None, model=None)

Run a single subagent. Returns text output, or structured dict if `schema` is provided.

```python
# Simple text
result = agent("Find all test files")

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
    lambda: agent("Check security"),
    lambda: agent("Check performance"),
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

## Rules

- Each `agent()` call creates a session via `subagents run --bg --output json`.
- Sessions are named `wf_<uuid>` and cleaned up automatically.
- `parallel()` uses threads; all agents run concurrently.
- `pipeline()` has no inter-stage barrier — items flow independently.
- Return `None` from `run()` to suppress JSON output.
- Diagnostics go to stderr; workflow output goes to stdout.