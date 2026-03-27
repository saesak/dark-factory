#!/usr/bin/env python3
"""Claude CLI subprocess wrapper for the Dark Factory pipeline.

Every LLM agent invocation in the pipeline goes through this module.
Responsibilities:
- Build prompts from template files with {{VARIABLE}} substitution
- Call `claude -p` as a subprocess with configurable tools and timeout
- Capture stdout, stderr, exit code, and wall clock time
- Handle failures gracefully (never raises, returns structured error)
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path


def build_prompt(template_path: str, variables: dict[str, str]) -> str:
    """Read a prompt template and replace all {{VARIABLE_NAME}} placeholders.

    Args:
        template_path: Absolute path to the .md prompt template file.
        variables: Mapping of variable names (without braces) to their values.

    Returns:
        The fully assembled prompt string with all placeholders replaced.
    """
    template: str = Path(template_path).read_text(encoding="utf-8")

    for name, value in variables.items():
        placeholder: str = "{{" + name + "}}"
        template = template.replace(placeholder, value)

    # Warn about any unreplaced variables
    remaining: list[str] = re.findall(r"\{\{([A-Z_]+)\}\}", template)
    if remaining:
        print(f"Warning: unreplaced template variables: {', '.join(remaining)}")

    return template


def invoke_claude(
    prompt: str,
    allowed_tools: list[str],
    timeout_ms: int,
    working_dir: str,
    max_retries: int = 2,
    debug_file: str | None = None,
) -> dict:
    """Call claude -p as a subprocess and capture the result.

    The prompt is passed via stdin (not as a CLI argument) to handle
    arbitrarily large prompts. Uses --print to suppress interactive UI.

    On non-zero exit codes (excluding timeouts and missing CLI), retries
    up to max_retries times before returning the failure.

    Args:
        prompt: The fully assembled prompt string.
        allowed_tools: List of tool names to allow (e.g. ["Read", "Glob", "Grep"]).
        timeout_ms: Timeout in milliseconds.
        working_dir: Working directory for the subprocess (the repo being reviewed).
        max_retries: Number of retry attempts on non-zero exit code (default: 2).
        debug_file: Optional path to write Claude debug logs (plugins, settings, etc.).

    Returns:
        A dict with keys:
            - stdout: str
            - stderr: str
            - exit_code: int
            - timed_out: bool
            - wall_clock_ms: int
    """
    cmd: list[str] = ["claude", "-p", "--print"]

    if debug_file:
        cmd.extend(["--debug-file", debug_file])

    if allowed_tools:
        tools_str: str = ",".join(allowed_tools)
        cmd.extend(["--allowedTools", tools_str])

    timeout_seconds: float = timeout_ms / 1000.0

    # Unset CLAUDECODE so dark-factory works from inside Claude Code sessions
    env: dict[str, str] = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    total_start: float = time.monotonic()

    for attempt in range(1 + max_retries):
        timed_out: bool = False
        stdout: str = ""
        stderr: str = ""
        exit_code: int = -1

        try:
            result: subprocess.CompletedProcess[str] = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                cwd=working_dir,
                timeout=timeout_seconds,
                env=env,
            )
            stdout = result.stdout
            stderr = result.stderr
            exit_code = result.returncode
        except subprocess.TimeoutExpired as e:
            timed_out = True
            stdout = e.stdout if isinstance(e.stdout, str) else ""
            stderr = e.stderr if isinstance(e.stderr, str) else ""
            exit_code = -1
        except FileNotFoundError:
            stderr = "Error: 'claude' CLI not found. Is it installed and on PATH?"
            exit_code = -1
            # No point retrying if claude isn't installed
            break

        # Don't retry on success or timeout (timeout isn't transient)
        if exit_code == 0 or timed_out:
            break

        # Retry on non-zero exit code
        if attempt < max_retries:
            print(
                f"  Retry {attempt + 1}/{max_retries}: "
                f"claude -p exited with code {exit_code}, retrying..."
            )

    total_wall_clock_ms: int = int((time.monotonic() - total_start) * 1000)

    return {
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "wall_clock_ms": total_wall_clock_ms,
    }
