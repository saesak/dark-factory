# lib/

Shared utilities used by all pipeline stages. This directory contains infrastructure code only — no business logic, no pipeline orchestration, no decision-making about what to review or fix.

## Current Files

| File | Status | Purpose |
|------|--------|---------|
| `invoke.py` | BUILT | `claude -p` subprocess wrapper |
| `git_context.py` | BUILT | Git operations — diffs, changed files, worktree detection |
| `run_logger.py` | BUILT | Run directory management and structured logging |

## File Details

### invoke.py

Wraps `claude -p` subprocess calls. Every LLM agent invocation in the pipeline goes through this module. Responsibilities:

- **Build prompt from template:** Read a `.md` file from `prompts/`, replace `{{VARIABLE_NAME}}` placeholders with provided context values
- **Call subprocess:** Invoke `claude -p "<prompt>" --allowedTools <tools>` with configurable timeout
- **Model override:** When `model` is provided (e.g. `"opus"`, `"sonnet"`, `"haiku"`), passes `--model <name>` to `claude -p`
- **Capture output:** Collect stdout, stderr, exit code, wall clock time
- **Retry on transient failures:** On non-zero exit codes, retries up to `max_retries` times (default: 2) before returning the failure. Does NOT retry on timeouts or missing CLI (those aren't transient).
- **Handle failures:** Timeout, non-zero exit code, empty output — return structured error, never raise

Returns a result dict: `{"exit_code": int, "stdout": str, "stderr": str, "wall_clock_ms": int, "timed_out": bool}`

### git_context.py

Computes everything the pipeline needs to know about the current git state. Pure functions — no side effects, no writing to disk.

- **compute_diff(repo_path, base_branch, scope=None, files=None):** Three-dot diff (`git diff base...HEAD`). When `files` is set, appends `-- file1 file2` to limit the diff to specific files (takes precedence over `scope`). When `scope` is set, appends `-- {scope}` to limit the diff to a subdirectory.
- **list_changed_files(repo_path, base_branch, scope=None, files=None):** List of file paths changed relative to base. When `files` is set, appends `-- file1 file2` to limit to specific files (takes precedence over `scope`). When `scope` is set, appends `-- {scope}` to limit to a subdirectory. Note: the local variable for the result is named `changed` (not `files`) to avoid shadowing the parameter.
- **detect_worktree_name():** If running in a git worktree, return its name. Otherwise return None.
- **get_branch_name():** Current branch name
- **get_repo_root():** Absolute path to repo root
- **get_run_identifier(name_override):** Resolve the run identifier using priority: explicit override > worktree name > branch name > repo_branch fallback

### run_logger.py

Creates and manages run directories. Handles all file I/O for pipeline observability.

- **create_run_dir(identifier, run_type):** Creates `~/.dark-factory/runs/{identifier}/{seq}_{type}_{timestamp}/` with sequential numbering per identifier directory. Also creates `steps/` subdirectory.
- **save_diff_snapshot(run_dir, diff):** Write `diff_before.patch`
- **save_step_output(run_dir, step_num, step_name, stdout, stderr):** Write to `steps/{step_num}_{step_name}_stdout.txt`
- **write_run_json(run_dir, data):** Write the final `run.json` with run metadata

Sequential numbering: scans existing directories in the identifier folder, finds the highest number, increments by 1. Format: `{seq:03d}_{type}_{timestamp}` (e.g., `003_review_2026-02-27T14-30`).

## Rules

- **No business logic.** These utilities don't decide what to review, what metrics to run, or how to interpret results. They provide infrastructure that stages compose.
- **All functions must be typed.** Parameters, return values, local variables.
- **Pure functions where possible.** `git_context.py` in particular should be side-effect-free. `run_logger.py` necessarily writes to disk, but its functions should be idempotent where reasonable.
- **No imports from `stages/`, `prompts/`, or `metrics/`.** `lib/` is a dependency of those directories, not the other way around.
