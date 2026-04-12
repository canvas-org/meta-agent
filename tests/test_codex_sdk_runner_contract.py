"""Hermetic tests for the CodexSdkRunResult contract and runner structure."""
from __future__ import annotations

import unittest
from dataclasses import fields

from meta_agent.codex_sdk_runner import CodexSdkRunResult, run_codex_sdk_turn


class TestCodexSdkRunResultContract(unittest.TestCase):
    def test_default_result_has_expected_fields(self) -> None:
        r = CodexSdkRunResult()
        expected = {
            "final_response", "raw_events_jsonl", "normalized_trace_jsonl",
            "stderr", "exit_code", "timed_out", "hook_failures",
            "hook_warnings", "usage", "items",
        }
        actual = {f.name for f in fields(r)}
        self.assertEqual(actual, expected)

    def test_default_result_values(self) -> None:
        r = CodexSdkRunResult()
        self.assertEqual(r.final_response, "")
        self.assertEqual(r.exit_code, 0)
        self.assertFalse(r.timed_out)
        self.assertEqual(r.hook_failures, [])
        self.assertEqual(r.hook_warnings, [])
        self.assertIsNone(r.usage)
        self.assertEqual(r.items, [])

    def test_result_is_mutable(self) -> None:
        r = CodexSdkRunResult()
        r.final_response = "hello"
        r.exit_code = 1
        r.timed_out = True
        r.hook_failures.append("fail")
        self.assertEqual(r.final_response, "hello")
        self.assertEqual(r.exit_code, 1)
        self.assertTrue(r.timed_out)
        self.assertEqual(r.hook_failures, ["fail"])

    def test_run_codex_sdk_turn_fails_without_sdk(self) -> None:
        """If codex_app_server is not installed, the runner returns a clean error."""
        try:
            import codex_app_server  # noqa: F401
            self.skipTest("codex_app_server is installed; cannot test missing-SDK path")
        except ImportError:
            pass

        result = run_codex_sdk_turn(
            prompt="test",
            model="gpt-5.4",
            cwd="/tmp",
        )
        self.assertEqual(result.exit_code, 1)
        self.assertIn("codex_app_server", result.stderr)

    def test_independent_list_defaults(self) -> None:
        """Each result instance should have independent mutable lists."""
        r1 = CodexSdkRunResult()
        r2 = CodexSdkRunResult()
        r1.hook_failures.append("a")
        r1.items.append("b")
        self.assertEqual(r2.hook_failures, [])
        self.assertEqual(r2.items, [])
