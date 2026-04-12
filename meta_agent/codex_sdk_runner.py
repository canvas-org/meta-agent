"""Shared Python Codex SDK runner — single source of truth for SDK execution.

This module owns:
- SDK client/thread lifecycle
- low-level turn streaming via thread.turn(...).stream()
- raw notification capture
- normalized trace generation
- final response extraction from persisted turn state
- timeout and error normalization
"""
from __future__ import annotations

import asyncio
import concurrent.futures
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
    raw_notification = _notification_to_dict(notification)
    ntype = raw_notification.get("type")

    if ntype == "item.completed":
        item = raw_notification.get("item")
        if item is None:
            return None
        item_dict = _item_to_dict(item)
        return {"type": "item.completed", "item": item_dict}

    if ntype == "turn.completed":
        usage = raw_notification.get("usage")
        return {"type": "turn.completed", "usage": _to_serializable(usage)}

    if ntype == "turn.started":
        return {"type": "turn.started"}

    if ntype == "thread.started":
        thread_id = raw_notification.get("thread_id")
        return {"type": "thread.started", "thread_id": thread_id}

    if ntype == "turn.failed":
        error = raw_notification.get("error")
        return {"type": "turn.failed", "error": _to_serializable(error)}

    if ntype in ("item.started", "item.updated"):
        item = raw_notification.get("item")
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
        item_type = _normalize_item_type(_extract_attr_or_key(item, "type"))
        if item_type != "agent_message":
            continue
        text = _extract_item_text(item)
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
        normalized = normalize_notification(n)
        if normalized is None or normalized.get("type") != "item.completed":
            continue
        item = normalized.get("item", {})
        if not isinstance(item, dict):
            continue
        if _normalize_item_type(item.get("type")) != "agent_message":
            continue
        text = _extract_item_text(item) or ""
        if isinstance(text, str) and text.strip():
            last_message = text
    return last_message


# ---------------------------------------------------------------------------
# SDK execution (requires codex_app_server_sdk at runtime)
# ---------------------------------------------------------------------------

def _run_sdk_inner(
    *,
    prompt: str,
    model: str,
    cwd: str,
    timeout_sec: int,
    approval_policy: str,
    sandbox: str,
    config: dict[str, Any] | None,
) -> CodexSdkRunResult:
    """Inner execution body, runs inside a worker thread with a hard deadline."""
    return asyncio.run(
        _run_sdk_inner_async(
            prompt=prompt,
            model=model,
            cwd=cwd,
            timeout_sec=timeout_sec,
            approval_policy=approval_policy,
            sandbox=sandbox,
            config=config,
        )
    )


