# prompts/

Markdown prompt templates passed to `claude -p` via the `lib/invoke.py` wrapper. These are the instructions each LLM agent receives — they define what the agent does, what context it has, and what output format to use.

## Current Prompts

| File | Status | Stage | Step | Tools |
|------|--------|-------|------|-------|
| `review_emit.md` | BUILT | review | Emit issues | Read-only (Read, Glob, Grep, Bash) |
| `review_coherence.md` | BUILT | review | Coherence check | Read-only (Read, Glob, Grep, Bash) |
| `review_fix.md` | BUILT | review | Apply fixes | Full (Read, Write, Edit, Bash, Glob, Grep) |
| `review_verify.md` | BUILT | review | Verification | Read-only (Read, Glob, Grep, Bash) |
| `metrics_fix.md` | BUILT | review | Fix metric violations | Full (Read, Write, Edit, Bash, Glob, Grep) |
| `simplify.md` | BUILT | review | Code simplification | Full (Read, Write, Edit, Bash, Glob, Grep) |

## Planned Prompts

Future stages will add: `plan.md`, `implement.md`, `judge.md`, and their sub-step variants.

## Naming Convention

```
{stage}_{step}.md
```

Examples: `review_emit.md`, `review_coherence.md`, `plan_generate.md`, `judge_evaluate.md`.

For prompts shared across stages (like `metrics_fix.md`), use the logical owner as the prefix.

## Design Principles

These are non-negotiable rules for every prompt file:

### 1. Self-Contained

The prompt includes ALL context the agent needs. No conversation history, no shared state, no assumption that the agent remembers anything from a previous call. The orchestrator passes the diff, changed file list, and any prior step outputs explicitly via template variables.

### 2. Role-Specific Tools

Analysis prompts are read-only: `--allowedTools Read,Glob,Grep`. Fix prompts get write access: `--allowedTools Read,Write,Edit,Bash,Glob,Grep`. No agent gets more tools than it needs. The orchestrator controls this — the prompt should document which tools it expects at the top.

### 3. Output Format Specified

Each prompt tells the agent exactly what format to use for output. The review_emit prompt specifies a structured issue list format. The coherence prompt specifies a fix plan format. This makes downstream parsing reliable without requiring the API's structured output feature.

### 4. Context Injection via Template Variables

The orchestrator replaces `{{VARIABLE_NAME}}` placeholders before passing the prompt to `claude -p`. Common variables:

| Variable | Description | Injected by | Used by |
|----------|-------------|-------------|---------|
| `{{CONTEXT_INSTRUCTIONS}}` | Instructions for the agent to fetch the diff via Bash (replaces inline `{{DIFF}}`) | review.py | review_emit, review_coherence, review_fix, review_verify, simplify |
| `{{CHANGED_FILES}}` | Newline-separated list of changed files | git_context.py | review_emit, review_coherence, review_fix, review_verify, metrics_fix, simplify |
| `{{BASE_BRANCH}}` | Base branch name (e.g. origin/main) | git_context.py | review_emit |
| `{{ISSUES}}` | Full output from review_emit step | run_logger.py | review_coherence |
| `{{FIX_PLAN}}` | Consolidated fix plan from coherence step | run_logger.py | review_fix |
| `{{PREVIOUS_ISSUES}}` | Issues from original review (for reference) | run_logger.py | review_verify |
| `{{METRICS_JSON}}` | Deterministic metric violations as JSON | metrics/runner.py | metrics_fix |

### 5. Performance Review Has a High Bar

Any prompt that reviews performance must include this instruction: "Only flag performance issues that would cause failures, timeouts, or severe degradation under normal expected load. Do not flag optimizations that would make fast code faster. The bar is 'something is completely wrong' not 'this could be better.'"

## Adding a New Prompt

1. Create `prompts/{stage}_{step}.md`
2. Document which tools the agent needs at the top of the prompt
3. Specify the exact output format the agent should use
4. List all `{{VARIABLE}}` placeholders the prompt expects
5. Update this CLAUDE.md with the new prompt entry
