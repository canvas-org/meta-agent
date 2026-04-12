"""Tests for the codex_sdk path in meta_agent/task_runner.py after commit 3."""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from meta_agent.codex_sdk_runner import CodexSdkRunResult


def _make_sdk_result(**overrides: object) -> CodexSdkRunResult:
    defaults = {
        "final_response": "Fixed the bug.",
        "raw_events_jsonl": '{"type":"turn.started"}\n',
        "normalized_trace_jsonl": '{"type":"item.completed","item":{"type":"agent_message","text":"Fixed the bug."}}\n',
        "stderr": "",
        "exit_code": 0,
        "timed_out": False,
        "hook_failures": [],
        "hook_warnings": [],
        "usage": None,
        "items": [{"type": "agent_message", "text": "Fixed the bug."}],
    }
    defaults.update(overrides)
    return CodexSdkRunResult(**defaults)  # type: ignore[arg-type]


class TestRunCodexSdkWithHooksNative(unittest.TestCase):
    """Test _run_codex_sdk_with_hooks_native calls the shared runner."""

    @patch("meta_agent.task_runner._codex_native_hooks_supported", return_value=True)
    @patch("meta_agent.codex_sdk_runner.run_codex_sdk_turn")
    def test_calls_shared_runner(self, mock_turn: object, _mock_hooks: object) -> None:
        from meta_agent.task_runner import _run_codex_sdk_with_hooks_native

        mock_turn.return_value = _make_sdk_result()  # type: ignore[union-attr]

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            result = _run_codex_sdk_with_hooks_native(
                prompt="fix it", model="gpt-5.4", work_dir=work, timeout=60,
            )

        mock_turn.assert_called_once()  # type: ignore[union-attr]
        call_kwargs = mock_turn.call_args.kwargs  # type: ignore[union-attr]
        self.assertEqual(call_kwargs["prompt"], "fix it")
        self.assertEqual(call_kwargs["model"], "gpt-5.4")
        self.assertEqual(result.final_response, "Fixed the bug.")
        self.assertEqual(result.exit_code, 0)

    @patch("meta_agent.task_runner._codex_native_hooks_supported", return_value=True)
    @patch("meta_agent.codex_sdk_runner.run_codex_sdk_turn")
    def test_failure_propagates(self, mock_turn: object, _mock_hooks: object) -> None:
        from meta_agent.task_runner import _run_codex_sdk_with_hooks_native

        mock_turn.return_value = _make_sdk_result(  # type: ignore[union-attr]
            exit_code=1, stderr="SDK error", final_response="",
        )

        with tempfile.TemporaryDirectory() as tmp:
            result = _run_codex_sdk_with_hooks_native(
                prompt="x", model="m", work_dir=Path(tmp), timeout=30,
            )

        self.assertEqual(result.exit_code, 1)
        self.assertIn("SDK error", result.stderr)


class TestRunCodexSdkWithHooksCompat(unittest.TestCase):
    """Test the backward-compatible wrapper returns (CompletedProcess, list, list)."""

    @patch("meta_agent.task_runner._codex_native_hooks_supported", return_value=True)
    @patch("meta_agent.codex_sdk_runner.run_codex_sdk_turn")
    def test_compat_returns_tuple(self, mock_turn: object, _mock_hooks: object) -> None:
        from meta_agent.task_runner import run_codex_sdk_with_hooks

        mock_turn.return_value = _make_sdk_result()  # type: ignore[union-attr]

        with tempfile.TemporaryDirectory() as tmp:
            cp, failures, warnings = run_codex_sdk_with_hooks(
                prompt="fix", model="m", work_dir=Path(tmp), timeout=30,
            )

        self.assertEqual(cp.returncode, 0)
        self.assertIn("item.completed", cp.stdout)
        self.assertEqual(failures, [])
        self.assertEqual(warnings, [])


class TestRunTaskCodexSdk(unittest.TestCase):
    """Test run_task_codex_sdk writes trace artifacts and returns TaskResult."""

    @patch("meta_agent.task_runner._codex_native_hooks_supported", return_value=True)
    @patch("meta_agent.codex_sdk_runner.run_codex_sdk_turn")
    def test_writes_trace_files(self, mock_turn: object, _mock_hooks: object) -> None:
        from meta_agent.benchmark import Task
        from meta_agent.task_runner import run_task_codex_sdk

        mock_turn.return_value = _make_sdk_result()  # type: ignore[union-attr]

        with tempfile.TemporaryDirectory() as tmp_root:
            config_dir = Path(tmp_root) / "config"
            config_dir.mkdir()
            (config_dir / "AGENTS.md").write_text("fix it")

            work = Path(tmp_root) / "task"
            work.mkdir()

            task = Task(
                name="test-task",
                instruction="fix it",
                workspace=str(work),
                verify=["python", "-c", "print('ok')"],
                timeout=30,
            )

            result = asyncio.run(run_task_codex_sdk(
                task, str(config_dir), "gpt-5.4", work,
            ))

            self.assertTrue((work / "trace.jsonl").exists())
            self.assertTrue((work / "trace.raw.jsonl").exists())
            self.assertTrue((work / "final_response.txt").exists())
            self.assertEqual(
                (work / "final_response.txt").read_text(), "Fixed the bug.",
            )
            self.assertTrue(result.passed)
            self.assertEqual(result.task_name, "test-task")
