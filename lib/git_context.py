#!/usr/bin/env python3
"""Git context utilities for the Dark Factory pipeline.

Computes diffs, changed files, worktree detection, and branch identification.
All functions take repo_path as first argument and run git commands in that directory.
Pure functions — no side effects, no writing to disk.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


def parse_pr_ref(value: str) -> tuple[str, int]:
    """Parse a PR reference into (repo_slug, pr_number).

    Accepts formats:
        - owner/repo#123
        - https://github.com/owner/repo/pull/123

    Args:
        value: The PR reference string.

    Returns:
        Tuple of (repo_slug, pr_number) where repo_slug is "owner/repo".

    Raises:
        ValueError: If the value does not match any expected format.
    """
    # Try URL format: https://github.com/owner/repo/pull/123
    url_match: re.Match[str] | None = re.match(
        r"https?://github\.com/([^/]+/[^/]+)/pull/(\d+)", value
    )
    if url_match:
        return url_match.group(1), int(url_match.group(2))

    # Try shorthand format: owner/repo#123
    short_match: re.Match[str] | None = re.match(r"([^/]+/[^#]+)#(\d+)", value)
    if short_match:
        return short_match.group(1), int(short_match.group(2))

    raise ValueError(
        f"Invalid PR reference: {value!r}. "
        f"Expected owner/repo#123 or https://github.com/owner/repo/pull/123"
    )


def list_pr_changed_files(repo: str, pr_number: int) -> list[str]:
    """List files changed in a GitHub PR using the gh CLI.

    Args:
        repo: Repository slug (e.g. "owner/repo").
        pr_number: The PR number.

    Returns:
        List of file paths changed in the PR.
    """
    result: subprocess.CompletedProcess[str] = subprocess.run(
        [
            "gh",
            "pr",
            "view",
            str(pr_number),
            "--repo",
            repo,
            "--json",
            "files",
            "--jq",
            ".files[].path",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"Warning: gh pr view failed: {result.stderr.strip()}")
        return []
    files: list[str] = [f for f in result.stdout.strip().split("\n") if f]
    return files


def compute_diff(
    repo_path: str,
    base_branch: str,
    scope: str | None = None,
    files: list[str] | None = None,
) -> str:
    """Compute three-dot diff between base branch and HEAD.

    Args:
        repo_path: Absolute path to the git repository.
        base_branch: Base branch for the diff (e.g. "origin/main").
        scope: Optional subdirectory to scope the diff to (e.g. "backend/").
        files: Optional list of file paths to scope the diff to. Takes
            precedence over scope if both are provided.

    Returns:
        The diff output as a string. Empty string if no diff or on error.
    """
    cmd: list[str] = ["git", "diff", f"{base_branch}...HEAD"]
    if files:
        cmd.extend(["--"] + files)
    elif scope:
        cmd.extend(["--", scope])
    result: subprocess.CompletedProcess[str] = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=repo_path,
    )
    if result.returncode != 0:
        print(f"Warning: git diff failed: {result.stderr.strip()}")
        return ""
    return result.stdout


def list_changed_files(
    repo_path: str,
    base_branch: str,
    scope: str | None = None,
    files: list[str] | None = None,
) -> list[str]:
    """List files changed between base branch and HEAD.

    Args:
        repo_path: Absolute path to the git repository.
        base_branch: Base branch for the diff (e.g. "origin/main").
        scope: Optional subdirectory to scope the file list to (e.g. "backend/").
        files: Optional list of file paths to scope the file list to. Takes
            precedence over scope if both are provided.

    Returns:
        List of file paths relative to the repo root.
    """
    cmd: list[str] = ["git", "diff", "--name-only", f"{base_branch}...HEAD"]
    if files:
        cmd.extend(["--"] + files)
    elif scope:
        cmd.extend(["--", scope])
    result: subprocess.CompletedProcess[str] = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=repo_path,
    )
    if result.returncode != 0:
        print(f"Warning: git diff --name-only failed: {result.stderr.strip()}")
        return []
    files: list[str] = [f for f in result.stdout.strip().split("\n") if f]
    return files


def detect_worktree_name(repo_path: str) -> str:
    """Detect if running in a git worktree and return its name.

    Priority:
        1. If repo_path is a git worktree, return the worktree directory name.
        2. If not a worktree, return the branch name.
        3. Fallback: repo directory name.

    Args:
        repo_path: Absolute path to the git repository.

    Returns:
        A string identifier for the worktree/branch.
    """
    repo: Path = Path(repo_path)
    git_path: Path = repo / ".git"

    # If .git is a file (not a directory), this is a worktree
    if git_path.is_file():
        return repo.name

    # Check git worktree list to see if we're in a linked worktree
    result: subprocess.CompletedProcess[str] = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        capture_output=True,
        text=True,
        cwd=repo_path,
    )
    if result.returncode == 0:
        worktree_count: int = result.stdout.count("worktree ")
        if worktree_count > 1:
            # We might be in a linked worktree — check if repo_path
            # matches a non-main worktree
            lines: list[str] = result.stdout.strip().split("\n")
            main_worktree: str = ""
            for line in lines:
                if line.startswith("worktree "):
                    wt_path: str = line.split("worktree ", 1)[1]
                    if not main_worktree:
                        main_worktree = wt_path
                    resolved_repo: str = str(repo.resolve())
                    if wt_path == resolved_repo and wt_path != main_worktree:
                        return repo.name

    # Not a worktree — return branch name
    branch: str = get_branch_name(repo_path)
    if branch:
        return branch

    # Fallback: repo directory name
    return repo.name


def get_branch_name(repo_path: str) -> str:
    """Get the current branch name.

    Args:
        repo_path: Absolute path to the git repository.

    Returns:
        The current branch name, or empty string on error.
    """
    result: subprocess.CompletedProcess[str] = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        cwd=repo_path,
    )
    if result.returncode != 0:
        print(f"Warning: git rev-parse failed: {result.stderr.strip()}")
        return ""
    return result.stdout.strip()


def get_current_sha(repo_path: str) -> str:
    """Get the current commit SHA.

    Args:
        repo_path: Absolute path to the git repository.

    Returns:
        The full commit SHA, or empty string on error.
    """
    result: subprocess.CompletedProcess[str] = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        cwd=repo_path,
    )
    if result.returncode != 0:
        print(f"Warning: git rev-parse HEAD failed: {result.stderr.strip()}")
        return ""
    return result.stdout.strip()
