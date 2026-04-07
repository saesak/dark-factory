#!/usr/bin/env python3
"""Dark Factory CLI — entry point for the code quality pipeline.

Usage:
    python ~/.dark-factory/cli.py review [OPTIONS]
    python ~/.dark-factory/cli.py plan [OPTIONS]     # stub
    python ~/.dark-factory/cli.py implement [OPTIONS] # stub
    python ~/.dark-factory/cli.py judge [OPTIONS]     # stub
    python ~/.dark-factory/cli.py full [OPTIONS]      # stub

The review subcommand runs the autonomous review pipeline.
See spec.md for full documentation.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    print("Error: PyYAML is required. Install with: pip install pyyaml")
    sys.exit(1)

# Add dark-factory root to path for imports
_DARK_FACTORY_DIR: Path = Path(__file__).resolve().parent
if str(_DARK_FACTORY_DIR) not in sys.path:
    sys.path.insert(0, str(_DARK_FACTORY_DIR))

CONFIG_PATH: Path = _DARK_FACTORY_DIR / "config.yaml"

# Default configuration values (used when config.yaml is missing or incomplete)
DEFAULTS: dict[str, Any] = {
    "base_branch": "origin/main",
    "max_iterations": 2,
    "metrics": {
        "cyclomatic_complexity_threshold": 10,
        "function_length_threshold": 50,
        "file_length_threshold": 500,
        "coverage_delta_minimum": 80,
        "duplication_min_tokens": 50,
    },
    "timeouts": {
        "review_emit": 600000,
        "coherence": 300000,
        "fix": 600000,
        "metrics_fix": 300000,
        "verify": 600000,
        "simplify": 600000,
        "update_docs": 300000,
        "test": 600000,
    },
}


def load_config() -> dict[str, Any]:
    """Load configuration from config.yaml, falling back to defaults.

    Returns:
        Merged configuration dictionary.
    """
    config: dict[str, Any] = dict(DEFAULTS)

    if CONFIG_PATH.exists():
        try:
            raw: str = CONFIG_PATH.read_text(encoding="utf-8")
            file_config: dict[str, Any] | None = yaml.safe_load(raw)
            if file_config and isinstance(file_config, dict):
                config = _deep_merge(config, file_config)
        except yaml.YAMLError as e:
            print(f"Warning: failed to parse config.yaml: {e}")
            print("Using default configuration.")

    return config


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge override into base. Override values take precedence.

    Args:
        base: The base dictionary (defaults).
        override: The override dictionary (from file or CLI).

    Returns:
        A new merged dictionary.
    """
    result: dict = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with all subcommands.

    Returns:
        Configured ArgumentParser.
    """
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        prog="dark-factory",
        description="Dark Factory — autonomous code quality pipeline",
    )
    subparsers = parser.add_subparsers(dest="command", help="Pipeline stage")

    # --- review subcommand ---
    review_parser: argparse.ArgumentParser = subparsers.add_parser(
        "review",
        help="Run the autonomous review pipeline",
    )
    review_parser.add_argument(
        "--base",
        type=str,
        default=None,
        help="Base branch for diff (default: from config.yaml)",
    )
    review_parser.add_argument(
        "--pr",
        type=str,
        default=None,
        help="GitHub PR reference (owner/repo#123 or full URL). Mutually exclusive with --base.",
    )
    review_parser.add_argument(
        "--name",
        type=str,
        default=None,
        help="Override worktree/PR identifier for run directory",
    )
    review_parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="Max review-fix loops (default: from config.yaml)",
    )
    review_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Emit issues only, don't apply fixes",
    )
    review_parser.add_argument(
        "--metrics-only",
        action="store_true",
        help="Skip LLM review, only run deterministic metrics",
    )
    review_parser.add_argument(
        "--no-metrics",
        action="store_true",
        help="Skip deterministic metrics, only run LLM review",
    )
    review_parser.add_argument(
        "--no-simplify",
        action="store_true",
        help="Skip the code simplification step after review",
    )
    review_parser.add_argument(
        "--no-docs",
        action="store_true",
        help="Skip the documentation update step after review",
    )
    review_parser.add_argument(
        "--repo-path",
        type=str,
        default=None,
        help="Path to the repository (default: current working directory)",
    )
    review_parser.add_argument(
        "--scope",
        type=str,
        default=None,
        help=(
            "Subdirectory to scope the review to (e.g., backend/). "
            "When set, only files under this path are reviewed, and git diffs "
            "are scoped to this subdirectory. Useful for monorepo subdirectories."
        ),
    )
    review_parser.add_argument(
        "--files",
        type=str,
        default=None,
        help="Comma-separated list of files to scope the review to",
    )
    review_parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Model override for claude -p (e.g. opus, sonnet, haiku)",
    )

    # --- stub subcommands ---
    subparsers.add_parser("plan", help="Planner agent (not yet implemented)")
    subparsers.add_parser("implement", help="Implementer agent (not yet implemented)")
    subparsers.add_parser("judge", help="Judge agent (not yet implemented)")
    subparsers.add_parser("full", help="Full pipeline chain (not yet implemented)")

    return parser


def cmd_review(args: argparse.Namespace, config: dict[str, Any]) -> None:
    """Handle the review subcommand.

    Args:
        args: Parsed CLI arguments.
        config: Loaded configuration dictionary.
    """
    # Validate conflicting flags
    if args.pr is not None and args.base is not None:
        print("Error: --pr and --base are mutually exclusive")
        sys.exit(1)
    if args.files is not None and args.scope is not None:
        print("Error: --files and --scope are mutually exclusive")
        sys.exit(1)
    if args.files is not None and args.pr is not None:
        print("Error: --files and --pr are mutually exclusive")
        sys.exit(1)
    if args.metrics_only and args.no_metrics:
        print("Error: --metrics-only and --no-metrics are mutually exclusive")
        sys.exit(1)
    if args.metrics_only and args.dry_run:
        print("Error: --metrics-only and --dry-run are mutually exclusive")
        sys.exit(1)

    # Handle --pr flag
    if args.pr is not None:
        from lib.git_context import parse_pr_ref

        repo_slug: str
        pr_number: int
        try:
            repo_slug, pr_number = parse_pr_ref(args.pr)
        except ValueError as e:
            print(f"Error: {e}")
            sys.exit(1)
        config["pr"] = {"repo": repo_slug, "number": pr_number}
        # Default name: owner-repo-PR-123
        if args.name is None:
            config["name"] = f"{repo_slug.replace('/', '-')}-PR-{pr_number}"

    # CLI overrides take precedence over config.yaml
    if args.base is not None:
        config["base_branch"] = args.base
    if args.max_iterations is not None:
        config["max_iterations"] = args.max_iterations
    if args.name is not None:
        config["name"] = args.name
    if args.repo_path is not None:
        config["repo_path"] = str(Path(args.repo_path).resolve())
    else:
        config["repo_path"] = str(Path.cwd())

    config["dry_run"] = args.dry_run
    config["metrics_only"] = args.metrics_only
    config["no_metrics"] = args.no_metrics
    config["no_simplify"] = args.no_simplify
    config["no_docs"] = args.no_docs
    config["scope"] = args.scope
    config["files"] = args.files.split(",") if args.files else None
    config["model"] = args.model

    from stages.review import run

    result: dict[str, Any] = run(config)

    # Exit with non-zero if pipeline did not pass
    status: str = result.get("final_status", "error")
    if status != "pass":
        sys.exit(1)


def cmd_stub(command_name: str) -> None:
    """Handle stub subcommands that are not yet implemented.

    Args:
        command_name: Name of the subcommand.
    """
    print(f"'{command_name}' is not implemented yet. See spec.md for planned design.")


def main() -> None:
    """Entry point for the Dark Factory CLI."""
    parser: argparse.ArgumentParser = build_parser()
    args: argparse.Namespace = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    config: dict[str, Any] = load_config()

    if args.command == "review":
        cmd_review(args, config)
    elif args.command in ("plan", "implement", "judge", "full"):
        cmd_stub(args.command)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
