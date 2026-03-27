# metrics/

Deterministic metric tools. No LLM calls happen here — everything is computed from code using established static analysis tools.

## Current Files

| File | Status | Purpose |
|------|--------|---------|
| `runner.py` | BUILT | Runs all metric tools on changed files, returns structured JSON |

## How It Works

`runner.py` takes a list of changed files and runs each metric tool against them. Results are aggregated into a single JSON structure. The review pipeline calls this after LLM fixes are applied (step 5) to catch implementation-level quality issues that deterministic tools are better at finding than LLMs.

When `scope` is set in config (monorepo support), `coverage.xml` is looked up in `{repo_path}/{scope}/coverage.xml` first, then falls back to `{repo_path}/coverage.xml`. Other tools (radon, jscpd, ruff) don't need scope-specific changes because they receive already-scoped file lists from `git_context.list_changed_files()`.

## Tools Used

| Tool | What It Measures | Install |
|------|-----------------|---------|
| `radon cc` | Cyclomatic complexity per function | `pip install radon` |
| `diff-cover` | Coverage delta on changed lines | `pip install diff-cover` (requires `coverage.xml` from test run) |
| `jscpd` | Syntactic duplication detection | `npm install -g jscpd` |
| `ruff check` | Lint violations | `pip install ruff` |

Future: `grimp` for dependency graph analysis (Phase 3 — architecture constraint checking).

## Output Format

`runner.py` returns a JSON object with a `violations` array. Each violation has:

```json
{
  "violations": [
    {
      "file": "src/services/order.py",
      "line": 45,
      "metric": "cyclomatic_complexity",
      "value": 15,
      "threshold": 10,
      "detail": "Function process_order has complexity 15 (threshold: 10)"
    },
    {
      "file": "src/services/order.py",
      "line": 45,
      "metric": "function_length",
      "value": 72,
      "threshold": 50,
      "detail": "Function process_order is 72 lines (threshold: 50)"
    }
  ],
  "summary": {
    "complexity_violations": 1,
    "coverage_violations": 0,
    "duplication_violations": 0,
    "lint_violations": 0,
    "total_violations": 1
  }
}
```

## Thresholds

Configured in `config.yaml` under the `metrics` key:

| Metric | Config Key | Default |
|--------|-----------|---------|
| Cyclomatic complexity | `cyclomatic_complexity_threshold` | 10 |
| Function length (lines) | `function_length_threshold` | 50 |
| File length (lines) | `file_length_threshold` | 500 |
| Coverage delta (% of new lines) | `coverage_delta_minimum` | 80 |
| Duplication (jscpd tokens) | `duplication_min_tokens` | 50 |

## Adding a New Metric

1. Add the tool invocation to `runner.py`
2. Parse the tool's output into the standard violation format (`file`, `line`, `metric`, `value`, `threshold`, `detail`)
3. Add the threshold to `config.yaml` with a default value
4. Update the `summary` section to include the new metric count
5. Update this CLAUDE.md
