#!/usr/bin/env python3
"""Run directory management and structured logging for the Dark Factory pipeline.

Creates run directories, saves step outputs, and writes run.json metadata.
All run data is stored under ~/.dark-factory/runs/{worktree_name}/{seq}_{type}_{timestamp}/.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

DARK_FACTORY_DIR: Path = Path.home() / ".dark-factory"
RUNS_DIR: Path = DARK_FACTORY_DIR / "runs"


def create_run(worktree_name: str, run_type: str) -> str:
    """Create a new run directory with sequential numbering.

    Creates: ~/.dark-factory/runs/{worktree_name}/{seq}_{type}_{timestamp}/
    Also creates the steps/ subdirectory inside.

    Args:
        worktree_name: Identifier for the worktree/branch/PR.
        run_type: Type of run (e.g. "review", "plan", "implement").

    Returns:
        Absolute path to the created run directory.
    """
    worktree_dir: Path = RUNS_DIR / worktree_name
    worktree_dir.mkdir(parents=True, exist_ok=True)

    # Find the next sequential number
    seq: int = _next_sequence_number(worktree_dir)

    # Timestamp format: 2026-02-27T14-30 (no seconds, dashes not colons)
    timestamp: str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M")

    run_dir_name: str = f"{seq:03d}_{run_type}_{timestamp}"
    run_dir: Path = worktree_dir / run_dir_name
    run_dir.mkdir(parents=True, exist_ok=True)

    # Create steps subdirectory
    steps_dir: Path = run_dir / "steps"
    steps_dir.mkdir(exist_ok=True)

    return str(run_dir)


def _next_sequence_number(worktree_dir: Path) -> int:
    """Scan existing directories and return the next sequence number.

    Args:
        worktree_dir: Path to the worktree's run directory.

    Returns:
        The next sequential number (starting from 1).
    """
    max_seq: int = 0
    pattern: re.Pattern[str] = re.compile(r"^(\d{3})_")

    if worktree_dir.exists():
        for entry in worktree_dir.iterdir():
            if entry.is_dir():
                match: re.Match[str] | None = pattern.match(entry.name)
                if match:
                    seq_num: int = int(match.group(1))
                    if seq_num > max_seq:
                        max_seq = seq_num

    return max_seq + 1


def save_step_output(
    run_dir: str, step_number: int, step_name: str, output: str
) -> str:
    """Write step stdout to the steps directory.

    Args:
        run_dir: Absolute path to the run directory.
        step_number: The step number (1-8).
        step_name: Short name for the step (e.g. "review_emit").
        output: The stdout content to save.

    Returns:
        Path relative to run_dir where the output was saved.
    """
    filename: str = f"{step_number}_{step_name}_stdout.txt"
    rel_path: str = f"steps/{filename}"
    full_path: Path = Path(run_dir) / rel_path
    full_path.write_text(output, encoding="utf-8")
    return rel_path


def save_step_error(run_dir: str, step_number: int, step_name: str, error: str) -> str:
    """Write step stderr to the steps directory.

    Args:
        run_dir: Absolute path to the run directory.
        step_number: The step number (1-8).
        step_name: Short name for the step (e.g. "review_emit").
        error: The stderr content to save.

    Returns:
        Path relative to run_dir where the error was saved.
    """
    filename: str = f"{step_number}_{step_name}_stderr.txt"
    rel_path: str = f"steps/{filename}"
    full_path: Path = Path(run_dir) / rel_path
    full_path.write_text(error, encoding="utf-8")
    return rel_path


def write_run_json(run_dir: str, data: dict) -> None:
    """Write run.json metadata to the run directory.

    Args:
        run_dir: Absolute path to the run directory.
        data: The run metadata dictionary to serialize.
    """
    run_json_path: Path = Path(run_dir) / "run.json"
    run_json_path.write_text(
        json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8"
    )


def save_diff_snapshot(run_dir: str, diff: str) -> None:
    """Write the diff snapshot to diff_before.patch in the run directory.

    Args:
        run_dir: Absolute path to the run directory.
        diff: The diff content to save.
    """
    patch_path: Path = Path(run_dir) / "diff_before.patch"
    patch_path.write_text(diff, encoding="utf-8")
