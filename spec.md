# Dark Factory — Project Specification

## What This Is

Dark Factory is a code quality pipeline system that orchestrates autonomous code review and (eventually) autonomous code generation using `claude -p` subprocess calls and deterministic metric tools. It runs headlessly — in a terminal, in CI, as a background process — with no interactive session required.

The core insight: LLMs can write and review code, but without structured feedback loops and guardrails, the output decays architecturally over time. Dark Factory builds the verification layer — complexity checks, coverage enforcement, duplication detection, and iterative self-correction — that makes autonomous code work reliable. Each pipeline stage is independently runnable, stages are composable, and the orchestrator is plain Python with no framework dependencies.

## Composable CLI Design

Every pipeline stage is independently invocable via CLI:

```bash
python ~/.dark-factory/cli.py review [OPTIONS]        # Autonomous review pipeline (Phase 1)
python ~/.dark-factory/cli.py plan [OPTIONS]           # Planner agent (future — Phase 4)
python ~/.dark-factory/cli.py implement [OPTIONS]      # Implementer agent (future — Phase 4)
python ~/.dark-factory/cli.py judge [OPTIONS]          # Judge agent (future — Phase 4)
python ~/.dark-factory/cli.py full [OPTIONS]           # Full pipeline chain (future — Phase 5)
```

Each stage reads its inputs from the codebase and/or previous run outputs, writes its outputs to a run directory, and has its own prompts, configuration, and timeout defaults. The full pipeline chains stages together, but no stage assumes it's part of a larger run.

### Review CLI Options

```bash
python ~/.dark-factory/cli.py review [OPTIONS]

Options:
  --base <branch>       Base branch for diff (default: origin/main)
  --name <identifier>   Override worktree/PR identifier for run directory
  --max-iterations <n>  Max review-fix loops (default: 2)
  --dry-run             Emit issues only, don't apply fixes
  --metrics-only        Skip LLM review, only run deterministic metrics
  --no-metrics          Skip deterministic metrics, only run LLM review
  --scope <path>        Subdirectory to scope the review to (e.g., backend/)
```

#### Monorepo Support

The `--scope` flag enables reviewing a subdirectory within a monorepo. When set:

- Git diffs and changed file lists are scoped to the specified subdirectory
- Tests run with `cwd` set to `{repo_path}/{scope}` (monorepo subpackages have their own test runners)
- `coverage.xml` is looked up in `{repo_path}/{scope}/` first, then `{repo_path}/`
- Fix agents still operate from the repo root (file paths are relative to repo root) but are told to focus on the scoped subdirectory

Example: reviewing only the `backend/` subpackage in a monorepo:

```bash
cd ~/my-monorepo
python ~/.dark-factory/cli.py review --scope backend/
```

---

## File Structure

```
~/.dark-factory/
├── spec.md                           # This file — project overview, file structure, what's built vs planned
├── CLAUDE.md                         # Project conventions, how to work on this codebase
├── cli.py                            # [BUILT] Entry point — dispatches to stage modules
│
├── stages/
│   ├── CLAUDE.md                     # Conventions for stage modules
│   └── review.py                     # [BUILT] Review pipeline orchestration
│                                     #   Future: plan.py, implement.py, judge.py
│
├── prompts/
│   ├── CLAUDE.md                     # Prompt design principles and naming conventions
│   ├── review_emit.md                # [BUILT] LLM review: analyze code and emit issues
│   ├── review_coherence.md           # [BUILT] Coherence check: consolidate fix plan from issues
│   ├── review_fix.md                 # [BUILT] Apply consolidated fixes to code
│   ├── review_verify.md              # [BUILT] Abbreviated re-review after fixes
│   └── metrics_fix.md                # [BUILT] Fix deterministic metric violations
│                                     #   Future: plan.md, implement.md, judge.md, and sub-step variants
│
├── metrics/
│   ├── CLAUDE.md                     # How metric tools work, output format
│   └── runner.py                     # [BUILT] Runs radon, diff-cover, jscpd, ruff — returns JSON
│
├── lib/
│   ├── CLAUDE.md                     # Shared utilities, no business logic
│   ├── invoke.py                     # [BUILT] claude -p subprocess wrapper
│   ├── git_context.py                # [BUILT] Diff computation, changed files, worktree detection
│   └── run_logger.py                 # [BUILT] Run directory creation, run.json writing, step output capture
│
├── config.yaml                       # Default configuration — thresholds, timeouts, base branch
│
└── runs/                             # Run output directory (created at runtime)
    └── {worktree-or-pr-identifier}/  # One directory per worktree/PR/branch
        ├── 001_review_2026-02-27T14-30/
        │   ├── run.json              # Run metadata: type, status, timing, issues found/fixed
        │   ├── steps/                # Output from each pipeline step
        │   │   ├── 1_review_emit_stdout.txt
        │   │   ├── 2_coherence_stdout.txt
        │   │   ├── 3_fix_stdout.txt
        │   │   ├── 4_test_stdout.txt
        │   │   ├── 5_metrics.json
        │   │   ├── 6_metrics_fix_stdout.txt
        │   │   ├── 7_test_stdout.txt
        │   │   └── 8_verify_stdout.txt
        │   └── diff_before.patch     # Snapshot of code state going in
        │
        ├── 002_review_2026-02-27T16-00/
        └── ...
```

