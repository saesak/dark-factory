#!/usr/bin/env python3
"""Basic unit tests for the Dark Factory pipeline."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

# Add dark-factory root to path
_ROOT: Path = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from cli import DEFAULTS, _deep_merge, build_parser, load_config
from lib.git_context import parse_pr_ref
from lib.invoke import build_prompt, invoke_claude
from lib.run_logger import (
    _next_sequence_number,
    create_run,
    save_diff_snapshot,
    save_step_error,
    save_step_output,
    write_run_json,
)


# ── cli.py ──────────────────────────────────────────────────────────────


class TestDeepMerge:
    def test_flat_override(self) -> None:
        base: dict[str, Any] = {"a": 1, "b": 2}
        override: dict[str, Any] = {"b": 99}
        result: dict[str, Any] = _deep_merge(base, override)
        assert result == {"a": 1, "b": 99}

    def test_nested_merge(self) -> None:
        base: dict[str, Any] = {"metrics": {"threshold": 10, "coverage": 80}}
        override: dict[str, Any] = {"metrics": {"threshold": 15}}
        result: dict[str, Any] = _deep_merge(base, override)
        assert result == {"metrics": {"threshold": 15, "coverage": 80}}

    def test_new_key_added(self) -> None:
        base: dict[str, Any] = {"a": 1}
        override: dict[str, Any] = {"b": 2}
        result: dict[str, Any] = _deep_merge(base, override)
        assert result == {"a": 1, "b": 2}

    def test_does_not_mutate_base(self) -> None:
        base: dict[str, Any] = {"a": {"x": 1}}
        override: dict[str, Any] = {"a": {"y": 2}}
        _deep_merge(base, override)
        assert base == {"a": {"x": 1}}

    def test_empty_override(self) -> None:
        base: dict[str, Any] = {"a": 1}
        result: dict[str, Any] = _deep_merge(base, {})
        assert result == {"a": 1}

    def test_override_dict_with_scalar(self) -> None:
        base: dict[str, Any] = {"a": {"nested": True}}
        override: dict[str, Any] = {"a": "flat_now"}
        result: dict[str, Any] = _deep_merge(base, override)
        assert result == {"a": "flat_now"}


class TestLoadConfig:
    def test_returns_defaults_when_no_file(self, tmp_path: Path) -> None:
        fake_config: Path = tmp_path / "nonexistent.yaml"
        with patch("cli.CONFIG_PATH", fake_config):
            config: dict[str, Any] = load_config()
        assert config["base_branch"] == DEFAULTS["base_branch"]
        assert config["max_iterations"] == DEFAULTS["max_iterations"]

    def test_merges_file_with_defaults(self, tmp_path: Path) -> None:
        config_file: Path = tmp_path / "config.yaml"
        config_file.write_text("max_iterations: 5\n", encoding="utf-8")
        with patch("cli.CONFIG_PATH", config_file):
            config: dict[str, Any] = load_config()
        assert config["max_iterations"] == 5
        assert config["base_branch"] == DEFAULTS["base_branch"]

    def test_handles_invalid_yaml(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        config_file: Path = tmp_path / "config.yaml"
        config_file.write_text(": : bad yaml [[[", encoding="utf-8")
        with patch("cli.CONFIG_PATH", config_file):
            config: dict[str, Any] = load_config()
        assert config["base_branch"] == DEFAULTS["base_branch"]
        captured: pytest.CaptureResult[str] = capsys.readouterr()
        assert "Warning" in captured.out


class TestBuildParser:
    def test_review_subcommand_defaults(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["review"])
        assert args.command == "review"
        assert args.base is None
        assert args.dry_run is False
        assert args.metrics_only is False

    def test_review_with_all_flags(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "review",
                "--base",
                "origin/develop",
                "--name",
                "test-run",
                "--max-iterations",
                "3",
                "--dry-run",
                "--no-metrics",
                "--no-simplify",
                "--repo-path",
                "/tmp/repo",
                "--scope",
                "backend/",
            ]
        )
        assert args.base == "origin/develop"
        assert args.name == "test-run"
        assert args.max_iterations == 3
        assert args.dry_run is True
        assert args.no_metrics is True
        assert args.no_simplify is True
        assert args.repo_path == "/tmp/repo"
        assert args.scope == "backend/"

    def test_stub_subcommands(self) -> None:
        parser = build_parser()
        for cmd in ("plan", "implement", "judge", "full"):
            args = parser.parse_args([cmd])
            assert args.command == cmd


# ── lib/invoke.py ───────────────────────────────────────────────────────


class TestBuildPrompt:
    def test_replaces_variables(self, tmp_path: Path) -> None:
        template: Path = tmp_path / "template.md"
        template.write_text("Review {{DIFF}} in {{REPO}}", encoding="utf-8")
        result: str = build_prompt(
            str(template), {"DIFF": "my-diff", "REPO": "my-repo"}
        )
        assert result == "Review my-diff in my-repo"

    def test_unreplaced_variables_warn(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        template: Path = tmp_path / "template.md"
        template.write_text("Hello {{NAME}} and {{MISSING}}", encoding="utf-8")
        result: str = build_prompt(str(template), {"NAME": "world"})
        assert "world" in result
        assert "{{MISSING}}" in result
        captured: pytest.CaptureResult[str] = capsys.readouterr()
        assert "MISSING" in captured.out

    def test_no_variables(self, tmp_path: Path) -> None:
        template: Path = tmp_path / "template.md"
        template.write_text("Plain text, no vars", encoding="utf-8")
        result: str = build_prompt(str(template), {})
        assert result == "Plain text, no vars"

    def test_duplicate_variable(self, tmp_path: Path) -> None:
        template: Path = tmp_path / "template.md"
        template.write_text("{{X}} and {{X}}", encoding="utf-8")
        result: str = build_prompt(str(template), {"X": "val"})
        assert result == "val and val"


# ── lib/invoke.py — invoke_claude ─────────────────────────────────────


class TestInvokeClaude:
    def _mock_run_success(
        self, *args: Any, **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args[0], returncode=0, stdout="ok", stderr=""
        )

    def _mock_run_fail(
        self, *args: Any, **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args[0], returncode=1, stdout="error output", stderr="bad"
        )

    def test_success_no_retry(self) -> None:
        with patch(
            "lib.invoke.subprocess.run", side_effect=self._mock_run_success
        ) as mock:
            result: dict[str, Any] = invoke_claude("prompt", ["Read"], 60000, "/tmp")
        assert result["exit_code"] == 0
        assert result["stdout"] == "ok"
        assert mock.call_count == 1

    def test_retries_on_nonzero_exit(self) -> None:
        with patch(
            "lib.invoke.subprocess.run", side_effect=self._mock_run_fail
        ) as mock:
            result: dict[str, Any] = invoke_claude(
                "prompt", ["Read"], 60000, "/tmp", max_retries=2
            )
        assert result["exit_code"] == 1
        assert mock.call_count == 3  # 1 initial + 2 retries

    def test_retry_succeeds_on_second_attempt(self) -> None:
        calls: list[subprocess.CompletedProcess[str]] = [
            subprocess.CompletedProcess([], returncode=1, stdout="fail", stderr="err"),
            subprocess.CompletedProcess([], returncode=0, stdout="success", stderr=""),
        ]
        with patch("lib.invoke.subprocess.run", side_effect=calls) as mock:
            result: dict[str, Any] = invoke_claude(
                "prompt", ["Read"], 60000, "/tmp", max_retries=2
            )
        assert result["exit_code"] == 0
        assert result["stdout"] == "success"
        assert mock.call_count == 2

    def test_no_retry_on_timeout(self) -> None:
        def timeout_side_effect(*args: Any, **kwargs: Any) -> None:
            raise subprocess.TimeoutExpired(cmd="claude", timeout=60)

        with patch(
            "lib.invoke.subprocess.run", side_effect=timeout_side_effect
        ) as mock:
            result: dict[str, Any] = invoke_claude(
                "prompt", ["Read"], 60000, "/tmp", max_retries=2
            )
        assert result["timed_out"] is True
        assert mock.call_count == 1

    def test_no_retry_on_file_not_found(self) -> None:
        with patch("lib.invoke.subprocess.run", side_effect=FileNotFoundError) as mock:
            result: dict[str, Any] = invoke_claude(
                "prompt", ["Read"], 60000, "/tmp", max_retries=2
            )
        assert result["exit_code"] == -1
        assert "not found" in result["stderr"]
        assert mock.call_count == 1

    def test_max_retries_zero(self) -> None:
        with patch(
            "lib.invoke.subprocess.run", side_effect=self._mock_run_fail
        ) as mock:
            result: dict[str, Any] = invoke_claude(
                "prompt", ["Read"], 60000, "/tmp", max_retries=0
            )
        assert result["exit_code"] == 1
        assert mock.call_count == 1


# ── lib/git_context.py ──────────────────────────────────────────────────


class TestParsePrRef:
    def test_shorthand_format(self) -> None:
        repo: str
        num: int
        repo, num = parse_pr_ref("owner/repo#123")
        assert repo == "owner/repo"
        assert num == 123

    def test_url_format(self) -> None:
        repo, num = parse_pr_ref("https://github.com/owner/repo/pull/456")
        assert repo == "owner/repo"
        assert num == 456

    def test_invalid_format_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid PR reference"):
            parse_pr_ref("not-a-pr-ref")

    def test_url_with_http(self) -> None:
        repo, num = parse_pr_ref("http://github.com/org/project/pull/99")
        assert repo == "org/project"
        assert num == 99


# ── lib/run_logger.py ───────────────────────────────────────────────────


class TestRunLogger:
    def test_next_sequence_number_empty(self, tmp_path: Path) -> None:
        assert _next_sequence_number(tmp_path) == 1

    def test_next_sequence_number_existing(self, tmp_path: Path) -> None:
        (tmp_path / "001_review_2026-01-01T00-00").mkdir()
        (tmp_path / "002_review_2026-01-01T00-01").mkdir()
        assert _next_sequence_number(tmp_path) == 3

    def test_next_sequence_number_ignores_non_matching(self, tmp_path: Path) -> None:
        (tmp_path / "001_review_2026-01-01T00-00").mkdir()
        (tmp_path / "random_dir").mkdir()
        assert _next_sequence_number(tmp_path) == 2

    def test_create_run(self, tmp_path: Path) -> None:
        with patch("lib.run_logger.RUNS_DIR", tmp_path):
            run_dir: str = create_run("test-worktree", "review")
        run_path: Path = Path(run_dir)
        assert run_path.exists()
        assert run_path.name.startswith("001_review_")
        assert (run_path / "steps").is_dir()

    def test_create_run_sequential(self, tmp_path: Path) -> None:
        with patch("lib.run_logger.RUNS_DIR", tmp_path):
            first: str = create_run("wt", "review")
            second: str = create_run("wt", "review")
        assert Path(first).name.startswith("001_")
        assert Path(second).name.startswith("002_")

    def test_save_step_output(self, tmp_path: Path) -> None:
        steps_dir: Path = tmp_path / "steps"
        steps_dir.mkdir()
        rel: str = save_step_output(str(tmp_path), 1, "review_emit", "hello output")
        assert rel == "steps/1_review_emit_stdout.txt"
        assert (tmp_path / rel).read_text(encoding="utf-8") == "hello output"

    def test_save_step_error(self, tmp_path: Path) -> None:
        steps_dir: Path = tmp_path / "steps"
        steps_dir.mkdir()
        rel: str = save_step_error(str(tmp_path), 2, "coherence", "some error")
        assert rel == "steps/2_coherence_stderr.txt"
        assert (tmp_path / rel).read_text(encoding="utf-8") == "some error"

    def test_write_run_json(self, tmp_path: Path) -> None:
        data: dict[str, Any] = {"status": "pass", "issues": 3}
        write_run_json(str(tmp_path), data)
        run_json: Path = tmp_path / "run.json"
        assert run_json.exists()
        loaded: dict[str, Any] = json.loads(run_json.read_text(encoding="utf-8"))
        assert loaded == data

    def test_save_diff_snapshot(self, tmp_path: Path) -> None:
        save_diff_snapshot(str(tmp_path), "diff content here")
        patch_file: Path = tmp_path / "diff_before.patch"
        assert patch_file.exists()
        assert patch_file.read_text(encoding="utf-8") == "diff content here"


# ── stages/review.py ────────────────────────────────────────────────────


class TestCountIssues:
    def setup_method(self) -> None:
        # Import here to avoid import side effects at module level
        from stages.review import _count_issues

        self._count_issues = _count_issues

    def test_total_line(self) -> None:
        assert self._count_issues("blah\nTOTAL: 5 issues\nblah") == 5

    def test_total_new_issues(self) -> None:
        assert self._count_issues("TOTAL: 3 new issues") == 3

    def test_total_single_issue(self) -> None:
        assert self._count_issues("TOTAL: 1 issue") == 1

    def test_issue_headers_fallback(self) -> None:
        text: str = "### ISSUE-1\nfoo\n### ISSUE-2\nbar\n### ISSUE-3\nbaz"
        assert self._count_issues(text) == 3

    def test_no_issues(self) -> None:
        assert self._count_issues("Everything looks good, CLEAN") == 0

    def test_case_insensitive_total(self) -> None:
        assert self._count_issues("total: 7 issues") == 7


# ── metrics/runner.py ───────────────────────────────────────────────────


class TestRunMetrics:
    def test_no_files_returns_empty(self) -> None:
        from metrics.runner import run_metrics

        config: dict[str, Any] = {
            "metrics": {},
            "repo_path": "/nonexistent",
            "base_branch": "origin/main",
        }
        result: dict[str, Any] = run_metrics([], config)
        assert result["violations"] == []
        assert result["summary"]["total_violations"] == 0

    def test_missing_tools_skipped(self, tmp_path: Path) -> None:
        """When no metric tools are installed, all checks are skipped gracefully."""
        from metrics.runner import run_metrics

        # Create a dummy .py file so file-existence checks pass
        dummy: Path = tmp_path / "test.py"
        dummy.write_text("x = 1\n", encoding="utf-8")

        config: dict[str, Any] = {
            "metrics": {},
            "repo_path": str(tmp_path),
            "base_branch": "origin/main",
        }
        # Mock shutil.which to always return None (tools not installed)
        with patch("metrics.runner.shutil.which", return_value=None):
            result: dict[str, Any] = run_metrics(["test.py"], config)
        assert result["summary"]["total_violations"] == 0
