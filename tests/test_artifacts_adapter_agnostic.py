"""Tests that the refactored ArtifactsBench adapter uses the unified dispatch."""
from __future__ import annotations

import json
import unittest

from benchmarks.artifacts_bench.adapter import (
    _extract_text_from_jsonl,
    extract_answer_from_codex_output,
)


class TestExtractTextFromJsonlNormalized(unittest.TestCase):
    """Verify _extract_text_from_jsonl handles the normalized SDK trace format."""

    def test_item_completed_agent_message(self) -> None:
        raw = json.dumps({
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "Here is the fix."},
        })
        result = _extract_text_from_jsonl(raw)
        self.assertEqual(result, "Here is the fix.")

    def test_legacy_message_format(self) -> None:
        raw = json.dumps({"type": "message", "content": "hello"})
        result = _extract_text_from_jsonl(raw)
        self.assertEqual(result, "hello")

    def test_claude_assistant_format(self) -> None:
        raw = json.dumps({
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "hi"}]},
        })
        result = _extract_text_from_jsonl(raw)
        self.assertEqual(result, "hi")

    def test_mixed_formats(self) -> None:
        lines = "\n".join([
            json.dumps({"type": "turn.started"}),
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "first"}}),
            json.dumps({"type": "item.completed", "item": {"type": "command_execution", "command": "ls"}}),
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "second"}}),
        ])
        result = _extract_text_from_jsonl(lines)
        self.assertIn("first", result)
        self.assertIn("second", result)

    def test_empty_returns_none(self) -> None:
        self.assertIsNone(_extract_text_from_jsonl(""))

    def test_non_agent_message_items_skipped(self) -> None:
        raw = json.dumps({
            "type": "item.completed",
            "item": {"type": "command_execution", "command": "pwd"},
        })
        self.assertIsNone(_extract_text_from_jsonl(raw))


class TestAdapterDoesNotImportRuntimeSpecific(unittest.TestCase):
    """Verify the adapter imports only the uniform dispatch, not runtime-specific runners."""

    def test_no_direct_runner_imports_in_run_agent_sync(self) -> None:
        import inspect
        from benchmarks.artifacts_bench.adapter import _run_agent_sync
        source = inspect.getsource(_run_agent_sync)
        self.assertNotIn("run_codex_cli_with_hooks", source)
        self.assertNotIn("run_codex_sdk_with_hooks", source)
        self.assertIn("run_agent", source)
