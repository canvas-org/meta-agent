"""Hermetic tests for the CodexSdkRunResult contract and runner structure."""
from __future__ import annotations

import json
import sys
import types
import unittest
from dataclasses import fields
from unittest.mock import MagicMock, patch

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


def _build_fake_codex_module() -> types.ModuleType:
    """Build a minimal fake codex_app_server module for mock tests."""
    mod = types.ModuleType("codex_app_server")

    class TextInput:
        def __init__(self, text: str) -> None:
            self.text = text

    class _FakeNotification:
        def __init__(self, **kwargs: object) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)

    class _FakeItem:
        def __init__(self, **kwargs: object) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)

    class _FakeUsage:
        def __init__(self) -> None:
            self.input_tokens = 10
            self.output_tokens = 5

    class _FakeTurnHandle:
        def __init__(self, notifications: list[object]) -> None:
            self._notifications = notifications

        def stream(self) -> list[object]:
            return self._notifications

    class _FakeTurn:
        def __init__(self) -> None:
            self.items = [
                _FakeItem(type="agent_message", text="Fixed the bug."),
            ]

    class _FakeThreadObj:
        def __init__(self) -> None:
            self.turns = [_FakeTurn()]

    class _FakeReadResponse:
        def __init__(self) -> None:
            self.thread = _FakeThreadObj()

    class _FakeThread:
        def turn(self, text_input: TextInput) -> _FakeTurnHandle:
            item = _FakeItem(type="agent_message", text="Fixed the bug.")
            usage = _FakeUsage()
            notifications = [
                _FakeNotification(type="turn.started"),
                _FakeNotification(type="item.completed", item=item),
                _FakeNotification(type="turn.completed", usage=usage),
            ]
            return _FakeTurnHandle(notifications)

        def read(self, include_turns: bool = False) -> _FakeReadResponse:
            return _FakeReadResponse()

    class Codex:
        def __init__(self, config: dict[str, object] | None = None) -> None:
            pass

        def __enter__(self) -> "Codex":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def thread_start(self, **kwargs: object) -> _FakeThread:
            return _FakeThread()

    mod.Codex = Codex  # type: ignore[attr-defined]
    mod.TextInput = TextInput  # type: ignore[attr-defined]
    return mod


class TestRunCodexSdkTurnSuccessPath(unittest.TestCase):
    """Exercise the full success path with a mocked codex_app_server."""

    def setUp(self) -> None:
        self._fake_mod = _build_fake_codex_module()
        self._original = sys.modules.get("codex_app_server")
        sys.modules["codex_app_server"] = self._fake_mod

    def tearDown(self) -> None:
        if self._original is not None:
            sys.modules["codex_app_server"] = self._original
        else:
            sys.modules.pop("codex_app_server", None)

    def test_success_returns_final_response(self) -> None:
        result = run_codex_sdk_turn(
            prompt="Fix the bug",
            model="gpt-5.4",
            cwd="/tmp",
            timeout_sec=60,
        )
        self.assertEqual(result.exit_code, 0)
        self.assertFalse(result.timed_out)
        self.assertEqual(result.final_response, "Fixed the bug.")

    def test_success_writes_raw_events(self) -> None:
        result = run_codex_sdk_turn(
            prompt="Fix the bug",
            model="gpt-5.4",
            cwd="/tmp",
        )
        self.assertTrue(len(result.raw_events_jsonl.strip()) > 0)
        events = [json.loads(line) for line in result.raw_events_jsonl.strip().split("\n")]
        event_types = [e.get("type") for e in events]
        self.assertIn("turn.started", event_types)
        self.assertIn("item.completed", event_types)
        self.assertIn("turn.completed", event_types)

    def test_success_writes_normalized_trace(self) -> None:
        result = run_codex_sdk_turn(
            prompt="Fix the bug",
            model="gpt-5.4",
            cwd="/tmp",
        )
        self.assertTrue(len(result.normalized_trace_jsonl.strip()) > 0)
        events = [json.loads(line) for line in result.normalized_trace_jsonl.strip().split("\n")]
        event_types = [e.get("type") for e in events]
        self.assertIn("turn.started", event_types)
        self.assertIn("item.completed", event_types)

    def test_success_captures_usage(self) -> None:
        result = run_codex_sdk_turn(
            prompt="Fix the bug",
            model="gpt-5.4",
            cwd="/tmp",
        )
        self.assertIsNotNone(result.usage)
        self.assertEqual(result.usage.input_tokens, 10)
        self.assertEqual(result.usage.output_tokens, 5)

    def test_success_prefers_persisted_items(self) -> None:
        result = run_codex_sdk_turn(
            prompt="Fix the bug",
            model="gpt-5.4",
            cwd="/tmp",
        )
        self.assertTrue(len(result.items) > 0)
        self.assertEqual(result.final_response, "Fixed the bug.")

    def test_success_has_empty_stderr(self) -> None:
        result = run_codex_sdk_turn(
            prompt="Fix the bug",
            model="gpt-5.4",
            cwd="/tmp",
        )
        self.assertEqual(result.stderr, "")
