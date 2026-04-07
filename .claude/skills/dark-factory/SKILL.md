---
name: dark-factory
description: Use when the user asks to run dark factory, dark-factory review, code quality review, or autonomous code review pipeline. Triggers on "run dark factory", "dark factory review", "dark-factory on this PR", etc.
---

# Dark Factory

Autonomous code quality pipeline. Reviews code changes, applies fixes, and verifies results using `claude -p` subprocess calls and deterministic metric tools.

## Running It

```bash
cd ~/target-repo
unset CLAUDECODE && python /path/to/dark-factory/cli.py review [OPTIONS]
```

**CRITICAL: You MUST `unset CLAUDECODE`** before running, otherwise `claude -p` subprocess calls will fail with "Claude Code cannot be launched inside another Claude Code session."

Always run in the background with a long timeout (up to 10 minutes) since LLM review steps take time:

```bash
cd ~/your-repo && unset CLAUDECODE && python /path/to/dark-factory/cli.py review --dry-run --base origin/main
```

## CLI Options

| Flag | Description |
|------|-------------|
| `--pr <ref>` | GitHub PR reference (`owner/repo#123` or full URL). Mutually exclusive with `--base`. |
| `--base <branch>` | Base branch for diff (default: `origin/main`). Mutually exclusive with `--pr`. |
| `--dry-run` | Emit issues only, don't apply fixes |
| `--metrics-only` | Skip LLM review, only run deterministic metrics |
| `--no-metrics` | Skip deterministic metrics, only run LLM review |
| `--no-simplify` | Skip code simplification step |
| `--scope <path>` | Scope review to a monorepo subdirectory (e.g. `backend/`, `frontend/`). Mutually exclusive with `--files`. |
| `--files <list>` | Comma-separated file paths to scope review to. Mutually exclusive with `--scope` and `--pr`. |
| `--model <name>` | Model override for claude -p (e.g. `opus`, `sonnet`, `haiku`). Default: sonnet. |
| `--repo-path <path>` | Path to repo (default: cwd) |
| `--name <id>` | Override worktree/PR identifier for run directory |
| `--max-iterations <n>` | Max review-fix loops (default: 2) |

## Common Patterns

### Review a GitHub PR directly (recommended for large PRs)
```bash
unset CLAUDECODE && python cli.py review --pr owner/repo#123
```

### Review a GitHub PR (dry run — issues only, no fixes)
```bash
unset CLAUDECODE && python cli.py review --pr owner/repo#123 --dry-run
```

### Review a local branch
```bash
cd ~/your-repo && unset CLAUDECODE && python /path/to/dark-factory/cli.py review --base origin/main
```

### Dry run (issues only, no fixes)
```bash
unset CLAUDECODE && python cli.py review --dry-run
```

### Scope to a monorepo subdirectory (when diff is too large)
```bash
unset CLAUDECODE && python cli.py review --scope backend/
unset CLAUDECODE && python cli.py review --scope frontend/
```

### Scope to specific files
```bash
unset CLAUDECODE && python cli.py review --files "src/api.py,src/models.py,tests/test_api.py"
```

### Use Opus for deeper review
```bash
unset CLAUDECODE && python cli.py review --model opus --dry-run
```

## Mutual Exclusivity

- `--files` and `--scope` are mutually exclusive
- `--files` and `--pr` are mutually exclusive
- `--pr` and `--base` are mutually exclusive

## Known Limitations

- **Must unset CLAUDECODE**: Nested Claude sessions are blocked by default (auto-handled by invoke.py now, but `unset CLAUDECODE` is still recommended for safety).
- **`--pr` requires `gh` CLI**: The PR mode uses `gh pr diff` and `gh pr checkout` under the hood.

## Run Output

Logs are saved to `runs/<branch-or-name>/<run-number>/`. Check `run.json` for summary and `steps/` for per-step stdout/stderr.
