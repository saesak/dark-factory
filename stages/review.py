#!/usr/bin/env python3
"""Review pipeline orchestration for the Dark Factory pipeline.

Implements the review pipeline:
    Steps 1-8: Review loop (may iterate up to max_iterations times)
        Step 1: LLM Review — Emit Issues (review_emit.md)
        Step 2: Coherence Check (review_coherence.md)
        Step 3: Apply Fixes (review_fix.md)
        Step 4: Run Tests
        Step 5: Deterministic Metrics (runner.py)
        Step 6: Fix Metric Violations (metrics_fix.md) — only if violations exist
        Step 7: Run Tests Again — only if step 6 ran
        Step 8: Verification (review_verify.md)
            -> If issues found and iteration < max: loop back to step 2
            -> If clean or max iterations: done
    Step 9: Simplify (simplify.md) — code reuse, quality, efficiency pass
    Step 10: Run Tests — only if step 9 ran and test_command configured
    Step 11: Update Docs (update_docs.md) — update markdown docs near changed files

Entry point: run(config) -> dict
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Add the dark-factory root to the path so lib/ and metrics/ are importable
_DARK_FACTORY_DIR: Path = Path(__file__).resolve().parent.parent
if str(_DARK_FACTORY_DIR) not in sys.path:
    sys.path.insert(0, str(_DARK_FACTORY_DIR))

from lib.git_context import (  # noqa: E402
    compute_diff,
    detect_worktree_name,
    get_current_sha,
    list_changed_files,
    list_pr_changed_files,
)
from lib.invoke import build_prompt, invoke_claude  # noqa: E402
from lib.run_logger import (  # noqa: E402
    create_run,
    save_diff_snapshot,
    save_step_error,
    save_step_output,
    write_run_json,
)
from lib.project_context import discover_project_docs  # noqa: E402
from metrics.runner import run_metrics  # noqa: E402

# Prompt file paths
PROMPTS_DIR: Path = _DARK_FACTORY_DIR / "prompts"
PROMPT_REVIEW_EMIT: str = str(PROMPTS_DIR / "review_emit.md")
PROMPT_COHERENCE: str = str(PROMPTS_DIR / "review_coherence.md")
PROMPT_FIX: str = str(PROMPTS_DIR / "review_fix.md")
PROMPT_VERIFY: str = str(PROMPTS_DIR / "review_verify.md")
PROMPT_METRICS_FIX: str = str(PROMPTS_DIR / "metrics_fix.md")
PROMPT_SIMPLIFY: str = str(PROMPTS_DIR / "simplify.md")
PROMPT_UPDATE_DOCS: str = str(PROMPTS_DIR / "update_docs.md")
PROMPT_REVIEW_INVARIANTS: str = str(PROMPTS_DIR / "review_invariants.md")
PROMPT_REVIEW_DOCS: str = str(PROMPTS_DIR / "review_docs.md")

# Tool sets
READONLY_TOOLS: list[str] = ["Read", "Glob", "Grep", "Bash"]
WRITE_TOOLS: list[str] = ["Read", "Write", "Edit", "Bash", "Glob", "Grep"]
INVARIANT_TOOLS: list[str] = ["Read", "Glob", "Grep", "Bash", "Agent"]


def run(config: dict[str, Any]) -> dict[str, Any]:
    """Run the review pipeline.

    Args:
        config: Parsed config merged with CLI overrides. Expected keys:
            - base_branch (str)
            - max_iterations (int)
            - repo_path (str)
            - dry_run (bool)
            - metrics_only (bool)
            - no_metrics (bool)
            - name (str | None) — override for worktree/PR identifier
            - test_command (str | None)
            - scope (str | None) — subdirectory to scope review to (monorepo support)
            - metrics (dict) — threshold configuration
            - timeouts (dict) — per-step timeout in ms

    Returns:
        Result dict matching the run.json schema.
    """
    # Extract config values
    repo_path: str = config.get("repo_path", ".")
    base_branch: str = config.get("base_branch", "origin/main")
    max_iterations: int = config.get("max_iterations", 2)
    dry_run: bool = config.get("dry_run", False)
    metrics_only: bool = config.get("metrics_only", False)
    no_metrics: bool = config.get("no_metrics", False)
    no_simplify: bool = config.get("no_simplify", False)
    no_docs: bool = config.get("no_docs", False)
    name_override: str | None = config.get("name")
    test_command: str | None = config.get("test_command")
    timeouts: dict[str, int] = config.get("timeouts", {})
    scope: str | None = config.get("scope")
    files: list[str] | None = config.get("files")
    model: str | None = config.get("model")
    pr_config: dict[str, Any] | None = config.get("pr")

    # Determine test working directory — monorepo subpackages run tests from scope dir
    test_cwd: str = str(Path(repo_path) / scope) if scope else repo_path

    # PR mode: checkout the branch locally so fix/simplify steps can write files
    if pr_config and not dry_run:
        pr_repo: str = pr_config["repo"]
        pr_num: int = pr_config["number"]
        print(f"[PR mode] Checking out PR #{pr_num} from {pr_repo}...")
        checkout_result: subprocess.CompletedProcess[str] = subprocess.run(
            ["gh", "pr", "checkout", str(pr_num), "--repo", pr_repo],
            capture_output=True,
            text=True,
            cwd=repo_path,
        )
        if checkout_result.returncode != 0:
            print(f"Error: gh pr checkout failed: {checkout_result.stderr.strip()}")
            return {
                "final_status": "error",
                "summary": "Failed to checkout PR branch",
                "issues_found": 0,
                "issues_fixed": 0,
                "issues_remaining": 0,
            }
        print("  Checked out PR branch successfully.")

    # Build context instructions for agents (replaces inline diff)
    if pr_config:
        pr_repo = pr_config["repo"]
        pr_num = pr_config["number"]
        original_context_instructions: str = (
            f"Run this command to fetch the original PR diff:\n\n"
            f"```bash\ngh pr diff {pr_num} --repo {pr_repo}\n```\n\n"
            f"This is the authoritative diff for this review. Do NOT use any other diff."
        )
    else:
        if files:
            files_suffix: str = " -- " + " ".join(files)
        elif scope:
            files_suffix = f" -- {scope}"
        else:
            files_suffix = ""
        original_context_instructions = (
            f"Run this command to fetch the diff:\n\n"
            f"```bash\ngit diff {base_branch}...HEAD{files_suffix}\n```\n\n"
            f"This is the authoritative diff for this review. Do NOT use any other diff."
        )

    # current_context_instructions always uses local git diff (fixes have been applied locally)
    if files:
        current_suffix: str = " -- " + " ".join(files)
    elif scope:
        current_suffix = f" -- {scope}"
    else:
        current_suffix = ""
    current_context_instructions: str = (
        f"Run this command to fetch the current diff (includes all applied fixes):\n\n"
        f"```bash\ngit diff {base_branch}...HEAD{current_suffix}\n```\n\n"
        f"This is the authoritative diff for this review. Do NOT use any other diff."
    )

    # Gather changed files
    if pr_config:
        changed_files = list_pr_changed_files(pr_config["repo"], pr_config["number"])
    else:
        changed_files = list_changed_files(repo_path, base_branch, scope, files)
    changed_files_str: str = "\n".join(changed_files)
    sha_start: str = get_current_sha(repo_path)

    # Discover project-specific documentation
    project_docs: dict[str, Any] = discover_project_docs(repo_path, changed_files)
    has_project_context: bool = bool(
        project_docs.get("invariants") or project_docs.get("conventions")
    )
    has_doc_check_context: bool = bool(
        project_docs.get("stale_docs") or project_docs.get("doc_guidelines")
    )

    # Build conventions block for emit prompt
    conventions_block: str = ""
    if project_docs.get("conventions"):
        conventions_block = (
            "\n## Repo-Specific Conventions\n\n"
            "In addition to the engineering preferences above, this repo has documented "
            "conventions. Check the diff against these:\n\n"
            + project_docs["conventions"]
        )

    # Read config flags for new passes
    no_invariants: bool = config.get("no_invariants", False)
    no_docs_check: bool = config.get("no_docs_check", False)
    invariants_only: bool = config.get("invariants_only", False)

    if not changed_files:
        print("No changes detected. Nothing to review.")
        return {
            "final_status": "pass",
            "summary": "No changes detected",
            "issues_found": 0,
            "issues_fixed": 0,
            "issues_remaining": 0,
        }

    # Determine run identifier
    worktree_name: str = name_override or detect_worktree_name(repo_path)

    # Create run directory
    run_dir: str = create_run(worktree_name, "review")
    run_id: str = Path(run_dir).name
    print(f"Run directory: {run_dir}")

    # Save diff snapshot (local mode only — PR mode saves a placeholder)
    if pr_config:
        save_diff_snapshot(
            run_dir,
            f"[PR mode] See gh pr diff {pr_config['number']} --repo {pr_config['repo']}",
        )
    else:
        diff: str = compute_diff(repo_path, base_branch, scope, files)
        save_diff_snapshot(run_dir, diff)

    # Initialize timing and result tracking
    pipeline_start: float = time.monotonic()
    started_at: str = datetime.now(timezone.utc).isoformat()
    steps_log: list[dict[str, Any]] = []
    final_status: str = "pass"
    issues_found: int = 0
    issues_fixed: int = 0
    issues_remaining: int = 0
    iterations_completed: int = 0
    metrics_summary: dict[str, int] = {}

    # Shared state across iterations
    emit_output: str = ""
    coherence_output: str = ""

    # --- METRICS-ONLY MODE ---
    if metrics_only:
        print("[metrics-only] Skipping LLM review, running metrics only.")
        step5_result: dict[str, Any] = _run_step_metrics(
            5, run_dir, changed_files, config, steps_log
        )
        metrics_summary = step5_result.get("summary", {})
        violations: list[dict] = step5_result.get("violations", [])

        if violations and not no_metrics:
            _run_step_metrics_fix(
                6,
                run_dir,
                step5_result,
                changed_files_str,
                timeouts,
                repo_path,
                steps_log,
                scope,
                files=files,
                model=model,
            )
            if test_command:
                test_ok: bool = _run_step_test(
                    7, run_dir, test_command, test_cwd, timeouts, steps_log
                )
                if not test_ok:
                    final_status = "stopped_test_failure"

        final_status = final_status if final_status != "pass" else "pass"
        return _finalize_run(
            run_dir,
            run_id,
            worktree_name,
            repo_path,
            base_branch,
            sha_start,
            started_at,
            pipeline_start,
            final_status,
            1,
            issues_found,
            issues_fixed,
            issues_remaining,
            steps_log,
            metrics_summary,
        )

    # --- DRY RUN MODE ---
    if dry_run:
        print("[dry-run] Emitting issues only, will not apply fixes.")

        run_invariants: bool = has_project_context and not no_invariants
        run_docs_check: bool = has_doc_check_context and not no_docs_check

        # Prepare doc check data
        stale_docs_json: str = json.dumps(project_docs.get("stale_docs", []))
        doc_index_json: str = json.dumps(project_docs.get("doc_index", []))
        changed_md_str: str = "\n".join(project_docs.get("changed_md_files", []))

        with ThreadPoolExecutor(max_workers=3) as executor:
            # Code quality + conventions (skip if invariants_only)
            emit_future: Future[str] | None = None
            if not invariants_only:
                emit_future = executor.submit(
                    _run_step_emit,
                    1,
                    run_dir,
                    original_context_instructions,
                    changed_files_str,
                    base_branch,
                    timeouts,
                    repo_path,
                    steps_log,
                    files,
                    model,
                    conventions_block,
                )

            # Invariant check
            invariants_future: Future[str] | None = None
            if run_invariants:
                invariants_future = executor.submit(
                    _run_step_invariants,
                    12,
                    run_dir,
                    original_context_instructions,
                    changed_files_str,
                    project_docs["invariants"],
                    project_docs.get("conventions", ""),
                    timeouts,
                    repo_path,
                    steps_log,
                    files,
                    model,
                )

            # Doc check
            docs_future: Future[str] | None = None
            if run_docs_check:
                docs_future = executor.submit(
                    _run_step_docs_check,
                    13,
                    run_dir,
                    original_context_instructions,
                    changed_files_str,
                    stale_docs_json,
                    doc_index_json,
                    project_docs.get("doc_guidelines", ""),
                    changed_md_str,
                    timeouts,
                    repo_path,
                    steps_log,
                    files,
                    model,
                )

        # Collect results
        emit_output = emit_future.result() if emit_future else ""
        invariants_output: str = invariants_future.result() if invariants_future else ""
        docs_output: str = docs_future.result() if docs_future else ""

        if emit_future and not emit_output:
            final_status = "error"
        else:
            print("\n--- Review Issues ---")
            if emit_output:
                print(emit_output)
            if invariants_output:
                print("\n--- Invariant Check ---")
                print(invariants_output)
            if docs_output:
                print("\n--- Documentation Check ---")
                print(docs_output)
            print("--- End Issues ---\n")

        return _finalize_run(
            run_dir,
            run_id,
            worktree_name,
            repo_path,
            base_branch,
            sha_start,
            started_at,
            pipeline_start,
            final_status,
            0,
            issues_found,
            issues_fixed,
            issues_remaining,
            steps_log,
            metrics_summary,
        )

    # --- FULL PIPELINE ---
    for iteration in range(1, max_iterations + 1):
        print(f"\n{'=' * 60}")
        print(f"  Iteration {iteration} of {max_iterations}")
        print(f"{'=' * 60}\n")

        # Recompute changed files for iterations > 1 (fixes may have changed things)
        if iteration > 1:
            changed_files = list_changed_files(repo_path, base_branch, scope, files)
            changed_files_str = "\n".join(changed_files)

        # Step 1: LLM Review — Emit Issues (only on first iteration)
        if iteration == 1:
            emit_output = _run_step_emit(
                1,
                run_dir,
                original_context_instructions,
                changed_files_str,
                base_branch,
                timeouts,
                repo_path,
                steps_log,
                files=files,
                model=model,
                project_conventions=conventions_block,
            )
            if not emit_output:
                final_status = "error"
                break
            issues_found = _count_issues(emit_output)

        # Step 2: Coherence Check
        # On iteration > 1, the "issues" come from the verify step
        issues_for_coherence: str = emit_output
        coherence_output = _run_step_coherence(
            2,
            run_dir,
            issues_for_coherence,
            original_context_instructions,
            changed_files_str,
            timeouts,
            repo_path,
            steps_log,
            model=model,
        )
        if not coherence_output:
            final_status = "error"
            break

        # Step 3: Apply Fixes
        fix_output: str = _run_step_fix(
            3,
            run_dir,
            coherence_output,
            original_context_instructions,
            changed_files_str,
            timeouts,
            repo_path,
            steps_log,
            scope,
            files=files,
            model=model,
        )
        if not fix_output:
            final_status = "error"
            break

        # Step 4: Run Tests
        if test_command:
            test_ok = _run_step_test(
                4, run_dir, test_command, test_cwd, timeouts, steps_log
            )
            if not test_ok:
                final_status = "stopped_test_failure"
                break
        else:
            print("[Step 4/8] Skipping tests — no test_command configured")

        # Step 5: Deterministic Metrics
        if not no_metrics:
            step5_result = _run_step_metrics(
                5, run_dir, changed_files, config, steps_log
            )
            metrics_summary = step5_result.get("summary", {})
            violations = step5_result.get("violations", [])

            # Step 6: Fix Metric Violations (only if violations exist)
            if violations:
                _run_step_metrics_fix(
                    6,
                    run_dir,
                    step5_result,
                    changed_files_str,
                    timeouts,
                    repo_path,
                    steps_log,
                    scope,
                    files=files,
                    model=model,
                )

                # Step 7: Run Tests Again (only if step 6 ran)
                if test_command:
                    test_ok = _run_step_test(
                        7, run_dir, test_command, test_cwd, timeouts, steps_log
                    )
                    if not test_ok:
                        final_status = "stopped_test_failure"
                        break
                else:
                    print("[Step 7/8] Skipping tests — no test_command configured")
            else:
                print("[Step 5/8] No metric violations found — skipping step 6")
        else:
            print("[Step 5-7] Skipping metrics — --no-metrics flag set")

        # Step 8: Verification
        updated_files: list[str] = list_changed_files(
            repo_path, base_branch, scope, files
        )
        updated_files_str: str = "\n".join(updated_files)

        verify_output: str = _run_step_verify(
            8,
            run_dir,
            current_context_instructions,
            updated_files_str,
            emit_output,
            timeouts,
            repo_path,
            steps_log,
            model=model,
        )

        iterations_completed = iteration

        if not verify_output:
            final_status = "error"
            break

        # Check if verification is clean
        is_clean: bool = "CLEAN" in verify_output.upper()
        if is_clean:
            print("[Step 8/8] Verification: CLEAN — no remaining issues")
            final_status = "pass"
            issues_fixed = issues_found
            issues_remaining = 0
            break

        # Issues found in verification
        new_issues: int = _count_issues(verify_output)
        print(f"[Step 8/8] Verification found {new_issues} new issue(s)")

        if iteration < max_iterations:
            print(f"  Looping back to step 2 for iteration {iteration + 1}")
            # Feed verify output as the new "issues" for the next coherence step
            emit_output = verify_output
        else:
            print(
                f"  Max iterations ({max_iterations}) reached. "
                f"Logging remaining issues."
            )
            final_status = "max_iterations"
            issues_remaining = new_issues
            break

    # Step 9: Simplify — runs once after all review iterations
    if final_status in ("pass", "max_iterations") and not dry_run and not no_simplify:
        simplify_files: list[str] = list_changed_files(
            repo_path, base_branch, scope, files
        )
        simplify_files_str: str = "\n".join(simplify_files)
        if simplify_files:
            _run_step_simplify(
                9,
                run_dir,
                current_context_instructions,
                simplify_files_str,
                timeouts,
                repo_path,
                steps_log,
                scope,
                files=files,
                model=model,
            )
            # Run tests after simplification if configured
            if test_command:
                test_ok = _run_step_test(
                    10, run_dir, test_command, test_cwd, timeouts, steps_log
                )
                if not test_ok:
                    final_status = "stopped_test_failure"

    # Step 11: Update Docs — update markdown docs near changed files
    if final_status in ("pass", "max_iterations") and not dry_run and not no_docs:
        docs_files: list[str] = list_changed_files(repo_path, base_branch, scope, files)
        docs_files_str: str = "\n".join(docs_files)
        if docs_files:
            _run_step_update_docs(
                11,
                run_dir,
                current_context_instructions,
                docs_files_str,
                timeouts,
                repo_path,
                steps_log,
                scope,
                files=files,
                model=model,
            )

    # Steps 12-13: Project-specific checks (parallel)
    if final_status in ("pass", "max_iterations") and not dry_run:
        run_invariants: bool = has_project_context and not no_invariants
        run_docs_check: bool = has_doc_check_context and not no_docs_check

        if run_invariants or run_docs_check:
            final_changed: list[str] = list_changed_files(
                repo_path, base_branch, scope, files
            )
            final_changed_str: str = "\n".join(final_changed)
            stale_docs_json: str = json.dumps(project_docs.get("stale_docs", []))
            doc_index_json: str = json.dumps(project_docs.get("doc_index", []))
            changed_md_str: str = "\n".join(project_docs.get("changed_md_files", []))

            with ThreadPoolExecutor(max_workers=2) as executor:
                inv_future: Future[str] | None = None
                if run_invariants:
                    inv_future = executor.submit(
                        _run_step_invariants,
                        12,
                        run_dir,
                        current_context_instructions,
                        final_changed_str,
                        project_docs["invariants"],
                        project_docs.get("conventions", ""),
                        timeouts,
                        repo_path,
                        steps_log,
                        files,
                        model,
                    )
                doc_future: Future[str] | None = None
                if run_docs_check:
                    doc_future = executor.submit(
                        _run_step_docs_check,
                        13,
                        run_dir,
                        current_context_instructions,
                        final_changed_str,
                        stale_docs_json,
                        doc_index_json,
                        project_docs.get("doc_guidelines", ""),
                        changed_md_str,
                        timeouts,
                        repo_path,
                        steps_log,
                        files,
                        model,
                    )

            if inv_future:
                inv_output: str = inv_future.result()
                if inv_output:
                    print("\n--- Invariant Check ---")
                    print(inv_output)
            if doc_future:
                doc_output: str = doc_future.result()
                if doc_output:
                    print("\n--- Documentation Check ---")
                    print(doc_output)

    # Correct issue accounting: remaining = found - fixed
    if issues_remaining == 0 and issues_fixed < issues_found:
        issues_remaining = issues_found - issues_fixed

    # Finalize
    return _finalize_run(
        run_dir,
        run_id,
        worktree_name,
        repo_path,
        base_branch,
        sha_start,
        started_at,
        pipeline_start,
        final_status,
        iterations_completed,
        issues_found,
        issues_fixed,
        issues_remaining,
        steps_log,
        metrics_summary,
    )


# --- Step Implementations ---


def _build_scope_note(
    files: list[str] | None,
    scope: str | None,
    verb: str,
) -> str:
    """Build a scope/files note to prepend to agent prompts."""
    if files:
        return (
            f"\n\nNOTE: This review is scoped to the following files: {', '.join(files)}. "
            f"Focus your {verb} on these files only.\n"
        )
    if scope:
        return (
            f"\n\nNOTE: This review is scoped to the `{scope}` subdirectory "
            f"of a monorepo. All file paths are relative to the repo root. "
            f"Focus your {verb} on files under `{scope}`.\n"
        )
    return ""


def _run_step_emit(
    step_num: int,
    run_dir: str,
    context_instructions: str,
    changed_files_str: str,
    base_branch: str,
    timeouts: dict[str, int],
    repo_path: str,
    steps_log: list[dict[str, Any]],
    files: list[str] | None = None,
    model: str | None = None,
    project_conventions: str = "",
) -> str:
    """Step 1: LLM Review — Emit Issues."""
    print("[Step 1/8] Running LLM review (emit issues)...")
    step_start: str = datetime.now(timezone.utc).isoformat()
    timeout: int = timeouts.get("review_emit", 600000)

    prompt: str = build_prompt(
        PROMPT_REVIEW_EMIT,
        {
            "CONTEXT_INSTRUCTIONS": context_instructions,
            "CHANGED_FILES": changed_files_str,
            "BASE_BRANCH": base_branch,
            "PROJECT_CONVENTIONS": project_conventions,
        },
    )

    scope_note: str = _build_scope_note(files, None, "review")
    if scope_note:
        prompt = scope_note + prompt

    debug_file: str = str(Path(run_dir) / "steps" / f"{step_num}_review_emit_debug.txt")
    result: dict = invoke_claude(
        prompt, READONLY_TOOLS, timeout, repo_path, debug_file=debug_file, model=model
    )

    output: str = result["stdout"]
    save_step_output(run_dir, step_num, "review_emit", output)
    if result["stderr"]:
        save_step_error(run_dir, step_num, "review_emit", result["stderr"])

    steps_log.append(
        {
            "step": step_num,
            "name": "review_emit",
            "started_at": step_start,
            "wall_clock_ms": result["wall_clock_ms"],
            "exit_code": result["exit_code"],
            "timed_out": result["timed_out"],
            "output_path": f"steps/{step_num}_review_emit_stdout.txt",
        }
    )

    if result["timed_out"]:
        print("  WARNING: Step 1 timed out")
        return ""
    elif result["exit_code"] != 0:
        print(f"  WARNING: Step 1 exited with code {result['exit_code']}")
        return ""

    print(f"  Completed in {result['wall_clock_ms'] / 1000:.1f}s")
    return output


def _run_step_coherence(
    step_num: int,
    run_dir: str,
    issues: str,
    context_instructions: str,
    changed_files_str: str,
    timeouts: dict[str, int],
    repo_path: str,
    steps_log: list[dict[str, Any]],
    model: str | None = None,
) -> str:
    """Step 2: Coherence Check."""
    print("[Step 2/8] Running coherence check...")
    step_start: str = datetime.now(timezone.utc).isoformat()
    timeout: int = timeouts.get("coherence", 300000)

    prompt: str = build_prompt(
        PROMPT_COHERENCE,
        {
            "ISSUES": issues,
            "CONTEXT_INSTRUCTIONS": context_instructions,
            "CHANGED_FILES": changed_files_str,
        },
    )

    debug_file: str = str(Path(run_dir) / "steps" / f"{step_num}_coherence_debug.txt")
    result: dict = invoke_claude(
        prompt, READONLY_TOOLS, timeout, repo_path, debug_file=debug_file, model=model
    )

    output: str = result["stdout"]
    save_step_output(run_dir, step_num, "coherence", output)
    if result["stderr"]:
        save_step_error(run_dir, step_num, "coherence", result["stderr"])

    steps_log.append(
        {
            "step": step_num,
            "name": "coherence",
            "started_at": step_start,
            "wall_clock_ms": result["wall_clock_ms"],
            "exit_code": result["exit_code"],
            "timed_out": result["timed_out"],
            "output_path": f"steps/{step_num}_coherence_stdout.txt",
        }
    )

    if result["timed_out"]:
        print("  WARNING: Step 2 timed out")
        return ""
    elif result["exit_code"] != 0:
        print(f"  WARNING: Step 2 exited with code {result['exit_code']}")
        return ""

    print(f"  Completed in {result['wall_clock_ms'] / 1000:.1f}s")
    return output


def _run_step_fix(
    step_num: int,
    run_dir: str,
    fix_plan: str,
    context_instructions: str,
    changed_files_str: str,
    timeouts: dict[str, int],
    repo_path: str,
    steps_log: list[dict[str, Any]],
    scope: str | None = None,
    files: list[str] | None = None,
    model: str | None = None,
) -> str:
    """Step 3: Apply Fixes."""
    print("[Step 3/8] Applying fixes...")
    step_start: str = datetime.now(timezone.utc).isoformat()
    timeout: int = timeouts.get("fix", 600000)

    variables: dict[str, str] = {
        "FIX_PLAN": fix_plan,
        "CONTEXT_INSTRUCTIONS": context_instructions,
        "CHANGED_FILES": changed_files_str,
    }

    prompt: str = build_prompt(PROMPT_FIX, variables)

    scope_note: str = _build_scope_note(files, scope, "fixes")
    if scope_note:
        prompt = scope_note + prompt

    debug_file: str = str(Path(run_dir) / "steps" / f"{step_num}_fix_debug.txt")
    result: dict = invoke_claude(
        prompt, WRITE_TOOLS, timeout, repo_path, debug_file=debug_file, model=model
    )

    output: str = result["stdout"]
    save_step_output(run_dir, step_num, "fix", output)
    if result["stderr"]:
        save_step_error(run_dir, step_num, "fix", result["stderr"])

    steps_log.append(
        {
            "step": step_num,
            "name": "fix",
            "started_at": step_start,
            "wall_clock_ms": result["wall_clock_ms"],
            "exit_code": result["exit_code"],
            "timed_out": result["timed_out"],
            "output_path": f"steps/{step_num}_fix_stdout.txt",
        }
    )

    if result["timed_out"]:
        print("  WARNING: Step 3 timed out")
        return ""
    elif result["exit_code"] != 0:
        print(f"  WARNING: Step 3 exited with code {result['exit_code']}")
        return ""

    print(f"  Completed in {result['wall_clock_ms'] / 1000:.1f}s")
    return output


def _run_step_test(
    step_num: int,
    run_dir: str,
    test_command: str,
    test_cwd: str,
    timeouts: dict[str, int],
    steps_log: list[dict[str, Any]],
) -> bool:
    """Step 4/7: Run Tests. Returns True if tests pass.

    Args:
        step_num: The step number (4 or 7).
        run_dir: Path to the run directory.
        test_command: Shell command to run tests.
        test_cwd: Working directory for the test command. When scope is set,
            this is {repo_path}/{scope} so monorepo subpackage test runners work.
        timeouts: Per-step timeout configuration.
        steps_log: Mutable list to append step result to.

    Returns:
        True if tests pass, False otherwise.
    """
    print(f"[Step {step_num}/8] Running tests...")
    step_start: str = datetime.now(timezone.utc).isoformat()
    timeout_ms: int = timeouts.get("test", 600000)
    timeout_s: float = timeout_ms / 1000.0

    start_time: float = time.monotonic()
    timed_out: bool = False
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1

    try:
        result: subprocess.CompletedProcess[str] = subprocess.run(
            test_command,
            shell=True,
            capture_output=True,
            text=True,
            cwd=test_cwd,
            timeout=timeout_s,
        )
        stdout = result.stdout
        stderr = result.stderr
        exit_code = result.returncode
    except subprocess.TimeoutExpired as e:
        timed_out = True
        stdout = e.stdout if isinstance(e.stdout, str) else ""
        stderr = e.stderr if isinstance(e.stderr, str) else ""

    wall_clock_ms: int = int((time.monotonic() - start_time) * 1000)

    # Save output
    combined_output: str = stdout
    if stderr:
        combined_output += f"\n--- stderr ---\n{stderr}"
    save_step_output(run_dir, step_num, "test", combined_output)

    steps_log.append(
        {
            "step": step_num,
            "name": "test",
            "started_at": step_start,
            "wall_clock_ms": wall_clock_ms,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "output_path": f"steps/{step_num}_test_stdout.txt",
        }
    )

    if timed_out:
        print(f"  FAILED: Tests timed out after {timeout_ms / 1000:.0f}s")
        return False
    if exit_code != 0:
        print(f"  FAILED: Tests exited with code {exit_code}")
        print("  Pipeline will stop — test failures signal fixes were wrong.")
        return False

    print(f"  Tests passed in {wall_clock_ms / 1000:.1f}s")
    return True


def _run_step_metrics(
    step_num: int,
    run_dir: str,
    changed_files: list[str],
    config: dict[str, Any],
    steps_log: list[dict[str, Any]],
) -> dict[str, Any]:
    """Step 5: Deterministic Metrics."""
    print("[Step 5/8] Running deterministic metrics...")
    step_start: str = datetime.now(timezone.utc).isoformat()
    start_time: float = time.monotonic()

    metrics_result: dict = run_metrics(changed_files, config)

    wall_clock_ms: int = int((time.monotonic() - start_time) * 1000)

    # Save metrics JSON output
    metrics_json: str = json.dumps(metrics_result, indent=2)
    save_step_output(run_dir, step_num, "metrics", metrics_json)

    summary: dict[str, int] = metrics_result.get("summary", {})
    total: int = summary.get("total_violations", 0)

    steps_log.append(
        {
            "step": step_num,
            "name": "metrics",
            "started_at": step_start,
            "wall_clock_ms": wall_clock_ms,
            "exit_code": 0,
            "timed_out": False,
            "output_path": f"steps/{step_num}_metrics_stdout.txt",
        }
    )

    print(f"  Found {total} violation(s) in {wall_clock_ms / 1000:.1f}s")
    if total > 0:
        print(
            f"    Complexity: {summary.get('complexity_violations', 0)}, "
            f"Coverage: {summary.get('coverage_violations', 0)}, "
            f"Duplication: {summary.get('duplication_violations', 0)}, "
            f"Lint: {summary.get('lint_violations', 0)}"
        )

    return metrics_result


def _run_step_metrics_fix(
    step_num: int,
    run_dir: str,
    metrics_result: dict[str, Any],
    changed_files_str: str,
    timeouts: dict[str, int],
    repo_path: str,
    steps_log: list[dict[str, Any]],
    scope: str | None = None,
    files: list[str] | None = None,
    model: str | None = None,
) -> str:
    """Step 6: Fix Metric Violations."""
    print("[Step 6/8] Fixing metric violations...")
    step_start: str = datetime.now(timezone.utc).isoformat()
    timeout: int = timeouts.get("metrics_fix", 300000)

    metrics_json: str = json.dumps(metrics_result, indent=2)
    prompt: str = build_prompt(
        PROMPT_METRICS_FIX,
        {
            "METRICS_JSON": metrics_json,
            "CHANGED_FILES": changed_files_str,
        },
    )

    scope_note: str = _build_scope_note(files, scope, "fixes")
    if scope_note:
        prompt = scope_note + prompt

    debug_file: str = str(Path(run_dir) / "steps" / f"{step_num}_metrics_fix_debug.txt")
    result: dict = invoke_claude(
        prompt, WRITE_TOOLS, timeout, repo_path, debug_file=debug_file, model=model
    )

    output: str = result["stdout"]
    save_step_output(run_dir, step_num, "metrics_fix", output)
    if result["stderr"]:
        save_step_error(run_dir, step_num, "metrics_fix", result["stderr"])

    steps_log.append(
        {
            "step": step_num,
            "name": "metrics_fix",
            "started_at": step_start,
            "wall_clock_ms": result["wall_clock_ms"],
            "exit_code": result["exit_code"],
            "timed_out": result["timed_out"],
            "output_path": f"steps/{step_num}_metrics_fix_stdout.txt",
        }
    )

    if result["timed_out"]:
        print("  WARNING: Step 6 timed out")
        return ""
    elif result["exit_code"] != 0:
        print(f"  WARNING: Step 6 exited with code {result['exit_code']}")
        return ""

    print(f"  Completed in {result['wall_clock_ms'] / 1000:.1f}s")
    return output


def _run_step_verify(
    step_num: int,
    run_dir: str,
    context_instructions: str,
    changed_files_str: str,
    previous_issues: str,
    timeouts: dict[str, int],
    repo_path: str,
    steps_log: list[dict[str, Any]],
    model: str | None = None,
) -> str:
    """Step 8: Verification."""
    print("[Step 8/8] Running verification...")
    step_start: str = datetime.now(timezone.utc).isoformat()
    timeout: int = timeouts.get("verify", 600000)

    prompt: str = build_prompt(
        PROMPT_VERIFY,
        {
            "CONTEXT_INSTRUCTIONS": context_instructions,
            "CHANGED_FILES": changed_files_str,
            "PREVIOUS_ISSUES": previous_issues,
        },
    )

    debug_file: str = str(Path(run_dir) / "steps" / f"{step_num}_verify_debug.txt")
    result: dict = invoke_claude(
        prompt, READONLY_TOOLS, timeout, repo_path, debug_file=debug_file, model=model
    )

    output: str = result["stdout"]
    save_step_output(run_dir, step_num, "verify", output)
    if result["stderr"]:
        save_step_error(run_dir, step_num, "verify", result["stderr"])

    steps_log.append(
        {
            "step": step_num,
            "name": "verify",
            "started_at": step_start,
            "wall_clock_ms": result["wall_clock_ms"],
            "exit_code": result["exit_code"],
            "timed_out": result["timed_out"],
            "output_path": f"steps/{step_num}_verify_stdout.txt",
        }
    )

    if result["timed_out"]:
        print("  WARNING: Step 8 timed out")
        return ""
    elif result["exit_code"] != 0:
        print(f"  WARNING: Step 8 exited with code {result['exit_code']}")
        return ""

    print(f"  Completed in {result['wall_clock_ms'] / 1000:.1f}s")
    return output


def _run_step_simplify(
    step_num: int,
    run_dir: str,
    context_instructions: str,
    changed_files_str: str,
    timeouts: dict[str, int],
    repo_path: str,
    steps_log: list[dict[str, Any]],
    scope: str | None = None,
    files: list[str] | None = None,
    model: str | None = None,
) -> str:
    """Step 9: Simplify — code reuse, quality, and efficiency pass."""
    print("[Step 9] Running code simplification...")
    step_start: str = datetime.now(timezone.utc).isoformat()
    timeout: int = timeouts.get("simplify", 600000)

    prompt: str = build_prompt(
        PROMPT_SIMPLIFY,
        {
            "CONTEXT_INSTRUCTIONS": context_instructions,
            "CHANGED_FILES": changed_files_str,
        },
    )

    scope_note: str = _build_scope_note(files, scope, "simplifications")
    if scope_note:
        prompt = scope_note + prompt

    debug_file: str = str(Path(run_dir) / "steps" / f"{step_num}_simplify_debug.txt")
    result: dict = invoke_claude(
        prompt, WRITE_TOOLS, timeout, repo_path, debug_file=debug_file, model=model
    )

    output: str = result["stdout"]
    save_step_output(run_dir, step_num, "simplify", output)
    if result["stderr"]:
        save_step_error(run_dir, step_num, "simplify", result["stderr"])

    steps_log.append(
        {
            "step": step_num,
            "name": "simplify",
            "started_at": step_start,
            "wall_clock_ms": result["wall_clock_ms"],
            "exit_code": result["exit_code"],
            "timed_out": result["timed_out"],
            "output_path": f"steps/{step_num}_simplify_stdout.txt",
        }
    )

    if result["timed_out"]:
        print("  WARNING: Step 9 timed out")
        return ""
    elif result["exit_code"] != 0:
        print(f"  WARNING: Step 9 exited with code {result['exit_code']}")
        return ""

    print(f"  Completed in {result['wall_clock_ms'] / 1000:.1f}s")
    return output


def _run_step_update_docs(
    step_num: int,
    run_dir: str,
    context_instructions: str,
    changed_files_str: str,
    timeouts: dict[str, int],
    repo_path: str,
    steps_log: list[dict[str, Any]],
    scope: str | None = None,
    files: list[str] | None = None,
    model: str | None = None,
) -> str:
    """Step 11: Update Docs — update markdown docs near changed files."""
    print("[Step 11] Updating documentation...")
    step_start: str = datetime.now(timezone.utc).isoformat()
    timeout: int = timeouts.get("update_docs", 300000)

    prompt: str = build_prompt(
        PROMPT_UPDATE_DOCS,
        {
            "CONTEXT_INSTRUCTIONS": context_instructions,
            "CHANGED_FILES": changed_files_str,
        },
    )

    scope_note: str = _build_scope_note(files, scope, "documentation")
    if scope_note:
        prompt = scope_note + prompt

    debug_file: str = str(Path(run_dir) / "steps" / f"{step_num}_update_docs_debug.txt")
    result: dict = invoke_claude(
        prompt, WRITE_TOOLS, timeout, repo_path, debug_file=debug_file, model=model
    )

    output: str = result["stdout"]
    save_step_output(run_dir, step_num, "update_docs", output)
    if result["stderr"]:
        save_step_error(run_dir, step_num, "update_docs", result["stderr"])

    steps_log.append(
        {
            "step": step_num,
            "name": "update_docs",
            "started_at": step_start,
            "wall_clock_ms": result["wall_clock_ms"],
            "exit_code": result["exit_code"],
            "timed_out": result["timed_out"],
            "output_path": f"steps/{step_num}_update_docs_stdout.txt",
        }
    )

    if result["timed_out"]:
        print("  WARNING: Step 11 timed out")
        return ""
    elif result["exit_code"] != 0:
        print(f"  WARNING: Step 11 exited with code {result['exit_code']}")
        return ""

    print(f"  Completed in {result['wall_clock_ms'] / 1000:.1f}s")
    return output


def _run_step_invariants(
    step_num: int,
    run_dir: str,
    context_instructions: str,
    changed_files_str: str,
    invariants: str,
    conventions: str,
    timeouts: dict[str, int],
    repo_path: str,
    steps_log: list[dict[str, Any]],
    files: list[str] | None = None,
    model: str | None = None,
) -> str:
    """Step N: Check against project invariants and conventions."""
    print(f"[Step {step_num}] Running invariant and convention check...")
    step_start: str = datetime.now(timezone.utc).isoformat()
    timeout: int = timeouts.get("review_invariants", 600000)

    prompt: str = build_prompt(
        PROMPT_REVIEW_INVARIANTS,
        {
            "CONTEXT_INSTRUCTIONS": context_instructions,
            "CHANGED_FILES": changed_files_str,
            "INVARIANTS": invariants,
            "CONVENTIONS": conventions,
        },
    )

    scope_note: str = _build_scope_note(files, None, "invariant check")
    if scope_note:
        prompt = scope_note + prompt

    debug_file: str = str(Path(run_dir) / "steps" / f"{step_num}_invariants_debug.txt")
    result: dict = invoke_claude(
        prompt, INVARIANT_TOOLS, timeout, repo_path, debug_file=debug_file, model=model
    )

    output: str = result["stdout"]
    save_step_output(run_dir, step_num, "invariants", output)
    if result["stderr"]:
        save_step_error(run_dir, step_num, "invariants", result["stderr"])

    steps_log.append(
        {
            "step": step_num,
            "name": "invariants",
            "started_at": step_start,
            "wall_clock_ms": result["wall_clock_ms"],
            "exit_code": result["exit_code"],
            "timed_out": result["timed_out"],
            "output_path": f"steps/{step_num}_invariants_stdout.txt",
        }
    )

    if result["timed_out"]:
        print(f"  WARNING: Step {step_num} timed out")
        return ""
    elif result["exit_code"] != 0:
        print(f"  WARNING: Step {step_num} exited with code {result['exit_code']}")
        return ""

    print(f"  Completed in {result['wall_clock_ms'] / 1000:.1f}s")
    return output


def _run_step_docs_check(
    step_num: int,
    run_dir: str,
    context_instructions: str,
    changed_files_str: str,
    stale_docs: str,
    doc_index: str,
    doc_guidelines: str,
    changed_md_files: str,
    timeouts: dict[str, int],
    repo_path: str,
    steps_log: list[dict[str, Any]],
    files: list[str] | None = None,
    model: str | None = None,
) -> str:
    """Step N: Check documentation staleness and quality."""
    print(f"[Step {step_num}] Running documentation check...")
    step_start: str = datetime.now(timezone.utc).isoformat()
    timeout: int = timeouts.get("review_docs", 300000)

    prompt: str = build_prompt(
        PROMPT_REVIEW_DOCS,
        {
            "CONTEXT_INSTRUCTIONS": context_instructions,
            "CHANGED_FILES": changed_files_str,
            "STALE_DOCS": stale_docs,
            "DOC_INDEX": doc_index,
            "DOC_GUIDELINES": doc_guidelines,
            "CHANGED_MD_FILES": changed_md_files,
        },
    )

    scope_note: str = _build_scope_note(files, None, "documentation check")
    if scope_note:
        prompt = scope_note + prompt

    debug_file: str = str(Path(run_dir) / "steps" / f"{step_num}_docs_check_debug.txt")
    result: dict = invoke_claude(
        prompt, READONLY_TOOLS, timeout, repo_path, debug_file=debug_file, model=model
    )

    output: str = result["stdout"]
    save_step_output(run_dir, step_num, "docs_check", output)
    if result["stderr"]:
        save_step_error(run_dir, step_num, "docs_check", result["stderr"])

    steps_log.append(
        {
            "step": step_num,
            "name": "docs_check",
            "started_at": step_start,
            "wall_clock_ms": result["wall_clock_ms"],
            "exit_code": result["exit_code"],
            "timed_out": result["timed_out"],
            "output_path": f"steps/{step_num}_docs_check_stdout.txt",
        }
    )

    if result["timed_out"]:
        print(f"  WARNING: Step {step_num} timed out")
        return ""
    elif result["exit_code"] != 0:
        print(f"  WARNING: Step {step_num} exited with code {result['exit_code']}")
        return ""

    print(f"  Completed in {result['wall_clock_ms'] / 1000:.1f}s")
    return output


# --- Helpers ---


def _count_issues(output: str) -> int:
    """Extract the issue count from LLM output.

    Looks for "TOTAL: N issues" or counts ISSUE-N headers.

    Args:
        output: The stdout from an LLM review step.

    Returns:
        The number of issues found, or 0 if not parseable.
    """
    import re

    # Try TOTAL line first
    match: re.Match[str] | None = re.search(
        r"TOTAL:\s*(\d+)\s*(?:new\s+)?issues?", output, re.IGNORECASE
    )
    if match:
        return int(match.group(1))

    # Fall back to counting ISSUE-N headers
    issue_matches: list[str] = re.findall(r"###\s*ISSUE-\d+", output)
    return len(issue_matches)


def _finalize_run(
    run_dir: str,
    run_id: str,
    worktree_name: str,
    repo_path: str,
    base_branch: str,
    sha_start: str,
    started_at: str,
    pipeline_start: float,
    final_status: str,
    iterations: int,
    issues_found: int,
    issues_fixed: int,
    issues_remaining: int,
    steps_log: list[dict[str, Any]],
    metrics_summary: dict[str, int],
) -> dict[str, Any]:
    """Build and write the final run.json, print summary, return result dict."""
    sha_end: str = get_current_sha(repo_path)
    completed_at: str = datetime.now(timezone.utc).isoformat()
    total_wall_clock_ms: int = int((time.monotonic() - pipeline_start) * 1000)

    run_data: dict[str, Any] = {
        "run_id": run_id,
        "type": "review",
        "worktree": worktree_name,
        "repo_path": repo_path,
        "base_branch": base_branch,
        "git_sha_start": sha_start,
        "git_sha_end": sha_end,
        "started_at": started_at,
        "completed_at": completed_at,
        "final_status": final_status,
        "iterations": iterations,
        "total_wall_clock_ms": total_wall_clock_ms,
        "issues_found": issues_found,
        "issues_fixed": issues_fixed,
        "issues_remaining": issues_remaining,
        "steps": steps_log,
        "metrics_summary": metrics_summary,
    }

    write_run_json(run_dir, run_data)

    # Print summary
    print(f"\n{'=' * 60}")
    print("  Review Pipeline Summary")
    print(f"{'=' * 60}")
    print(f"  Status:           {final_status}")
    print(f"  Iterations:       {iterations}")
    print(f"  Issues found:     {issues_found}")
    print(f"  Issues fixed:     {issues_fixed}")
    print(f"  Issues remaining: {issues_remaining}")
    print(f"  Total time:       {total_wall_clock_ms / 1000:.1f}s")
    print(f"  Run log:          {run_dir}/run.json")
    print(f"{'=' * 60}\n")

    return run_data
