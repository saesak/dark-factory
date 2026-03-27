# stages/

Pipeline stage modules. Each file in this directory is a self-contained pipeline stage that can be invoked independently from `cli.py`.

## Current Stages

| File | Status | Purpose |
|------|--------|---------|
| `review.py` | BUILT | Autonomous review pipeline — LLM review + deterministic metrics + auto-fix |

## Planned Stages

| File | Purpose |
|------|---------|
| `plan.py` | Planner agent — reads constraints, produces freeform plans with 9 required sections |
| `implement.py` | Implementer agent — executes plans, parallelizes via sub-agents per work unit |
| `judge.py` | Judge agent — evaluates output with fresh perspective, routes feedback to planner or implementer |

## Conventions

### Entry Point

Every stage module must expose a `run()` function as its entry point:

```python
def run(config: dict[str, Any]) -> dict[str, Any]:
    """Run this pipeline stage.

    Args:
        config: Parsed config.yaml merged with CLI overrides.
            Always contains: base_branch, max_iterations, metrics thresholds, timeouts.
            May contain: name (override), dry_run, metrics_only, no_metrics,
            scope (str | None — subdirectory to scope review to for monorepo support).

    Returns:
        Result dict with at minimum:
            - status: "pass" | "stopped_test_failure" | "max_iterations" | "error"
            - summary: human-readable one-line summary
    """
```

### Shared Infrastructure

Stages use `lib/` for all infrastructure:
- `lib/invoke.py` for `claude -p` subprocess calls
- `lib/git_context.py` for diff computation and worktree detection
- `lib/run_logger.py` for creating run directories and writing outputs

Stages should NOT directly call `subprocess.run()` for claude invocations — always go through `lib/invoke.py`.

### Error Handling

Step functions (`_run_step_*`) return empty string `""` when `claude -p` exits with a non-zero exit code or times out. The pipeline checks `if not output` and sets `final_status = "error"` + breaks. This prevents garbage output from cascading through subsequent steps.

Issue accounting is corrected before finalization: `issues_remaining = issues_found - issues_fixed` whenever the numbers don't add up (e.g., pipeline aborted mid-run).

### Independence

Each stage must be runnable without any other stage existing. A stage reads its inputs from the codebase and/or run directory, not from in-memory state passed by another stage. The `cli.py` dispatcher calls `stage.run(config)` and that is the only interface.

### Adding a New Stage

1. Create `stages/{name}.py` with a `run(config)` function
2. Create corresponding prompt files in `prompts/{name}_{step}.md`
3. Add the CLI subcommand to `cli.py`
4. Update this CLAUDE.md and `spec.md`
