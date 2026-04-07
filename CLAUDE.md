# Dark Factory

Autonomous code quality pipeline system that orchestrates code review (and eventually code generation) using `claude -p` subprocess calls and deterministic metric tools.

## Architecture

Python orchestrator calling `claude -p` subprocesses and deterministic metric tools (radon, diff-cover, jscpd, ruff). Each LLM agent call is a stateless subprocess — no conversation history, no shared state between calls. The orchestrator assembles context, injects it into prompt templates, invokes the subprocess, and captures the output.

Key principle: **each stage is independently runnable, stages are composable, no frameworks.** You can run `dark-factory review` without building the planner. You can run the planner manually and review its output separately. No stage assumes it is part of a larger pipeline.

## Code Conventions

- **Python 3.11+** — use modern syntax (`list[str]`, `dict[str, Any]`, `X | None` instead of `Optional[X]`)
- **Type hints on everything** — all function parameters, return values, local variables, class attributes, module-level variables
- **Standard library only** — no framework dependencies. `subprocess`, `json`, `pathlib`, `re`, `datetime`, `argparse`. Exception: `pyyaml` for config parsing.
- **Ruff for formatting** — run `ruff format` after any Python edits, `ruff check` before committing
- **No classes unless justified** — prefer functions. Use classes only when you need state that persists across multiple method calls.
- **Explicit error handling** — subprocess failures, timeouts, and malformed outputs must be handled. Never silently swallow errors.
- **Retry transient failures** — `invoke_claude` retries up to 2 times on non-zero exit codes. Step functions return `""` on failure so the pipeline aborts cleanly instead of cascading garbage.

## Directory Layout

| Directory | Contains | Key Rule |
|-----------|----------|----------|
| `stages/` | Pipeline stage modules (review.py, plan.py, etc.) | Each stage has a `run()` entry point, is independently callable |
| `prompts/` | Markdown templates passed to `claude -p` | Named `{stage}_{step}.md`, use `{{VARIABLE}}` for template injection |
| `metrics/` | Deterministic metric tools — no LLM calls | Output is structured JSON with violations array |
| `lib/` | Shared utilities used by all stages | No business logic — just infrastructure (subprocess wrapper, git, logging) |
| `runs/` | Run output directory (created at runtime) | Organized by worktree/PR, then sequential numbered runs |

## How to Test Changes

Run the review pipeline against a test branch:

```bash
cd ~/your-repo-with-changes
python ~/.dark-factory/cli.py review --base origin/main
```

For monorepo subdirectories, use `--scope` to limit the review to a subpackage:

```bash
cd ~/my-monorepo
python ~/.dark-factory/cli.py review --scope backend/
```

For development iteration:
- `--dry-run` to emit issues without applying fixes
- `--metrics-only` to test just the deterministic metric tools
- `--no-metrics` to test just the LLM review path
- `--no-simplify` to skip the code simplification pass
- `--scope <path>` to scope the review to a monorepo subdirectory
- `--files <a.py,b.py>` to scope the review to specific files (comma-separated)
- `--model <name>` to override the model for `claude -p` (e.g. `opus`, `sonnet`, `haiku`)

**Mutual exclusivity:** `--files`, `--scope`, and `--pr` are mutually exclusive. You can only use one at a time.

Check `~/.dark-factory/runs/` for output logs after each run.

## Scoping: Monorepo and File-Level

### `--scope` (monorepo subdirectory)

The `--scope` flag scopes the review pipeline to a subdirectory within a monorepo. When set:
- Git diffs and changed files are filtered to the scope path
- Tests run from `{repo_path}/{scope}` (subpackages have their own test runners)
- `coverage.xml` is looked up in the scope directory first, then the repo root
- Fix agents work from the repo root but are instructed to focus on the scoped subdirectory

### `--files` (explicit file list)

The `--files` flag scopes the review to a comma-separated list of specific files. When set:
- Git diffs are filtered to the listed files (`git diff base...HEAD -- file1 file2`)
- Changed file list is filtered to the listed files
- `--files` takes precedence over `--scope` if both are somehow provided to `compute_diff` / `list_changed_files`
- Fix agents are instructed to focus on the listed files only

## Reference

- **Project spec:** `~/.dark-factory/spec.md` — full file structure, pipeline steps, schemas, what's built vs planned
- **Design rationale:** See `spec.md` for the broader vision, three-pillar architecture, engineering tradeoffs, and why things are designed the way they are
