# Installing Dark Factory

## Prerequisites

- [Claude CLI](https://docs.anthropic.com/en/docs/claude-code) (`claude -p` must be available)
- Python 3.11+
- [uv](https://docs.astral.sh/uv/) for Python package management
- `npm` (for jscpd)

## Set Up the Virtual Environment

```bash
cd ~/.dark-factory
uv venv
uv pip install .
npm install -g jscpd
```

## Install the Claude Code Skill

Create the skill directory and file:

```bash
mkdir -p ~/.claude/skills/dark-factory
cat > ~/.claude/skills/dark-factory/SKILL.md << 'EOF'
---
name: dark-factory
description: Use when the user asks to run dark factory, dark-factory review, code quality review, or autonomous code review pipeline. Triggers on "run dark factory", "dark factory review", "dark-factory on this PR", etc.
---

# Dark Factory

Autonomous code quality pipeline at `~/.dark-factory/`. Reviews code changes, applies fixes, and verifies results using `claude -p` subprocess calls and deterministic metric tools.

## Location

`~/.dark-factory/` — NOT `~/dark-factory`.

## Running It

```bash
cd ~/path-to-repo
unset CLAUDECODE && python ~/.dark-factory/cli.py review [OPTIONS]
```

**CRITICAL: You MUST `unset CLAUDECODE`** before running, otherwise `claude -p` subprocess calls will fail with "Claude Code cannot be launched inside another Claude Code session."

Always run in the background with a long timeout (up to 10 minutes) since LLM review steps take time:

```bash
# Example
cd ~/my-repo && unset CLAUDECODE && python ~/.dark-factory/cli.py review --dry-run --base origin/main
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
| `--scope <path>` | Scope review to a monorepo subdirectory (e.g. `backend/`, `frontend/`) |
| `--repo-path <path>` | Path to repo (default: cwd) |
| `--name <id>` | Override worktree/PR identifier for run directory |
| `--max-iterations <n>` | Max review-fix loops (default: 2) |

## Common Patterns

### Review a GitHub PR directly (recommended for large PRs)
```bash
cd ~/my-repo && unset CLAUDECODE && python ~/.dark-factory/cli.py review --pr owner/repo#123
```

### Review a GitHub PR (dry run — issues only, no fixes)
```bash
cd ~/my-repo && unset CLAUDECODE && python ~/.dark-factory/cli.py review --pr owner/repo#123 --dry-run
```

### Review a local branch
```bash
cd ~/my-repo && unset CLAUDECODE && python ~/.dark-factory/cli.py review --base origin/main
```

### Dry run (issues only, no fixes)
```bash
unset CLAUDECODE && python ~/.dark-factory/cli.py review --dry-run
```

### Scope to a monorepo subdirectory (when diff is too large)
```bash
unset CLAUDECODE && python ~/.dark-factory/cli.py review --scope backend/
unset CLAUDECODE && python ~/.dark-factory/cli.py review --scope frontend/
```

## Known Limitations

- **Must unset CLAUDECODE**: Nested Claude sessions are blocked by default (auto-handled by invoke.py now, but `unset CLAUDECODE` is still recommended for safety).
- **`--pr` requires `gh` CLI**: The PR mode uses `gh pr diff` and `gh pr checkout` under the hood.

## Run Output

Logs are saved to `~/.dark-factory/runs/<branch-or-name>/<run-number>/`. Check `run.json` for summary and `steps/` for per-step stdout/stderr.
EOF
```

## Recommended Claude Code Setup

Dark-factory runs `claude -p` subprocesses that inherit your Claude Code configuration. These plugins and settings significantly improve review quality.

### Install plugins

```bash
claude plugin marketplace add anthropics/claude-plugins-official
claude plugin install superpowers@claude-plugins-official
claude plugin install code-review@claude-plugins-official
claude plugin install code-simplifier@claude-plugins-official
```

### Set effort level to high

Add `"effortLevel": "high"` to your `~/.claude/settings.json`:

```json
{
  "effortLevel": "high"
}
```

This makes the LLM review steps read more deeply instead of doing a surface-level scan.

## Verify

After installing, you can trigger the skill by telling Claude Code:

- "run dark factory"
- "dark factory review"
- "dark-factory on this PR"

Claude will automatically pick up the skill and run the pipeline.
