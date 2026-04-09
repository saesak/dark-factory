#!/usr/bin/env python3
"""Project documentation context discovery for the Dark Factory pipeline.

Scans a repository for documentation artifacts (invariants, conventions, review
checklists, frontmatter-linked docs) and identifies stale docs whose source files
changed but the doc itself was not updated. Used by review stages to inject
project-specific context into LLM prompts.
"""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Any

import yaml


# Directories to skip when walking the repo for .md files
_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        ".data",
        ".dark-factory",
        ".claude",
    }
)

_DOC_INDEX_CAP: int = 500


def parse_frontmatter(path: str) -> dict[str, Any]:
    """Parse YAML frontmatter from a markdown file.

    Reads the file and checks if the first line is ``---``. If so, reads lines
    until the closing ``---`` and parses the block as YAML.

    Args:
        path: Absolute or relative path to the markdown file.

    Returns:
        Parsed frontmatter dict, or empty dict if no frontmatter or on any error.
    """
    try:
        text: str = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}

    lines: list[str] = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}

    end_index: int | None = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_index = i
            break

    if end_index is None:
        return {}

    yaml_block: str = "\n".join(lines[1:end_index])
    try:
        parsed: Any = yaml.safe_load(yaml_block)
    except yaml.YAMLError:
        return {}

    if not isinstance(parsed, dict):
        return {}

    return parsed


def match_source_files(source_files: list[str], changed_files: list[str]) -> bool:
    """Check if any source file pattern matches any changed file.

    For each source pattern, tries three matching strategies against each
    changed file:
        1. Exact match (after stripping ``:symbol`` suffix from source)
        2. Prefix match (source ``scripts/`` matches ``scripts/foo.py``)
        3. Glob match via ``fnmatch`` (source ``docs/agents/*/config.json``
           matches ``docs/agents/thermal/config.json``)

    Args:
        source_files: List of source file patterns from frontmatter.
        changed_files: List of changed file paths in the PR.

    Returns:
        True if any source pattern matches any changed file.
    """
    for source_raw in source_files:
        # Strip :symbol suffix (e.g. "models/annotations.py:VALID_TRANSITIONS")
        source: str = source_raw.split(":")[0] if ":" in source_raw else source_raw

        for changed in changed_files:
            # Exact match
            if source == changed:
                return True

            # Prefix match (source "scripts/" matches "scripts/foo.py")
            if changed.startswith(source):
                return True

            # Glob match
            if fnmatch.fnmatch(changed, source):
                return True

    return False


def _read_file_safe(path: str) -> str:
    """Read a file and return its content, or empty string on any error.

    Args:
        path: Absolute path to the file.

    Returns:
        File content as string, or empty string if the file is missing or unreadable.
    """
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _concat_dir_md(dir_path: str) -> str:
    """Concatenate all .md files in a directory, sorted by name.

    Args:
        dir_path: Absolute path to the directory.

    Returns:
        Concatenated content of all .md files, separated by newlines.
        Empty string if directory is missing or contains no .md files.
    """
    dirp: Path = Path(dir_path)
    if not dirp.is_dir():
        return ""

    parts: list[str] = []
    md_files: list[Path] = sorted(dirp.glob("*.md"))
    for md_file in md_files:
        try:
            content: str = md_file.read_text(encoding="utf-8")
            parts.append(content)
        except (OSError, UnicodeDecodeError):
            continue

    return "\n".join(parts)


def _extract_heading(path: str) -> str:
    """Extract the first ``# heading`` from the first 10 lines of a file.

    Args:
        path: Absolute path to the markdown file.

    Returns:
        The heading text (without ``# `` prefix), or empty string if none found.
    """
    try:
        with open(path, encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i >= 10:
                    break
                stripped: str = line.strip()
                if stripped.startswith("# ") and not stripped.startswith("##"):
                    return stripped[2:].strip()
    except (OSError, UnicodeDecodeError):
        pass
    return ""


def _build_doc_index(repo_path: str) -> list[dict[str, str]]:
    """Walk the repo and build an index of all .md files with their headings.

    Skips directories in ``_SKIP_DIRS``. Caps output at ``_DOC_INDEX_CAP`` entries.

    Args:
        repo_path: Absolute path to the repository root.

    Returns:
        List of dicts with ``path`` (relative to repo) and ``title`` keys.
    """
    index: list[dict[str, str]] = []
    repo: Path = Path(repo_path)

    for dirpath, dirnames, filenames in os.walk(repo_path):
        # Prune skipped directories in-place
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]

        for filename in sorted(filenames):
            if not filename.endswith(".md"):
                continue

            abs_path: str = os.path.join(dirpath, filename)
            rel_path: str = str(Path(abs_path).relative_to(repo))
            title: str = _extract_heading(abs_path)

            index.append({"path": rel_path, "title": title})

            if len(index) >= _DOC_INDEX_CAP:
                return index

    return index