async def _run_sdk_inner_async(
    *,
    prompt: str,
    model: str,
    cwd: str,
    timeout_sec: int,
    approval_policy: str,
    sandbox: str,
    config: dict[str, Any] | None,
) -> CodexSdkRunResult:
    from codex_app_server_sdk import CodexClient, ThreadConfig

    raw_notifications: list[dict[str, Any]] = []
    normalized_events: list[dict[str, Any]] = []
    collected_items: list[Any] = []
    usage_obj: Any = None
    stderr_parts: list[str] = []
    exit_code = 0
    final_response = ""

    try:
        thread_config = ThreadConfig(
            model=model or None,
            cwd=cwd,
            approval_policy=approval_policy,
            sandbox=sandbox,
            config=config or None,
        )
        async with CodexClient.connect_stdio(
            cwd=cwd,
            inactivity_timeout=float(timeout_sec),
        ) as client:
            thread = await client.start_thread(thread_config)
            chat_result = await thread.chat_once(
                prompt,
                inactivity_timeout=float(timeout_sec),
            )

            for event in chat_result.raw_events:
                raw_dict = _notification_to_dict(event)
                raw_notifications.append(raw_dict)
                norm = normalize_notification(raw_dict)
                if norm is not None:
                    normalized_events.append(norm)

                if raw_dict.get("type") == "item.completed":
                    item = raw_dict.get("item")
                    if isinstance(item, dict):
                        collected_items.append(item)
                elif raw_dict.get("type") == "turn.completed":
                    usage = raw_dict.get("usage")
                    if usage is not None:
                        usage_obj = usage

            final_response = chat_result.final_text.strip()

            try:
                persisted = await thread.read(include_turns=True)
                persisted_items, persisted_usage = _extract_turn_items_and_usage(persisted)
                if persisted_items:
                    collected_items = persisted_items
                if persisted_usage is not None:
                    usage_obj = persisted_usage
            except Exception as read_err:
                stderr_parts.append(f"thread.read() failed: {read_err}")

    except Exception as exc:
        exit_code = 1
        stderr_parts.append(f"{type(exc).__name__}: {exc}")

    if not final_response:
        final_response = extract_final_response_from_items(collected_items)
    if not final_response:
        final_response = extract_final_response_from_notifications(raw_notifications)

    return CodexSdkRunResult(
        final_response=final_response,
        raw_events_jsonl=build_raw_events_jsonl(raw_notifications),
        normalized_trace_jsonl=build_normalized_trace_jsonl(normalized_events),
        stderr="\n".join(stderr_parts),
        exit_code=exit_code,
        timed_out=False,
        usage=usage_obj,
        items=collected_items,
    )


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

    Uses a hard deadline via ThreadPoolExecutor so a stalled SDK stream
    cannot hang the caller indefinitely.
    """
    try:
        from codex_app_server_sdk import CodexClient  # noqa: F401
    except ImportError as exc:
        return CodexSdkRunResult(
            stderr=f"codex_app_server_sdk not installed: {exc}",
            exit_code=1,
        )

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(
        _run_sdk_inner,
        prompt=prompt,
        model=model,
        cwd=cwd,
        timeout_sec=timeout_sec,
        approval_policy=approval_policy,
        sandbox=sandbox,
        config=config,
    )

    hard_deadline = timeout_sec + 30

    try:
        return future.result(timeout=hard_deadline)
    except concurrent.futures.TimeoutError:
        return CodexSdkRunResult(
            stderr=f"TIMEOUT after {hard_deadline}s (hard deadline, stream likely stalled)",
            exit_code=1,
            timed_out=True,
        )
    finally:
        executor.shutdown(wait=False)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_attr_or_key(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _item_to_dict(item: Any) -> dict[str, Any]:
    item_dict: dict[str, Any]
    if isinstance(item, dict):
        item_dict = {k: _to_serializable(v) for k, v in item.items()}
    elif hasattr(item, "model_dump"):
        dumped = item.model_dump()
        item_dict = dumped if isinstance(dumped, dict) else {"raw": str(dumped)[:500]}
    elif hasattr(item, "__dict__"):
        item_dict = {
            k: _to_serializable(v) for k, v in item.__dict__.items() if not k.startswith("_")
        }
    else:
        item_dict = {"raw": str(item)[:500]}

    item_type = item_dict.get("type")
    if isinstance(item_type, str):
        item_dict["type"] = _normalize_item_type(item_type)
    return item_dict


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


def _notification_to_dict(notification: Any) -> dict[str, Any]:
    if isinstance(notification, dict) and isinstance(notification.get("method"), str):
        return _jsonrpc_notification_to_dict(notification)

    ntype = getattr(notification, "type", None) or (
        notification.get("type") if isinstance(notification, dict) else None
    )
    payload: dict[str, Any] = {"type": str(ntype) if ntype is not None else "unknown"}

    item = _extract_attr_or_key(notification, "item")
    if item is not None:
        payload["item"] = _item_to_dict(item)

    usage = _extract_attr_or_key(notification, "usage")
    if usage is not None:
        payload["usage"] = _to_serializable(usage)

    error = _extract_attr_or_key(notification, "error")
    if error is not None:
        payload["error"] = _to_serializable(error)

    thread_id = _extract_attr_or_key(notification, "thread_id")
    if isinstance(thread_id, str):
        payload["thread_id"] = thread_id

    return payload


def _jsonrpc_notification_to_dict(notification: dict[str, Any]) -> dict[str, Any]:
    method = notification.get("method")
    params = notification.get("params")
    params_dict = params if isinstance(params, dict) else {}
    event_type = _normalize_event_type(method if isinstance(method, str) else "")

    payload: dict[str, Any] = {"type": event_type}

    item = params_dict.get("item")
    if item is not None:
        payload["item"] = _item_to_dict(item)

    usage = params_dict.get("usage")
    if usage is not None:
        payload["usage"] = _to_serializable(usage)

    error = params_dict.get("error")
    if error is not None:
        payload["error"] = _to_serializable(error)

    thread_id = _first_string(params_dict, "thread_id", "threadId")
    if thread_id is not None:
        payload["thread_id"] = thread_id

    turn_id = _first_string(params_dict, "turn_id", "turnId")
    if turn_id is not None:
        payload["turn_id"] = turn_id

    if event_type not in {
        "turn.started",
        "turn.completed",
        "turn.failed",
        "thread.started",
        "item.started",
        "item.updated",
        "item.completed",
    }:
        payload["raw"] = _to_serializable(notification)

    return payload


def _normalize_event_type(event_type: str) -> str:
    aliases = {
        "thread/start": "thread.started",
        "thread.started": "thread.started",
        "threadStarted": "thread.started",
        "turn/start": "turn.started",
        "turn.started": "turn.started",
        "turnStarted": "turn.started",
        "turn/completed": "turn.completed",
        "turn.completed": "turn.completed",
        "turnCompleted": "turn.completed",
        "turn/failed": "turn.failed",
        "turn.failed": "turn.failed",
        "turn/error": "turn.failed",
        "turnFailed": "turn.failed",
        "item/started": "item.started",
        "item.started": "item.started",
        "item/completed": "item.completed",
        "item.completed": "item.completed",
        "item/updated": "item.updated",
        "item.updated": "item.updated",
    }
    return aliases.get(event_type, event_type.replace("/", "."))


def _normalize_item_type(item_type: Any) -> Any:
    if not isinstance(item_type, str):
        return item_type
    aliases = {
        "agentMessage": "agent_message",
        "agent_message": "agent_message",
        "commandExecution": "command_execution",
        "command_execution": "command_execution",
        "fileChange": "file_change",
        "file_change": "file_change",
        "mcpToolCall": "mcp_tool_call",
        "mcp_tool_call": "mcp_tool_call",
        "toolCall": "tool_call",
        "tool_call": "tool_call",
    }
    return aliases.get(item_type, item_type)


def _extract_item_text(item: Any) -> str:
    text = _extract_attr_or_key(item, "text")
    if isinstance(text, str) and text.strip():
        return text

    content = _extract_attr_or_key(item, "content")
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, list):
        parts = [str(part) for part in content if isinstance(part, str) and part.strip()]
        if parts:
            return "\n".join(parts)

    summary = _extract_attr_or_key(item, "summary")
    if isinstance(summary, list):
        parts = [str(part) for part in summary if isinstance(part, str) and part.strip()]
        if parts:
            return "\n".join(parts)
    if isinstance(summary, str) and summary.strip():
        return summary

    message = _extract_attr_or_key(item, "message")
    if isinstance(message, str) and message.strip():
        return message

    command = _extract_attr_or_key(item, "command")
    if isinstance(command, str) and command.strip():
        return command

    return ""


def _extract_turn_items_and_usage(persisted: Any) -> tuple[list[dict[str, Any]], Any]:
    response = _to_serializable(persisted)
    if not isinstance(response, dict):
        return [], None

    thread = response.get("thread")
    if not isinstance(thread, dict):
        return [], None

    turns = thread.get("turns")
    if not isinstance(turns, list) or not turns:
        return [], None

    last_turn = turns[-1]
    if not isinstance(last_turn, dict):
        return [], None

    items_raw = last_turn.get("items")
    items = [_item_to_dict(item) for item in items_raw] if isinstance(items_raw, list) else []
    usage = last_turn.get("usage")
    return items, usage


def _first_string(obj: dict[str, Any], *keys: str) -> Optional[str]:
    for key in keys:
        value = obj.get(key)
        if isinstance(value, str):
            return value
    return None
