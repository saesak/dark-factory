#!/usr/bin/env python3
"""Deterministic metric tools for the Dark Factory pipeline.

Runs radon, diff-cover, jscpd, and ruff on changed files. Returns structured
JSON with a violations array and summary counts. No LLM calls happen here —
everything is computed from code using established static analysis tools.

Each tool runner checks if the tool is installed before running. If a tool
is missing, it logs a warning and skips — it does not fail the pipeline.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


def run_metrics(changed_files: list[str], config: dict) -> dict:
    """Run all metric tools on the changed files and return structured results.

    Args:
        changed_files: List of file paths relative to the repo root.
        config: Parsed configuration dict. Expected keys under "metrics":
            - cyclomatic_complexity_threshold (int)
            - duplication_min_tokens (int)
            - coverage_delta_minimum (int)
            Also expects "base_branch" and "repo_path" at top level.
            Optional: "scope" (str | None) — subdirectory scope for monorepo support.

    Returns:
        A dict with "violations" (list of violation dicts) and "summary"
        (counts by category).
    """
    metrics_config: dict = config.get("metrics", {})
    repo_path: str = config.get("repo_path", ".")
    scope: str | None = config.get("scope")

    complexity_threshold: int = metrics_config.get(
        "cyclomatic_complexity_threshold", 10
    )
    duplication_min_tokens: int = metrics_config.get("duplication_min_tokens", 50)
    coverage_minimum: int = metrics_config.get("coverage_delta_minimum", 80)
    base_branch: str = config.get("base_branch", "origin/main")

    # Filter to Python files for Python-specific tools
    py_files: list[str] = [f for f in changed_files if f.endswith(".py")]

    # Resolve to absolute paths for tool invocation
    py_abs: list[str] = [
        str(Path(repo_path) / f) for f in py_files if (Path(repo_path) / f).exists()
    ]
    all_abs: list[str] = [
        str(Path(repo_path) / f)
        for f in changed_files
        if (Path(repo_path) / f).exists()
    ]

    all_violations: list[dict] = []

    # Run each metric tool
    radon_violations: list[dict] = _run_radon(py_abs, complexity_threshold)
    all_violations.extend(radon_violations)

    coverage_violations: list[dict] = _run_diff_cover(
        base_branch, coverage_minimum, repo_path, scope
    )
    all_violations.extend(coverage_violations)

    duplication_violations: list[dict] = _run_jscpd(
        all_abs, duplication_min_tokens, repo_path
    )
    all_violations.extend(duplication_violations)

    lint_violations: list[dict] = _run_ruff(py_abs)
    all_violations.extend(lint_violations)

    # Build summary
    summary: dict[str, int] = {
        "complexity_violations": len(radon_violations),
        "coverage_violations": len(coverage_violations),
        "duplication_violations": len(duplication_violations),
        "lint_violations": len(lint_violations),
        "total_violations": len(all_violations),
    }

    return {
        "violations": all_violations,
        "summary": summary,
    }


def _run_radon(files: list[str], threshold: int) -> list[dict]:
    """Run radon cc on Python files and filter by complexity threshold.

    Args:
        files: Absolute paths to Python files.
        threshold: Maximum allowed cyclomatic complexity.

    Returns:
        List of violation dicts for functions exceeding the threshold.
    """
    if not files:
        return []

    if not shutil.which("radon"):
        print("Warning: radon not installed, skipping complexity check")
        return []

    violations: list[dict] = []

    for filepath in files:
        try:
            result: subprocess.CompletedProcess[str] = subprocess.run(
                ["radon", "cc", "-j", filepath],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode != 0:
                print(f"Warning: radon failed on {filepath}: {result.stderr.strip()}")
                continue

            data: dict[str, Any] = json.loads(result.stdout)

            for file_path, functions in data.items():
                for func in functions:
                    complexity: int = func.get("complexity", 0)
                    if complexity > threshold:
                        violations.append(
                            {
                                "metric": "cyclomatic_complexity",
                                "file": file_path,
                                "line": func.get("lineno", 0),
                                "function": func.get("name", "unknown"),
                                "value": complexity,
                                "threshold": threshold,
                                "tool": "radon",
                                "detail": (
                                    f"Function {func.get('name', 'unknown')} "
                                    f"has complexity {complexity} "
                                    f"(threshold: {threshold})"
                                ),
                            }
                        )
        except subprocess.TimeoutExpired:
            print(f"Warning: radon timed out on {filepath}")
        except (json.JSONDecodeError, KeyError) as e:
            print(f"Warning: failed to parse radon output for {filepath}: {e}")

    return violations


def _run_diff_cover(
    base_branch: str,
    threshold: int,
    repo_path: str,
    scope: str | None = None,
) -> list[dict]:
    """Run diff-cover against coverage.xml if it exists.

    Args:
        base_branch: Base branch for the diff.
        threshold: Minimum coverage percentage for changed lines.
        repo_path: Absolute path to the repository root.
        scope: Optional subdirectory scope. When set, checks for coverage.xml
            in {repo_path}/{scope}/ first, then falls back to {repo_path}/.

    Returns:
        List of violation dicts for files below the coverage threshold.
    """
    if not shutil.which("diff-cover"):
        print("Warning: diff-cover not installed, skipping coverage check")
        return []

    # When scoped, check scope dir first, then fall back to repo root
    coverage_xml: Path | None = None
    if scope:
        scoped_path: Path = Path(repo_path) / scope / "coverage.xml"
        if scoped_path.exists():
            coverage_xml = scoped_path
    if coverage_xml is None:
        root_path: Path = Path(repo_path) / "coverage.xml"
        if root_path.exists():
            coverage_xml = root_path

    if coverage_xml is None:
        print(
            "Warning: coverage.xml not found, skipping diff-cover "
            "(run tests with coverage first)"
        )
        return []

    violations: list[dict] = []

    try:
        result: subprocess.CompletedProcess[str] = subprocess.run(
            [
                "diff-cover",
                str(coverage_xml),
                f"--compare-branch={base_branch}",
                "--json-report",
                "-",
            ],
            capture_output=True,
            text=True,
            cwd=repo_path,
            timeout=120,
        )

        if result.returncode != 0 and not result.stdout:
            print(f"Warning: diff-cover failed: {result.stderr.strip()}")
            return []

        # Parse JSON output
        data: dict[str, Any] = json.loads(result.stdout)
        src_stats: dict[str, Any] = data.get("src_stats", {})

        for filepath, stats in src_stats.items():
            covered: int = stats.get("covered_lines", 0)
            total: int = stats.get("violation_lines", 0) + covered
            if total > 0:
                pct: float = (covered / total) * 100
                if pct < threshold:
                    violations.append(
                        {
                            "metric": "coverage_delta",
                            "file": filepath,
                            "line": 0,
                            "function": "",
                            "value": round(pct, 1),
                            "threshold": threshold,
                            "tool": "diff-cover",
                            "detail": (
                                f"File {filepath} has {pct:.1f}% coverage "
                                f"on changed lines (threshold: {threshold}%)"
                            ),
                        }
                    )
    except subprocess.TimeoutExpired:
        print("Warning: diff-cover timed out")
    except (json.JSONDecodeError, KeyError) as e:
        print(f"Warning: failed to parse diff-cover output: {e}")

    return violations


def _run_jscpd(files: list[str], min_tokens: int, repo_path: str) -> list[dict]:
    """Run jscpd for syntactic duplication detection.

    Args:
        files: Absolute paths to files to check.
        min_tokens: Minimum token count for jscpd to flag duplication.
        repo_path: Absolute path to the repository root.

    Returns:
        List of violation dicts for detected duplications.
    """
    if not files:
        return []

    if not shutil.which("jscpd"):
        print("Warning: jscpd not installed, skipping duplication check")
        return []

    violations: list[dict] = []

    try:
        # jscpd works best on directories; pass the repo path and filter
        subprocess.run(
            [
                "jscpd",
                "--min-tokens",
                str(min_tokens),
                "--reporters",
                "json",
                "--output",
                "/tmp/jscpd-report",
                *files,
            ],
            capture_output=True,
            text=True,
            cwd=repo_path,
            timeout=120,
        )

        # jscpd exits with 0 even when duplicates found; check the JSON report
        report_path: Path = Path("/tmp/jscpd-report/jscpd-report.json")
        if not report_path.exists():
            return []

        report_text: str = report_path.read_text(encoding="utf-8")
        data: dict[str, Any] = json.loads(report_text)

        duplicates: list[dict] = data.get("duplicates", [])
        for dup in duplicates:
            first: dict = dup.get("firstFile", {})
            second: dict = dup.get("secondFile", {})
            violations.append(
                {
                    "metric": "duplication",
                    "file": first.get("name", "unknown"),
                    "line": first.get("startLoc", {}).get("line", 0),
                    "function": "",
                    "value": dup.get("lines", 0),
                    "threshold": min_tokens,
                    "tool": "jscpd",
                    "detail": (
                        f"Duplication detected between "
                        f"{first.get('name', '?')}:{first.get('startLoc', {}).get('line', '?')}"
                        f"-{first.get('endLoc', {}).get('line', '?')}"
                        f" and "
                        f"{second.get('name', '?')}:{second.get('startLoc', {}).get('line', '?')}"
                        f"-{second.get('endLoc', {}).get('line', '?')}"
                    ),
                }
            )
    except subprocess.TimeoutExpired:
        print("Warning: jscpd timed out")
    except (json.JSONDecodeError, KeyError) as e:
        print(f"Warning: failed to parse jscpd output: {e}")

    return violations


def _run_ruff(files: list[str]) -> list[dict]:
    """Run ruff check on Python files and parse violations.

    Args:
        files: Absolute paths to Python files.

    Returns:
        List of violation dicts for lint violations found.
    """
    if not files:
        return []

    if not shutil.which("ruff"):
        print("Warning: ruff not installed, skipping lint check")
        return []

    violations: list[dict] = []

    try:
        result: subprocess.CompletedProcess[str] = subprocess.run(
            ["ruff", "check", "--output-format", "json", *files],
            capture_output=True,
            text=True,
            timeout=60,
        )

        # ruff exits with 1 when violations are found — that's expected
        output: str = result.stdout
        if not output:
            return []

        data: list[dict] = json.loads(output)

        for violation in data:
            violations.append(
                {
                    "metric": "lint",
                    "file": violation.get("filename", "unknown"),
                    "line": violation.get("location", {}).get("row", 0),
                    "function": "",
                    "value": violation.get("code", ""),
                    "threshold": "n/a",
                    "tool": "ruff",
                    "detail": (
                        f"{violation.get('code', '')}: "
                        f"{violation.get('message', 'unknown violation')}"
                    ),
                }
            )
    except subprocess.TimeoutExpired:
        print("Warning: ruff timed out")
    except (json.JSONDecodeError, KeyError) as e:
        print(f"Warning: failed to parse ruff output: {e}")

    return violations
