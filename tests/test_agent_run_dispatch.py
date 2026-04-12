"""Tests for the unified run_agent() dispatch in meta_agent/task_runner.py."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from meta_agent.codex_sdk_runner import CodexSdkRunResult
from meta_agent.task_runner import AgentRunResult, run_agent


def _make_sdk_result(**overrides: object) -> CodexSdkRunResult:
    defaults = {
        "final_response": "Done.",
        "raw_events_jsonl": '{"type":"turn.started"}\n',
        "normalized_trace_jsonl": '{"type":"item.completed","item":{"type":"agent_message","text":"Done."}}\n',
        "stderr": "",
        "exit_code": 0,
        "timed_out": False,
        "hook_failures": [],
        "hook_warnings": [],
        "usage": None,
        "items": [],
    }
    defaults.update(overrides)
    return CodexSdkRunResult(**defaults)  # type: ignore[arg-type]


class TestRunAgentCodexSdk(unittest.TestCase):
    @patch("meta_agent.task_runner._codex_native_hooks_supported", return_value=True)
    @patch("meta_agent.codex_sdk_runner.run_codex_sdk_turn")
    def test_returns_agent_run_result(self, mock_turn: object, _: object) -> None:
        mock_turn.return_value = _make_sdk_result()  # type: ignore[union-attr]

        with tempfile.TemporaryDirectory() as tmp_root:
            config = Path(tmp_root) / "cfg"
            config.mkdir()
            work = Path(tmp_root) / "work"
            work.mkdir()
            result = run_agent(
                prompt="fix it", config_dir=str(config), model="m",
                work_dir=work, timeout=30, runtime="codex_sdk",
            )

            self.assertIsInstance(result, AgentRunResult)
            self.assertEqual(result.final_response, "Done.")
            self.assertEqual(result.exit_code, 0)
            self.assertTrue((work / "trace.jsonl").exists())
            self.assertTrue((work / "trace.raw.jsonl").exists())
            self.assertTrue((work / "final_response.txt").exists())

    @patch("meta_agent.task_runner._codex_native_hooks_supported", return_value=True)
    @patch("meta_agent.codex_sdk_runner.run_codex_sdk_turn")
    def test_codex_sdk_failure(self, mock_turn: object, _: object) -> None:
        mock_turn.return_value = _make_sdk_result(exit_code=1, stderr="fail")  # type: ignore[union-attr]

        with tempfile.TemporaryDirectory() as tmp_root:
            config = Path(tmp_root) / "cfg"
            config.mkdir()
            work = Path(tmp_root) / "work"
            work.mkdir()
            result = run_agent(
                prompt="x", config_dir=str(config), model="m",
                work_dir=work, timeout=30, runtime="codex_sdk",
            )

        self.assertEqual(result.exit_code, 1)


class TestRunAgentUnsupported(unittest.TestCase):
    def test_unsupported_runtime_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                run_agent(
                    prompt="x", config_dir=str(Path(tmp)), model="m",
                    work_dir=Path(tmp), timeout=30, runtime="unknown_runtime",
                )


class TestAgentRunResultDefaults(unittest.TestCase):
    def test_defaults(self) -> None:
        r = AgentRunResult()
        self.assertEqual(r.final_response, "")
        self.assertEqual(r.trace_jsonl, "")
        self.assertEqual(r.exit_code, 0)
        self.assertEqual(r.hook_failures, [])
        self.assertEqual(r.hook_warnings, [])

    def test_independent_lists(self) -> None:
        r1 = AgentRunResult()
        r2 = AgentRunResult()
        r1.hook_failures.append("a")
        self.assertEqual(r2.hook_failures, [])
