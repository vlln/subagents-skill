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

Commands below assume `workflow` is on PATH or invoked from the skill directory. Examples use the short form `workflow` for brevity.

Use `workflow` to orchestrate multiple AI agents in parallel or pipeline patterns. A workflow script is a Python file that defines `run(agent, parallel, pipeline, phase, log, args, workflow)` — the agent writes the script, then executes it.

## Dependencies

Requires the [subagents](https://github.com/vlln/subagents-skill/blob/main/skills/subagents/SKILL.md) skill. Set `SKILL_SUBAGENTS_HOME` to its installation path.

## When To Use

- Multi-perspective analysis: fan out across dimensions (security, performance, style) then synthesize.
- Multi-stage pipelines: scan, analyze, fix, verify — each stage builds on the previous.
- Bulk operations: migrate, audit, or transform many files at once.
- Adversarial verification: generate a solution, have another agent find flaws, refine.
- Discovery-driven work: first find what needs doing, then process each item.

Don't use for simple single-file edits or tasks a single agent call can handle.

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

`meta` must be a pure literal — no variables, function calls, or template strings. `phase()` titles must match `meta.phases` titles exactly.

## API Reference

**agent(prompt, *, schema=None, label=None, backend=None, model=None)**

Run a single subagent. Returns text, or a validated dict if `schema` is provided. Use `schema` when the result feeds into another stage or needs parsing. Use `label` for display in the live panel. Use `backend` to select a specific backend (e.g. `"claude"`, `"kimi"`). Use `model` to override the model for this agent call.

**parallel(thunks)**

Run thunks concurrently, wait for all, return results in input order. Failed thunks return `None` — filter with `[r for r in results if r is not None]`. Use only when you need all results before the next step.

**pipeline(items, *stages)**

Process each item through all stages independently. No barrier between stages: item A can be in stage 3 while item B is still in stage 1. Wall-clock time is the slowest single-item chain, not the sum of slowest-per-stage. Each stage receives `(prev_result, original_item, index)`. This is the default pattern — prefer it over parallel.

**phase(title)** / **log(message)**

Emit progress to stderr. `phase` groups subsequent agent calls under a heading; `log` prints a single narrator line.

**args**

Value passed as `--args` on the CLI. Pass arrays/objects as actual JSON values, not JSON-encoded strings.

## Prompt Writing

Each `agent()` call is a self-contained task. Four elements make a good prompt:

- **Goal**: one sentence describing the task.
- **Scope**: what to examine, what to skip, constraints.
- **Steps**: ordered list guiding execution.
- **Output**: expected format — plain text, or a JSON Schema for structured data.

Common mistakes: vague prompts ("check the code"), passing file content instead of file paths (bloats context), forgetting to handle `None` returns from failed agents.

## Design Decisions

**Default to pipeline.** Only use parallel when you genuinely need all results together before proceeding. Nesting parallel barriers (wait for all, then wait for all again) is a common performance mistake — pipeline eliminates those barriers.

**Early filter, then deep-dive.** Scan cheaply first to identify targets, then apply expensive processing only to what matters.

## Patterns

**Fan-out gather:** run parallel reviews across dimensions (security, performance, style), then synthesize a single summary.

**Pipeline:** process each file through parse, transform, validate stages. Files flow independently with no barrier.

**Adversarial verify:** generate a solution, have another agent find flaws, refine if needed. Repeat until clean.

**Discover then deep-dive:** first scan to find work items, then pipeline each item through processing stages.

**Loop until dry:** repeatedly search for issues with diverse finders, deduplicate, fix, and continue until no new findings appear for two consecutive rounds.

## Performance

| Pattern | Agents | Time | Tokens |
|---------|--------|------|--------|
| Single analysis | 1 | 10-30s | 5-20k |
| 3-dimension parallel review | 3 | 30-60s | 20-60k |
| 10-file pipeline (3-stage) | 30 | 2-5min | 100-300k |
| Deep research (10 sources) | 15-20 | 3-8min | 150-400k |

## Monitoring

Workflow prints live progress to stderr during execution. CLI commands:

```bash
workflow list              # all runs with session status
workflow status <run_id>   # detailed status of one run
workflow stop <run_id>     # stop a running workflow
```

## Resume

If a workflow crashes, re-run with `--resume <id>`. Completed sessions are skipped.

```bash
workflow run script.py --resume myrun001
workflow resume myrun001 script.py
```

## Mock Mode

For testing without a real backend:

```python
from runtime import set_mock
set_mock("shell")  # agent() now executes prompts as shell commands
set_mock(None)     # restore real subagent execution
```

## Rules

- Each `agent()` call creates a session via `subagents run --bg --output json`.
- `parallel()` uses threads; all agents run concurrently.
- `pipeline()` has no inter-stage barrier.
- Sessions are named `wf_<uuid>` and cleaned up automatically.
- Diagnostics and live display go to stderr; workflow output goes to stdout.
- Return `None` from `run()` to suppress JSON output.