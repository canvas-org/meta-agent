"""Hermetic tests for final response extraction from items and notifications."""
from __future__ import annotations

import unittest

from meta_agent.codex_sdk_runner import (
    extract_final_response_from_items,
    extract_final_response_from_notifications,
)


class _FakeItem:
    def __init__(self, **kwargs: object) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


class TestExtractFromItems(unittest.TestCase):
    def test_camel_case_agent_message(self) -> None:
        items = [{"type": "agentMessage", "text": "The fix is applied."}]
        self.assertEqual(extract_final_response_from_items(items), "The fix is applied.")

    def test_single_agent_message(self) -> None:
        items = [{"type": "agent_message", "text": "The fix is applied."}]
        self.assertEqual(extract_final_response_from_items(items), "The fix is applied.")

    def test_last_agent_message_wins(self) -> None:
        items = [
            {"type": "agent_message", "text": "first"},
            {"type": "command_execution", "command": "ls"},
            {"type": "agent_message", "text": "second"},
        ]
        self.assertEqual(extract_final_response_from_items(items), "second")

    def test_no_agent_message_returns_empty(self) -> None:
        items = [
            {"type": "command_execution", "command": "ls"},
            {"type": "file_change", "changes": []},
        ]
        self.assertEqual(extract_final_response_from_items(items), "")

    def test_empty_items(self) -> None:
        self.assertEqual(extract_final_response_from_items([]), "")

    def test_whitespace_only_message_skipped(self) -> None:
        items = [
            {"type": "agent_message", "text": "   "},
            {"type": "agent_message", "text": "real answer"},
        ]
        self.assertEqual(extract_final_response_from_items(items), "real answer")

    def test_sdk_objects_with_attributes(self) -> None:
        items = [
            _FakeItem(type="agent_message", text="from object"),
        ]
        self.assertEqual(extract_final_response_from_items(items), "from object")


class TestExtractFromNotifications(unittest.TestCase):
    def test_extracts_from_jsonrpc_item_completed(self) -> None:
        notifications = [
            {
                "jsonrpc": "2.0",
                "method": "item/completed",
                "params": {
                    "item": {"type": "agentMessage", "text": "done"},
                    "turnId": "turn-1",
                },
            }
        ]
        self.assertEqual(
            extract_final_response_from_notifications(notifications), "done"
        )

    def test_extracts_from_item_completed(self) -> None:
        notifications = [
            {"type": "turn.started"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "done"}},
            {"type": "turn.completed", "usage": {}},
        ]
        self.assertEqual(
            extract_final_response_from_notifications(notifications), "done"
        )

    def test_last_agent_message_wins(self) -> None:
        notifications = [
            {"type": "item.completed", "item": {"type": "agent_message", "text": "first"}},
            {"type": "item.completed", "item": {"type": "command_execution", "command": "ls"}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "final"}},
        ]
        self.assertEqual(
            extract_final_response_from_notifications(notifications), "final"
        )

    def test_no_agent_messages(self) -> None:
        notifications = [
            {"type": "item.completed", "item": {"type": "command_execution", "command": "pwd"}},
        ]
        self.assertEqual(extract_final_response_from_notifications(notifications), "")

    def test_empty_notifications(self) -> None:
        self.assertEqual(extract_final_response_from_notifications([]), "")

    def test_non_dict_item_skipped(self) -> None:
        notifications = [
            {"type": "item.completed", "item": "not a dict"},
        ]
        self.assertEqual(extract_final_response_from_notifications(notifications), "")