---

## Phase 1: Review Pipeline (Building Now)

The review pipeline is the first thing being built. It operates on any branch with code changes and delivers immediate value — no dependency on the planner/implementer/judge pipeline.

### What It Does

Takes a branch with code changes, runs a two-pass autonomous review (LLM judgment + deterministic metrics), applies all recommended fixes, verifies with tests, and optionally loops once more. Output: cleaner code on the branch plus a run log documenting what was found and fixed.

### Pipeline Steps

```
dark-factory review is invoked
    |
    |  git_context.py: compute diff, identify changed files,
    |  detect worktree/branch name for run directory
    |
    |  run_logger.py: create run directory
    |  (e.g. ~/.dark-factory/runs/my-feature-branch/003_review_2026-02-27T14-30/)
    |
    v
Step 1: LLM Review — Emit Issues (review_emit.md)
    |  claude -p with read-only tools (Read, Glob, Grep)
    |  Analyzes: architecture, code quality, tests, performance (high bar only)
    |  Output: structured issue list with recommended fixes
    |  Does NOT apply anything
    |
    v
Step 2: Coherence Check (review_coherence.md)
    |  claude -p with read-only tools
    |  Input: issue list from step 1
    |  Checks: conflicting fixes, redundancies, combined approaches needed
    |  Output: consolidated fix plan
    |
    v
Step 3: Apply Fixes (review_fix.md)
    |  claude -p with full tools (Read, Write, Edit, Bash, Glob, Grep)
    |  Input: consolidated fix plan + original diff
    |  Applies all fixes per the plan — does not deviate
    |  Code is modified on disk
    |
    v
Step 4: Run Tests
    |  Bash: run the project's test suite (configured in config.yaml)
    |  If tests fail -> stop pipeline, report what was applied
    |  Does NOT attempt to fix test failures (signals the fixes were wrong)
    |
    v
Step 5: Deterministic Metrics (metrics/runner.py)
    |  Runs on changed files: radon (complexity), diff-cover (coverage),
    |  jscpd (duplication), ruff (lint)
    |  Output: JSON with violations array
    |  If no violations -> skip step 6
    |
    v
Step 6: Fix Metric Violations (metrics_fix.md) — if violations exist
    |  claude -p with full tools
    |  Input: metrics.json violations
    |  Mechanical fixes: split long functions, extract duplication, reduce complexity
    |
    v
Step 7: Run Tests Again — if step 6 ran
    |  Same as step 4. If fail -> log, stop.
    |
    v
Step 8: Verification (review_verify.md) — iteration 1 only
    |  claude -p with read-only tools
    |  Input: new diff (original changes + all fixes applied)
    |  Abbreviated review — any remaining issues?
    |  If clean -> done
    |  If issues found AND iteration < max_iterations -> loop to step 2
    |  If issues found AND iteration >= max_iterations -> log remaining, done
    |
    v
Done — run_logger.py writes final run.json
```

### File Details

#### cli.py [BUILT]

Entry point. Parses CLI arguments, loads config.yaml, dispatches to the appropriate stage module. Uses `argparse` — no external CLI framework.

#### stages/review.py [BUILT]

The review pipeline orchestrator. Implements the 8-step pipeline above. Coordinates between LLM calls (via `lib/invoke.py`), metric runs (via `metrics/runner.py`), and test execution (via subprocess). Manages iteration logic and writes results via `lib/run_logger.py`.

Entry point: `run(config: dict) -> dict` — takes parsed config, returns result dict with status and summary.

#### prompts/review_emit.md [BUILT]

LLM review prompt. Instructs the agent to analyze the diff across four dimensions:
- **Architecture** — system design, component boundaries, dependency direction, data flow
- **Code quality** — organization, DRY violations, dead code, structural issues, error handling
- **Tests** — coverage gaps, test quality, edge cases, failure modes
- **Performance** — high bar only: failures, timeouts, or severe degradation under normal load

Emits structured issue list. Does not apply fixes. Read-only tools.

#### prompts/review_coherence.md [BUILT]

Coherence check prompt. Reviews the full issue list before fixes are applied. Checks for conflicts between recommended fixes, identifies redundancies, produces a consolidated fix plan.

#### prompts/review_fix.md [BUILT]

Fix application prompt. Receives the consolidated fix plan and original diff. Applies all fixes per the plan. Full tools (Read, Write, Edit, Bash, Glob, Grep).

#### prompts/review_verify.md [BUILT]

Verification prompt. Abbreviated re-review of the modified code. Emits any remaining issues or reports clean. Read-only tools.

#### prompts/metrics_fix.md [BUILT]

Metric fix prompt. Receives deterministic metric violations (JSON). Makes mechanical changes — split long functions, extract duplicated code, reduce complexity. These are not judgment calls. Full tools.

#### metrics/runner.py [BUILT]

