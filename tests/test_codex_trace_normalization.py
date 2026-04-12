"""Hermetic tests for trace normalization helpers."""
from __future__ import annotations

import json
import unittest

from meta_agent.codex_sdk_runner import (
    build_normalized_trace_jsonl,
    build_raw_events_jsonl,
    normalize_notification,
)


class _FakeNotification:
    """Mimics an SDK Notification object with attribute access."""
    def __init__(self, **kwargs: object) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


class _FakeItem:
    def __init__(self, **kwargs: object) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


class TestNormalizeNotification(unittest.TestCase):
    def test_jsonrpc_item_completed_dict(self) -> None:
        notif = {
            "jsonrpc": "2.0",
            "method": "item/completed",
            "params": {
                "item": {"type": "agentMessage", "id": "1", "text": "hello"},
                "threadId": "thread-1",
            },
        }
        result = normalize_notification(notif)
        assert result is not None
        self.assertEqual(result["type"], "item.completed")
        self.assertEqual(result["item"]["type"], "agent_message")
        self.assertEqual(result["item"]["text"], "hello")

    def test_item_completed_dict(self) -> None:
        notif = {
            "type": "item.completed",
            "item": {"type": "agent_message", "id": "1", "text": "hello"},
        }
        result = normalize_notification(notif)
        assert result is not None
        self.assertEqual(result["type"], "item.completed")
        self.assertEqual(result["item"]["type"], "agent_message")
        self.assertEqual(result["item"]["text"], "hello")

    def test_item_completed_object(self) -> None:
        item = _FakeItem(type="command_execution", id="2", command="ls", status="completed")
        notif = _FakeNotification(type="item.completed", item=item)
        result = normalize_notification(notif)
        assert result is not None
        self.assertEqual(result["type"], "item.completed")
        self.assertEqual(result["item"]["type"], "command_execution")

    def test_turn_completed(self) -> None:
        notif = {"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 50}}
        result = normalize_notification(notif)
        assert result is not None
        self.assertEqual(result["type"], "turn.completed")
        self.assertEqual(result["usage"]["input_tokens"], 100)

    def test_jsonrpc_turn_completed(self) -> None:
        notif = {
            "jsonrpc": "2.0",
            "method": "turn/completed",
            "params": {"usage": {"input_tokens": 100, "output_tokens": 50}},
        }
        result = normalize_notification(notif)
        assert result is not None
        self.assertEqual(result["type"], "turn.completed")
        self.assertEqual(result["usage"]["input_tokens"], 100)

    def test_turn_started(self) -> None:
        result = normalize_notification({"type": "turn.started"})
        assert result is not None
        self.assertEqual(result["type"], "turn.started")

    def test_thread_started(self) -> None:
        result = normalize_notification({"type": "thread.started", "thread_id": "abc"})
        assert result is not None
        self.assertEqual(result["thread_id"], "abc")

    def test_turn_failed(self) -> None:
        notif = {"type": "turn.failed", "error": {"message": "boom"}}
        result = normalize_notification(notif)
        assert result is not None
        self.assertEqual(result["type"], "turn.failed")
        self.assertEqual(result["error"]["message"], "boom")

    def test_unknown_type_preserved(self) -> None:
        result = normalize_notification({"type": "custom.event"})
        assert result is not None
        self.assertEqual(result["type"], "custom.event")

    def test_item_completed_without_item_returns_none(self) -> None:
        result = normalize_notification({"type": "item.completed"})
        self.assertIsNone(result)


class TestBuildJsonl(unittest.TestCase):
    def test_raw_events_roundtrip(self) -> None:
        events = [
            {"type": "turn.started"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "hi"}},
        ]
        jsonl = build_raw_events_jsonl(events)
        lines = [json.loads(line) for line in jsonl.strip().split("\n")]
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["type"], "turn.started")

    def test_normalized_trace_skips_none(self) -> None:
        events = [{"type": "turn.started"}, None, {"type": "turn.completed"}]  # type: ignore[list-item]
        jsonl = build_normalized_trace_jsonl(events)
        lines = jsonl.strip().split("\n")
        self.assertEqual(len(lines), 2)

    def test_empty_list_returns_empty_string(self) -> None:
        self.assertEqual(build_raw_events_jsonl([]), "")
        self.assertEqual(build_normalized_trace_jsonl([]), "")
