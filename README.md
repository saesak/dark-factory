# Dark Factory

Autonomous code quality pipeline that reviews code changes, applies fixes, and verifies results — all headlessly using `claude -p` subprocess calls and deterministic metric tools.

## What You Need to Know

running it with default settings pushes commits to your branch automatically. Running it with dry run lets you surface issues. For large PRs (1000+ lines), run multiple parallel dark factory runs on different parts of the code. 


## What It Does

Point it at a branch with code changes and it runs a multi-step review pipeline:

1. **LLM Review** — Analyzes the diff for architecture, code quality, test, and performance issues
2. **Coherence Check** — Consolidates the issue list, resolves conflicts between fixes
3. **Apply Fixes** — Applies all recommended fixes to the codebase
4. **Run Tests** — Verifies fixes don't break anything
5. **Deterministic Metrics** — Runs complexity (radon), coverage (diff-cover), duplication (jscpd), and lint (ruff) checks
6. **Fix Metric Violations** — Mechanically fixes any threshold violations
7. **Re-test** — Verifies metric fixes
8. **Verification** — Abbreviated re-review of the final state; loops if issues remain

Output: cleaner code on your branch + a structured run log in `~/.dark-factory/runs/`.

## Quick Start

### Prerequisites

- [Claude CLI](https://docs.anthropic.com/en/docs/claude-code) (`claude -p` must be available)
- Python 3.11+
- PyYAML (`pip install pyyaml`)
- Metric tools: `radon`, `diff-cover`, `jscpd`, `ruff`

### Usage

```bash
# Run a review against origin/main
cd ~/your-repo-with-changes
python ~/.dark-factory/cli.py review

# Custom base branch
python ~/.dark-factory/cli.py review --base origin/develop

# Monorepo: scope to a subdirectory
python ~/.dark-factory/cli.py review --scope backend/

# Dry run (emit issues, don't fix)
python ~/.dark-factory/cli.py review --dry-run

# Only run deterministic metrics (no LLM)
python ~/.dark-factory/cli.py review --metrics-only

# Only run LLM review (no metrics)
python ~/.dark-factory/cli.py review --no-metrics

# Skip code simplification pass
python ~/.dark-factory/cli.py review --no-simplify
```

### CLI Options

| Flag | Description |
|------|-------------|
| `--base <branch>` | Base branch for diff (default: `origin/main`) |
| `--name <id>` | Override worktree/PR identifier for run directory |
| `--max-iterations <n>` | Max review-fix loops (default: 2) |
| `--dry-run` | Emit issues only, don't apply fixes |
| `--metrics-only` | Skip LLM review, only run deterministic metrics |
| `--no-metrics` | Skip deterministic metrics, only run LLM review |
| `--no-simplify` | Skip code simplification step |
| `--scope <path>` | Scope review to a monorepo subdirectory |
| `--repo-path <path>` | Path to repo (default: cwd) |

## Configuration

Edit `config.yaml` to adjust thresholds and timeouts:

```yaml
base_branch: origin/main
max_iterations: 2

metrics:
  cyclomatic_complexity_threshold: 10
  function_length_threshold: 50
  file_length_threshold: 500
  coverage_delta_minimum: 80
  duplication_min_tokens: 50

timeouts:
  review_emit: 600000   # 10 min
  coherence: 300000      #  5 min
  fix: 600000            # 10 min
  metrics_fix: 300000    #  5 min
  verify: 600000         # 10 min
```

## Project Structure

```
~/.dark-factory/
├── cli.py              # Entry point
├── config.yaml         # Default configuration
├── stages/
│   └── review.py       # Review pipeline orchestrator
├── prompts/            # Markdown prompt templates for each step
│   ├── review_emit.md
│   ├── review_coherence.md
│   ├── review_fix.md
│   ├── review_verify.md
│   ├── metrics_fix.md
│   └── simplify.md
├── metrics/
│   └── runner.py       # Deterministic metric tools (radon, diff-cover, jscpd, ruff)
├── lib/
│   ├── invoke.py       # claude -p subprocess wrapper
│   ├── git_context.py  # Diff computation, changed files, worktree detection
│   └── run_logger.py   # Run directory creation and step output capture
└── runs/               # Run output logs (created at runtime)
```

## Design Principles

- **No frameworks** — Plain Python, standard library + PyYAML only
- **Stateless LLM calls** — Each `claude -p` call is a fresh subprocess with no shared state
- **Composable stages** — Every stage is independently runnable
- **Deterministic + LLM** — Hard metric checks complement LLM judgment
- **Full run logs** — Every step's output is captured for debugging

## Future Phases

- **Phase 2**: Metrics infrastructure — per-PR metric snapshots, trend analysis
- **Phase 3**: Architecture documentation — `arch.yaml` constraint files, import direction enforcement
- **Phase 4**: Planning & implementation pipeline — planner/implementer/judge agents
- **Phase 5**: Full autonomous pipeline — plan -> implement -> test -> review