Runs deterministic metric tools on changed files. Returns structured JSON. Tools:
- `radon cc` — cyclomatic complexity per function
- `diff-cover` — coverage delta on changed lines (requires coverage.xml)
- `jscpd` — syntactic duplication detection
- `ruff check` — lint violations

Output format: JSON with a `violations` array. Each violation has: `file`, `line`, `metric`, `value`, `threshold`.

#### lib/invoke.py [BUILT]

`claude -p` subprocess wrapper. Responsibilities:
- Build prompt from template file + injected variables
- Replace `{{VARIABLE_NAME}}` placeholders with context values
- Call `claude -p` as a subprocess with appropriate `--allowedTools`
- Capture stdout/stderr
- Handle timeouts (configurable per step)
- Return structured result with exit code, output, timing

#### lib/git_context.py [BUILT]

Git context utilities. Responsibilities:
- Compute three-dot diff against base branch (`git diff base...HEAD`)
- List changed files
- Detect worktree name (if running in a worktree)
- Identify current branch
- Detect repo root path

Worktree/PR identification priority:
1. Explicit `--name` flag (override)
2. Git worktree name if invoked from a worktree
3. Branch name
4. Fallback: `{repo-name}_{branch-name}`

#### lib/run_logger.py [BUILT]

Run logging utilities. Responsibilities:
- Create run directories following naming convention: `{worktree}/{seq}_{type}_{timestamp}/`
- Sequential numbering (001, 002, ...) per worktree directory
- Create `steps/` subdirectory
- Save diff snapshot (`diff_before.patch`)
- Write step outputs to `steps/` directory
- Write final `run.json` with metadata

### run.json Schema (Review)

```json
{
  "run_id": "003_review_2026-02-27T14-30",
  "type": "review",
  "worktree": "my-feature-branch",
  "repo_path": "/Users/you/my-repo",
  "base_branch": "origin/main",
  "git_sha_start": "abc123",
  "git_sha_end": "def456",
  "started_at": "2026-02-27T14:30:00Z",
  "completed_at": "2026-02-27T14:42:00Z",
  "final_status": "pass",
  "iterations": 1,
  "total_wall_clock_ms": 720000,
  "issues_found": 8,
  "issues_fixed": 8,
  "issues_remaining": 0,
  "steps": [
    {
      "step": 1,
      "name": "review_emit",
      "started_at": "2026-02-27T14:30:00Z",
      "wall_clock_ms": 180000,
      "exit_code": 0,
      "output_path": "steps/1_review_emit_stdout.txt"
    }
  ],
  "metrics_summary": {
    "complexity_violations": 1,
    "coverage_violations": 0,
    "duplication_violations": 1,
    "lint_violations": 0
  }
}
```

---

## Future Phases (Not Yet Built)

### Phase 2: Metrics Infrastructure

Instrument hard implementation metrics (complexity, coverage delta, file length). Compute and store per-PR metric snapshots for trend analysis. Add co-change coupling analysis from git history. Surface metrics as standalone reports and within the review pipeline.

### Phase 3: Architecture Documentation

Human-agent collaboration to create `arch.yaml` + `arch.md` pairs for each module. Establishes module boundaries, dependencies, and layer structure. Write 3-5 constraint files for existing architectural boundaries. Build a minimal enforcer that checks import directions against constraint YAMLs using `grimp`.

### Phase 4: Planning & Implementation Pipeline

Build the three-agent coding pipeline:
- **Planner** — reads constraints, produces freeform plans with 9 required sections. Read-only tools.
- **Implementer** — executes plans, parallelizes via sub-agents per work unit. Full tools.
- **Judge** — evaluates output with fresh perspective, routes feedback (PASS, ARCH_FAILURE, IMPL_FAILURE, etc.). Read-only tools.

New files: `stages/plan.py`, `stages/implement.py`, `stages/judge.py`, and corresponding prompt files.

### Phase 5: Full Autonomous Pipeline

Chain all stages: plan -> implement -> test -> review. Integrate decision record drafting into PR process. Add architectural drift detector on periodic schedule. Code cleanup agent that proposes metric-improving refactors.

---

## Dependencies

Tools that must be installed on the system:

| Tool | Language | Purpose |
|------|----------|---------|
| `claude` CLI | — | `claude -p` subprocess calls for LLM agents |
| `radon` | Python | Cyclomatic complexity analysis |
| `diff-cover` | Python | Coverage delta on diffs (requires `coverage.xml`) |
| `jscpd` | Node.js | Syntactic duplication detection |
| `ruff` | Python/Rust | Linting and formatting |

The orchestrator itself uses Python standard library only (`subprocess`, `json`, `pathlib`, `re`, `datetime`, `argparse`, `yaml`). No framework dependencies. The one exception is `pyyaml` for config parsing — or we can use a simple custom YAML parser for the flat config structure.

Future dependencies: `grimp` (dependency graph analysis for architecture constraint checking).

---

## Design Rationale

For the full design rationale — why subprocess calls instead of the custom agent framework, why deterministic metrics separate from LLM judgment, the three-pillar architecture (metrics, architecture docs, cross-cutting constraints), and the broader vision — see the design notes in this spec above.
