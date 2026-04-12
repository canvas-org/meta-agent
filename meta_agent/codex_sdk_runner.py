"""Shared Python Codex SDK runner — single source of truth for SDK execution.

This module owns:
- SDK client/thread lifecycle
- low-level turn streaming via thread.turn(...).stream()
- raw notification capture
- normalized trace generation
- final response extraction from persisted turn state
- timeout and error normalization

Callers should NOT be wired to this module yet (that happens in commit 3+).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional


@dataclass
class CodexSdkRunResult:
    final_response: str = ""
    raw_events_jsonl: str = ""
    normalized_trace_jsonl: str = ""
    stderr: str = ""
    exit_code: int = 0
    timed_out: bool = False
    hook_failures: list[str] = field(default_factory=list)
    hook_warnings: list[str] = field(default_factory=list)
    usage: Any = None  # ThreadTokenUsage | None at runtime
    items: list[Any] = field(default_factory=list)  # list[ThreadItem] at runtime


# ---------------------------------------------------------------------------
# Trace helpers (pure functions, tested hermetically)
# ---------------------------------------------------------------------------

def normalize_notification(notification: Any) -> dict[str, Any] | None:
    """Convert a raw SDK Notification into a normalized trace dict.

    Returns None for notifications that don't map to a trace event.
    """
    ntype = getattr(notification, "type", None) or (
        notification.get("type") if isinstance(notification, dict) else None
    )

    if ntype == "item.completed":
        item = _extract_attr_or_key(notification, "item")
        if item is None:
            return None
        item_dict = _item_to_dict(item)
        return {"type": "item.completed", "item": item_dict}

    if ntype == "turn.completed":
        usage = _extract_attr_or_key(notification, "usage")
        return {"type": "turn.completed", "usage": _to_serializable(usage)}

    if ntype == "turn.started":
        return {"type": "turn.started"}

    if ntype == "thread.started":
        thread_id = _extract_attr_or_key(notification, "thread_id")
        return {"type": "thread.started", "thread_id": thread_id}

    if ntype == "turn.failed":
        error = _extract_attr_or_key(notification, "error")
        return {"type": "turn.failed", "error": _to_serializable(error)}

    if ntype in ("item.started", "item.updated"):
        item = _extract_attr_or_key(notification, "item")
        item_dict = _item_to_dict(item) if item is not None else {}
        return {"type": ntype, "item": item_dict}

    return {"type": str(ntype), "raw": str(notification)[:500]}


def build_raw_events_jsonl(notifications: list[dict[str, Any]]) -> str:
    """Serialize raw notification dicts to JSONL."""
    lines = [json.dumps(n, default=str) for n in notifications]
    return "\n".join(lines) + "\n" if lines else ""


def build_normalized_trace_jsonl(normalized: list[dict[str, Any]]) -> str:
    """Serialize normalized trace events to JSONL."""
    lines = [json.dumps(e, default=str) for e in normalized if e is not None]
    return "\n".join(lines) + "\n" if lines else ""


def extract_final_response_from_items(items: list[Any]) -> str:
    """Extract the final agent message text from a list of thread items.

    Walks the items list and returns the text of the last agent_message item.
    Items can be dicts or SDK ThreadItem objects.
    """
    last_message = ""
    for item in items:
        item_type = _extract_attr_or_key(item, "type")
        if item_type == "agent_message":
            text = _extract_attr_or_key(item, "text")
            if isinstance(text, str) and text.strip():
                last_message = text
    return last_message


def extract_final_response_from_notifications(
    notifications: list[dict[str, Any]],
) -> str:
    """Extract the final agent message from raw notification dicts.

    Falls back to scanning item.completed events when items list is
    unavailable (e.g. during streaming before turn completes).
    """
    last_message = ""
    for n in notifications:
        ntype = n.get("type")
        if ntype != "item.completed":
            continue
        item = n.get("item", {})
        if not isinstance(item, dict):
            continue
        if item.get("type") != "agent_message":
            continue
        text = item.get("text", "")
        if isinstance(text, str) and text.strip():
            last_message = text
    return last_message


# ---------------------------------------------------------------------------
# SDK execution (requires codex_app_server at runtime)
# ---------------------------------------------------------------------------

def run_codex_sdk_turn(
    *,
    prompt: str,
    model: str,
    cwd: str,
    timeout_sec: int = 300,
    approval_policy: str = "never",
    sandbox: str = "workspace-write",
    config: dict[str, Any] | None = None,
) -> CodexSdkRunResult:
    """Execute a single Codex SDK turn and return a typed result.

    Uses the sync Codex client with thread.turn(...).stream() for
    raw notification capture.
    """
    try:
        from codex_app_server import Codex, TextInput
    except ImportError as exc:
        return CodexSdkRunResult(
            stderr=f"codex_app_server not installed: {exc}",
            exit_code=1,
        )

    raw_notifications: list[dict[str, Any]] = []
    normalized_events: list[dict[str, Any]] = []
    collected_items: list[Any] = []
    usage_obj: Any = None
    stderr_parts: list[str] = []
    timed_out = False
    exit_code = 0
    error_message = ""

    start = time.monotonic()

    try:
        sdk_config = config or {}
        with Codex(config=sdk_config or None) as codex:
            thread = codex.thread_start(
                model=model,
                cwd=cwd,
                approval_policy=approval_policy,
                sandbox=sandbox,
            )

            turn_handle = thread.turn(TextInput(prompt))

            for notification in turn_handle.stream():
                elapsed = time.monotonic() - start
                if elapsed > timeout_sec:
                    timed_out = True
                    stderr_parts.append(
                        f"TIMEOUT after {int(elapsed)}s (limit {timeout_sec}s)"
                    )
                    break

                raw_dict = _to_serializable(notification)
                if isinstance(raw_dict, dict):
                    raw_notifications.append(raw_dict)
                else:
                    raw_notifications.append(
                        {"type": "unknown", "raw": str(raw_dict)[:500]}
                    )

                norm = normalize_notification(notification)
                if norm is not None:
                    normalized_events.append(norm)

                ntype = getattr(notification, "type", None)
                if ntype == "item.completed":
                    item = getattr(notification, "item", None)
                    if item is not None:
                        collected_items.append(item)
                elif ntype == "turn.completed":
                    usage_obj = getattr(notification, "usage", None)
                elif ntype == "turn.failed":
                    err = getattr(notification, "error", None)
                    error_message = str(getattr(err, "message", err))
                    exit_code = 1

            if not timed_out and not error_message:
                try:
                    persisted = thread.read(include_turns=True)
                    turns = getattr(
                        getattr(persisted, "thread", None), "turns", None
                    )
                    if turns and len(turns) > 0:
                        last_turn = turns[-1]
                        turn_items = getattr(last_turn, "items", None) or []
                        if turn_items:
                            collected_items = list(turn_items)
                except Exception as read_err:
                    stderr_parts.append(f"thread.read() failed: {read_err}")

    except Exception as exc:
        exit_code = 1
        error_message = f"{type(exc).__name__}: {exc}"
        stderr_parts.append(error_message)

    if timed_out:
        exit_code = 1

    final_response = extract_final_response_from_items(collected_items)
    if not final_response:
        final_response = extract_final_response_from_notifications(raw_notifications)

    return CodexSdkRunResult(
        final_response=final_response,
        raw_events_jsonl=build_raw_events_jsonl(raw_notifications),
        normalized_trace_jsonl=build_normalized_trace_jsonl(normalized_events),
        stderr="\n".join(stderr_parts),
        exit_code=exit_code,
        timed_out=timed_out,
        usage=usage_obj,
        items=collected_items,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_attr_or_key(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _item_to_dict(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return item
    if hasattr(item, "model_dump"):
        return item.model_dump()
    if hasattr(item, "__dict__"):
        return {k: v for k, v in item.__dict__.items() if not k.startswith("_")}
    return {"raw": str(item)[:500]}


def _to_serializable(obj: Any) -> Any:
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {k: _to_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_serializable(v) for v in obj]
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "__dict__"):
        return {k: _to_serializable(v) for k, v in obj.__dict__.items()
                if not k.startswith("_")}
    return str(obj)[:500]