def _find_stale_docs(repo_path: str, changed_files: list[str]) -> list[dict[str, Any]]:
    """Find docs whose source_files overlap with changed_files but weren't updated.

    Scans:
        - ``docs/invariants/*.md``
        - ``docs/conventions/*.md``
        - ``.claude/rules/*.md``
        - Any other ``.md`` files under ``docs/`` (recursively)

    For each file, parses frontmatter and checks if the ``source_files`` key
    exists and overlaps with ``changed_files``. If the doc itself was NOT
    changed (not in ``changed_files``), it is considered stale.

    Args:
        repo_path: Absolute path to the repository root.
        changed_files: List of changed file paths in the PR.

    Returns:
        List of dicts with ``path`` (relative) and ``source_files`` (list[str]).
    """
    repo: Path = Path(repo_path)
    stale: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    changed_set: set[str] = set(changed_files)

    # Directories to scan for frontmatter-linked docs
    scan_dirs: list[Path] = [
        repo / "docs" / "invariants",
        repo / "docs" / "conventions",
        repo / ".claude" / "rules",
    ]

    # Also scan docs/ recursively
    docs_dir: Path = repo / "docs"

    # Collect candidate .md files
    candidates: list[Path] = []

    for scan_dir in scan_dirs:
        if scan_dir.is_dir():
            candidates.extend(sorted(scan_dir.glob("*.md")))

    # Recursively scan docs/ for additional .md files
    if docs_dir.is_dir():
        for dirpath, dirnames, filenames in os.walk(str(docs_dir)):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for filename in sorted(filenames):
                if filename.endswith(".md"):
                    candidates.append(Path(dirpath) / filename)

    for candidate in candidates:
        if not candidate.is_file():
            continue

        rel_path: str = str(candidate.relative_to(repo))
        if rel_path in seen_paths:
            continue
        seen_paths.add(rel_path)

        # Skip docs that were themselves changed in the PR
        if rel_path in changed_set:
            continue

        frontmatter: dict[str, Any] = parse_frontmatter(str(candidate))
        source_files_val: Any = frontmatter.get("source_files")

        if not isinstance(source_files_val, list) or not source_files_val:
            continue

        source_files_list: list[str] = [str(s) for s in source_files_val]

        if match_source_files(source_files_list, changed_files):
            stale.append({"path": rel_path, "source_files": source_files_list})

    return stale


def discover_project_docs(repo_path: str, changed_files: list[str]) -> dict[str, Any]:
    """Discover project documentation context for a repository.

    Scans the repo for documentation artifacts and returns a structured dict
    with all relevant context for review prompts.

    Args:
        repo_path: Absolute path to the repository root.
        changed_files: List of file paths changed in the PR (relative to repo root).

    Returns:
        Dict with keys:
            - invariants: Concatenated docs/invariants/*.md content
            - conventions: Concatenated docs/conventions/*.md content
            - doc_guidelines: Content of docs/DOCUMENTATION.md
            - checklist: Content of docs/review-checklist.md
            - stale_docs: List of stale docs (path + source_files)
            - doc_index: List of all .md files (path + title), capped at 500
            - changed_md_files: Subset of changed_files ending with .md
            - has_invariants: Whether docs/invariants/ exists and has content
            - has_conventions: Whether docs/conventions/ exists and has content
            - has_doc_guidelines: Whether docs/DOCUMENTATION.md exists
    """
    repo: Path = Path(repo_path)

    invariants: str = _concat_dir_md(str(repo / "docs" / "invariants"))
    conventions: str = _concat_dir_md(str(repo / "docs" / "conventions"))
    doc_guidelines: str = _read_file_safe(str(repo / "docs" / "DOCUMENTATION.md"))
    checklist: str = _read_file_safe(str(repo / "docs" / "review-checklist.md"))

    stale_docs: list[dict[str, Any]] = _find_stale_docs(repo_path, changed_files)
    doc_index: list[dict[str, str]] = _build_doc_index(repo_path)

    changed_md_files: list[str] = [f for f in changed_files if f.endswith(".md")]

    return {
        "invariants": invariants,
        "conventions": conventions,
        "doc_guidelines": doc_guidelines,
        "checklist": checklist,
        "stale_docs": stale_docs,
        "doc_index": doc_index,
        "changed_md_files": changed_md_files,
        "has_invariants": bool(invariants),
        "has_conventions": bool(conventions),
        "has_doc_guidelines": bool(doc_guidelines),
    }
