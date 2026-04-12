from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, List, Optional, Union

if TYPE_CHECKING:
    from claude_agent_sdk import ClaudeAgentOptions

from meta_agent.benchmark import Task
from meta_agent.run_context import RunContext

_HARNESS_FILES = {"AGENTS.md", "CLAUDE.md"}
_HARNESS_GLOBS = {"*.sh"}
_HARNESS_DIRS = {".codex", ".claude"}
_CODEX_HOOK_EVENTS_UNSUPPORTED = {"PreToolUse", "PostToolUse"}
_CODEX_HOOKS_NATIVE_SUPPORT: Optional[bool] = None
_CODEX_SDK_DIR = Path(__file__).resolve().parent / "codex_sdk"
_CODEX_SDK_RUNNER = _CODEX_SDK_DIR / "run_codex_sdk.mjs"
_CODEX_SDK_READY: Optional[bool] = None


def _copy_harness_files(config_dir: str, work_dir: Path) -> None:
    """Copy harness files into the task work directory."""
    src = Path(config_dir)
    if not src.is_dir():
        return

    for name in _HARNESS_FILES:
        f = src / name
        if f.is_file():
            shutil.copy2(f, work_dir / name)

    for pattern in _HARNESS_GLOBS:
        for f in src.glob(pattern):
            if f.is_file():
                shutil.copy2(f, work_dir / f.name)

    for name in _HARNESS_DIRS:
        d = src / name
        if d.is_dir():
            shutil.copytree(d, work_dir / name, dirs_exist_ok=True)


def _ensure_claude_md(work_dir: Path) -> None:
    """Claude Code reads CLAUDE.md, not AGENTS.md.

    If only AGENTS.md exists, synthesize CLAUDE.md that imports it.
    """
    claude_md = work_dir / "CLAUDE.md"
    agents_md = work_dir / "AGENTS.md"
    if claude_md.exists():
        return
    if agents_md.exists():
        claude_md.write_text("@AGENTS.md\n")


def _build_codex_exec_cmd(prompt: str, model: str) -> list[str]:
    cmd = ["codex", "exec", "--full-auto", "--json", "--skip-git-repo-check"]
    if model:
        cmd.extend(["--model", model])
    cmd.append(prompt)
    return cmd


def _build_codex_sdk_cmd() -> list[str]:
    return ["node", str(_CODEX_SDK_RUNNER)]


def _ensure_codex_sdk_ready() -> tuple[bool, str]:
    global _CODEX_SDK_READY

    if _CODEX_SDK_READY is True:
        return True, ""

    if not _CODEX_SDK_DIR.is_dir():
        _CODEX_SDK_READY = False
        return False, f"Codex SDK directory missing at {_CODEX_SDK_DIR}"
    if not _CODEX_SDK_RUNNER.is_file():
        _CODEX_SDK_READY = False
        return False, f"Codex SDK runner missing at {_CODEX_SDK_RUNNER}"

    sdk_pkg_dir = _CODEX_SDK_DIR / "node_modules" / "@openai" / "codex-sdk"
    if sdk_pkg_dir.is_dir():
        _CODEX_SDK_READY = True
        return True, ""

    install = subprocess.run(
        ["npm", "install"],
        cwd=str(_CODEX_SDK_DIR),
        capture_output=True,
        text=True,
        timeout=180,
    )
    if install.returncode != 0:
        _CODEX_SDK_READY = False
        stderr = (install.stderr or "").strip()
        stdout = (install.stdout or "").strip()
        detail = stderr or stdout or f"exit={install.returncode}"
        return False, f"Failed to install @openai/codex-sdk: {detail}"

    if not sdk_pkg_dir.is_dir():
        _CODEX_SDK_READY = False
        return False, f"npm install succeeded but SDK package missing at {sdk_pkg_dir}"

    _CODEX_SDK_READY = True
    return True, ""


def _run_codex_sdk_turn(
    prompt: str,
    model: str,
    work_dir: Path,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    ready, reason = _ensure_codex_sdk_ready()
    if not ready:
        return subprocess.CompletedProcess(
            args=_build_codex_sdk_cmd(),
            returncode=1,
            stdout="",
            stderr=reason,
        )

    payload: dict[str, Any] = {
        "prompt": prompt,
        "workingDirectory": str(work_dir),
        "timeoutSec": timeout,
        "skipGitRepoCheck": True,
    }
    if model:
        payload["model"] = model

    sdk_result = subprocess.run(
        _build_codex_sdk_cmd(),
        cwd=str(work_dir),
        capture_output=True,
        text=True,
        input=json.dumps(payload),
        timeout=timeout + 30,
    )

    stdout_raw = (sdk_result.stdout or "").strip()
    try:
        sdk_payload = json.loads(stdout_raw) if stdout_raw else {}
    except json.JSONDecodeError:
        sdk_payload = {}

    sdk_ok = isinstance(sdk_payload, dict) and bool(sdk_payload.get("ok"))
    if sdk_result.returncode != 0 or not sdk_ok:
        err_detail = ""
        if isinstance(sdk_payload, dict):
            raw_err = sdk_payload.get("error")
            if isinstance(raw_err, str):
                err_detail = raw_err.strip()
        stderr = "\n".join(
            part for part in [err_detail, (sdk_result.stderr or "").strip()] if part
        ).strip()
        return subprocess.CompletedProcess(
            args=sdk_result.args,
            returncode=sdk_result.returncode or 1,
            stdout="",
            stderr=stderr,
        )

    final_response = sdk_payload.get("finalResponse", "")
    if not isinstance(final_response, str):
        final_response = ""
    items = sdk_payload.get("items")
    if not isinstance(items, list):
        items = []

    trace_events: list[dict[str, Any]] = []
    if final_response.strip():
        trace_events.append({"type": "message", "content": final_response})
    for item in items:
        if isinstance(item, dict):
            trace_events.append({"type": "item.completed", "item": item})

    trace_stdout = "\n".join(json.dumps(e) for e in trace_events)
    if trace_stdout:
        trace_stdout += "\n"
    return subprocess.CompletedProcess(
        args=sdk_result.args,
        returncode=0,
        stdout=trace_stdout,
        stderr=(sdk_result.stderr or "").strip(),
    )


def _codex_native_hooks_supported() -> bool:
    global _CODEX_HOOKS_NATIVE_SUPPORT

    if os.environ.get("META_AGENT_FORCE_CODEX_HOOK_EMULATION", "").strip() == "1":
        return False
    if os.environ.get("META_AGENT_ASSUME_CODEX_NATIVE_HOOKS", "").strip() == "1":
        return True
    if _CODEX_HOOKS_NATIVE_SUPPORT is not None:
        return _CODEX_HOOKS_NATIVE_SUPPORT

    try:
        result = subprocess.run(
            ["codex", "features", "list"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            _CODEX_HOOKS_NATIVE_SUPPORT = False
        else:
            _CODEX_HOOKS_NATIVE_SUPPORT = (
                re.search(r"^codex_hooks\s+", result.stdout, re.MULTILINE) is not None
            )
    except (OSError, subprocess.SubprocessError):
        _CODEX_HOOKS_NATIVE_SUPPORT = False

    return _CODEX_HOOKS_NATIVE_SUPPORT


def _load_codex_hooks_config(work_dir: Path) -> dict[str, Any]:
    hooks_path = work_dir / ".codex" / "hooks.json"
    if not hooks_path.is_file():
        return {}
    try:
        payload = json.loads(hooks_path.read_text())
    except json.JSONDecodeError:
        return {}
    hooks = payload.get("hooks")
    return hooks if isinstance(hooks, dict) else {}


def _extract_last_agent_message_from_codex_trace(raw_trace: str) -> Optional[str]:
    last_message: Optional[str] = None
    for line in raw_trace.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "message":
            content = event.get("content")
            if isinstance(content, str) and content.strip():
                last_message = content
            continue
        if event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        if item.get("type") != "agent_message":
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            last_message = text
    return last_message


def _hook_group_matches(event_name: str, group: dict[str, Any], payload: dict[str, Any]) -> bool:
    matcher = group.get("matcher")
    if not isinstance(matcher, str) or matcher in {"", "*"}:
        return True

    if event_name == "SessionStart":
        target = str(payload.get("source", ""))
    elif event_name in {"PreToolUse", "PostToolUse"}:
        target = str(payload.get("tool_name", ""))
    else:
        # For UserPromptSubmit/Stop the matcher is ignored.
        return True

    try:
        return re.search(matcher, target) is not None
    except re.error:
        return False


def _run_codex_hook_event(
    hooks_config: dict[str, Any],
    event_name: str,
    work_dir: Path,
    model: str,
    payload: dict[str, Any],
) -> list[str]:
    groups = hooks_config.get(event_name)
    if not isinstance(groups, list):
        return []

    failures: list[str] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        if not _hook_group_matches(event_name, group, payload):
            continue
        handlers = group.get("hooks")
        if not isinstance(handlers, list):
            continue
        for handler in handlers:
            if not isinstance(handler, dict):
                continue
            handler_type = handler.get("type", "command")
            if handler_type != "command":
                continue
            command = handler.get("command")
            if not isinstance(command, str) or not command.strip():
                continue

            timeout_raw = handler.get("timeout", handler.get("timeoutSec", 600))
            try:
                timeout_sec = max(1, int(timeout_raw))
            except (TypeError, ValueError):
                timeout_sec = 600

            hook_input = {
                "session_id": payload.get("session_id", uuid.uuid4().hex),
                "transcript_path": str(work_dir / "trace.jsonl"),
                "cwd": str(work_dir),
                "hook_event_name": event_name,
                "model": model,
                **payload,
            }
            try:
                hook_result = subprocess.run(
                    command,
                    shell=True,
                    cwd=str(work_dir),
                    capture_output=True,
                    text=True,
                    input=json.dumps(hook_input),
                    timeout=timeout_sec,
                )
                if hook_result.returncode != 0:
                    stderr = (hook_result.stderr or "").strip()
                    stdout = (hook_result.stdout or "").strip()
                    detail = stderr or stdout or f"exit={hook_result.returncode}"
                    failures.append(
                        f"{event_name}: command `{command}` failed ({detail})"
                    )
            except subprocess.TimeoutExpired:
                failures.append(
                    f"{event_name}: command `{command}` timed out after {timeout_sec}s"
                )

    return failures


def run_codex_cli_with_hooks(
    prompt: str,
    model: str,
    work_dir: Path,
    timeout: int,
) -> tuple[subprocess.CompletedProcess[str], list[str], list[str]]:
    """Run Codex CLI and emulate hooks when native hooks are unavailable."""
    cmd = _build_codex_exec_cmd(prompt, model)
    hooks_config = _load_codex_hooks_config(work_dir)
    emulate_hooks = bool(hooks_config) and not _codex_native_hooks_supported()
    hook_failures: list[str] = []
    hook_warnings: list[str] = []

    if emulate_hooks:
        unsupported = sorted(
            event for event in _CODEX_HOOK_EVENTS_UNSUPPORTED if event in hooks_config
        )
        if unsupported:
            hook_warnings.append(
                "Codex hook emulation does not support events: "
                + ", ".join(unsupported)
            )

        pre_payload = {"source": "startup", "prompt": prompt, "turn_id": uuid.uuid4().hex}
        hook_failures.extend(
            _run_codex_hook_event(hooks_config, "SessionStart", work_dir, model, pre_payload)
        )
        hook_failures.extend(
            _run_codex_hook_event(hooks_config, "UserPromptSubmit", work_dir, model, pre_payload)
        )

        if hook_failures:
            return (
                subprocess.CompletedProcess(
                    args=cmd,
                    returncode=1,
                    stdout="",
                    stderr="\n".join(hook_failures),
                ),
                hook_failures,
                hook_warnings,
            )

    result = subprocess.run(
        cmd,
        cwd=str(work_dir),
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    if emulate_hooks:
        last_message = _extract_last_agent_message_from_codex_trace(result.stdout)
        stop_payload = {
            "turn_id": uuid.uuid4().hex,
            "stop_hook_active": False,
            "last_assistant_message": last_message,
        }
        hook_failures.extend(
            _run_codex_hook_event(hooks_config, "Stop", work_dir, model, stop_payload)
        )

    return result, hook_failures, hook_warnings


def _run_codex_sdk_with_hooks_native(
    prompt: str,
    model: str,
    work_dir: Path,
    timeout: int,
) -> Any:
    """Run Codex SDK via the shared Python runner with hook emulation.

    Returns a CodexSdkRunResult with hook data folded in.
    """
    from meta_agent.codex_sdk_runner import CodexSdkRunResult, run_codex_sdk_turn

    hooks_config = _load_codex_hooks_config(work_dir)
    emulate_hooks = bool(hooks_config) and not _codex_native_hooks_supported()
    hook_failures: list[str] = []
    hook_warnings: list[str] = []

    if emulate_hooks:
        unsupported = sorted(
            event for event in _CODEX_HOOK_EVENTS_UNSUPPORTED if event in hooks_config
        )
        if unsupported:
            hook_warnings.append(
                "Codex hook emulation does not support events: "
                + ", ".join(unsupported)
            )

        pre_payload = {"source": "startup", "prompt": prompt, "turn_id": uuid.uuid4().hex}
        hook_failures.extend(
            _run_codex_hook_event(hooks_config, "SessionStart", work_dir, model, pre_payload)
        )
        hook_failures.extend(
            _run_codex_hook_event(hooks_config, "UserPromptSubmit", work_dir, model, pre_payload)
        )

        if hook_failures:
            return CodexSdkRunResult(
                exit_code=1,
                stderr="\n".join(hook_failures),
                hook_failures=list(hook_failures),
                hook_warnings=list(hook_warnings),
            )

    sdk_result = run_codex_sdk_turn(
        prompt=prompt,
        model=model,
        cwd=str(work_dir),
        timeout_sec=timeout,
    )

    if emulate_hooks:
        stop_payload = {
            "turn_id": uuid.uuid4().hex,
            "stop_hook_active": False,
            "last_assistant_message": sdk_result.final_response,
        }
        hook_failures.extend(
            _run_codex_hook_event(hooks_config, "Stop", work_dir, model, stop_payload)
        )

    sdk_result.hook_failures = hook_failures
    sdk_result.hook_warnings = hook_warnings
    return sdk_result


def run_codex_sdk_with_hooks(
    prompt: str,
    model: str,
    work_dir: Path,
    timeout: int,
) -> tuple[subprocess.CompletedProcess[str], list[str], list[str]]:
    """Backward-compatible wrapper — returns (CompletedProcess, failures, warnings).

    Legacy callers (e.g. ArtifactsBench adapter) still expect this signature.
    Will be removed when all adapters migrate to the shared runner directly.
    """
    sdk_result = _run_codex_sdk_with_hooks_native(prompt, model, work_dir, timeout)
    compat = subprocess.CompletedProcess(
        args=_build_codex_sdk_cmd(),
        returncode=sdk_result.exit_code,
        stdout=sdk_result.normalized_trace_jsonl,
        stderr=sdk_result.stderr,
    )
    return compat, sdk_result.hook_failures, sdk_result.hook_warnings


def run_command(
    cmd: Union[str, List[str]], cwd: Path, timeout: int = 300
) -> subprocess.CompletedProcess[str]:
    if isinstance(cmd, list):
        return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
    return subprocess.run(cmd, shell=True, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)


def load_config_module(config_path: str) -> Any:
    spec = importlib.util.spec_from_file_location("harness_config", config_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load config module from {config_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "build_options"):
        raise AttributeError(
            f"Config module {config_path} must export a build_options(ctx) function"
        )
    return module


def serialize_block(block: Any) -> dict[str, Any]:
    from claude_agent_sdk import TextBlock, ThinkingBlock, ToolUseBlock, ToolResultBlock

    if isinstance(block, TextBlock):
        return {"type": "TextBlock", "text": block.text}
    if isinstance(block, ThinkingBlock):
        return {"type": "ThinkingBlock", "thinking": block.thinking}
    if isinstance(block, ToolUseBlock):
        return {
            "type": "ToolUseBlock",
            "id": block.id,
            "name": block.name,
            "input": block.input,
        }
    if isinstance(block, ToolResultBlock):
        content = block.content
        if isinstance(content, list):
            content = [str(c) if not isinstance(c, (str, dict)) else c for c in content]
        return {
            "type": "ToolResultBlock",
            "tool_use_id": block.tool_use_id,
            "content": content,
            "is_error": block.is_error,
        }
    return {"type": type(block).__name__, "raw": str(block)[:500]}


def serialize_message(message: Any) -> dict[str, Any]:
    from claude_agent_sdk import AssistantMessage, ResultMessage, UserMessage, SystemMessage

    msg_type = type(message).__name__
    record: dict[str, Any] = {"type": msg_type, "timestamp": time.time()}

    if isinstance(message, AssistantMessage):
        record["content"] = [serialize_block(b) for b in message.content]
        record["model"] = message.model
        if message.usage:
            record["usage"] = message.usage
    elif isinstance(message, ResultMessage):
        record["subtype"] = message.subtype
        record["is_error"] = message.is_error
        record["num_turns"] = message.num_turns
        record["duration_ms"] = message.duration_ms
        record["total_cost_usd"] = message.total_cost_usd
        record["session_id"] = message.session_id
        record["usage"] = message.usage
        record["result"] = message.result
    elif isinstance(message, UserMessage):
        content = message.content
        if isinstance(content, str):
            record["content"] = content
        elif isinstance(content, list):
            record["content"] = [serialize_block(b) for b in content]
        else:
            record["content"] = str(content)[:500]
    elif isinstance(message, SystemMessage):
        record["subtype"] = message.subtype
    else:
        record["raw"] = str(message)[:500]

    return record


@dataclass
class TaskResult:
    task_name: str
    passed: bool
    reward: float
    cost_usd: Optional[float]
    num_turns: Optional[int]
    duration_ms: Optional[int]
    wall_time_s: Optional[float]
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    cache_tokens: Optional[int]
    session_id: Optional[str]
    work_dir: str
    verify_exit_code: int
    verify_output: str


async def run_task(
    task: Task, config_path: str, model: str, work_dir: Path
) -> TaskResult:
    from claude_agent_sdk import query, ResultMessage

    config_module = load_config_module(config_path)
    ctx = RunContext(cwd=str(work_dir), model=model, task_instruction=task.instruction)
    options = config_module.build_options(ctx)

    perm_override = os.environ.get("CLAUDE_PERMISSION_MODE")
    if perm_override and hasattr(options, "permission_mode") and options.permission_mode != perm_override:
        print(f"  [TASK] permission_mode overridden: {options.permission_mode} -> {perm_override} (CLAUDE_PERMISSION_MODE env var)")
        options.permission_mode = perm_override

    trace_path = work_dir / "trace.jsonl"
    start_time = time.time()

    num_turns = None
    duration_ms = None
    cost_usd = None
    session_id = None
    wall_time_s = None
    input_tokens = None
    output_tokens = None
    cache_tokens = None
    final_result: dict[str, Any] = {}

    print(f"  [TASK] {task.name}: model={options.model}, perm={options.permission_mode}, cwd={options.cwd}")

    with open(trace_path, "w") as trace_file:
        async for message in query(prompt=task.instruction, options=options):
            record = serialize_message(message)
            trace_file.write(json.dumps(record) + "\n")
            trace_file.flush()

            if isinstance(message, ResultMessage):
                num_turns = message.num_turns
                duration_ms = message.duration_ms
                cost_usd = message.total_cost_usd
                session_id = message.session_id
                wall_time_s = time.time() - start_time
                usage = message.usage if isinstance(message.usage, dict) else {}
                input_tokens = usage.get("input_tokens")
                output_tokens = usage.get("output_tokens")
                cache_tokens = usage.get("cache_read_input_tokens")
                final_result = {
                    "num_turns": num_turns,
                    "duration_ms": duration_ms,
                    "total_cost_usd": cost_usd,
                    "session_id": session_id,
                    "wall_time_s": wall_time_s,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cache_tokens": cache_tokens,
                }

    (work_dir / "result.json").write_text(json.dumps(final_result, indent=2))

    verify_result = run_command(task.verify, cwd=work_dir, timeout=task.timeout)

    return TaskResult(
        task_name=task.name,
        passed=verify_result.returncode == 0,
        reward=1.0 if verify_result.returncode == 0 else 0.0,
        cost_usd=cost_usd,
        num_turns=num_turns,
        duration_ms=duration_ms,
        wall_time_s=wall_time_s,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_tokens=cache_tokens,
        session_id=session_id,
        work_dir=str(work_dir),
        verify_exit_code=verify_result.returncode,
        verify_output=(verify_result.stdout or "") + (verify_result.stderr or ""),
    )


async def run_task_codex(
    task: Task,
    config_dir: str,
    model: str,
    work_dir: Path,
) -> TaskResult:
    """Run a single task using Codex CLI."""
    start = time.time()

    _copy_harness_files(config_dir, work_dir)

    trace_path = work_dir / "trace.jsonl"
    result, hook_failures, hook_warnings = run_codex_cli_with_hooks(
        prompt=task.instruction,
        model=model,
        work_dir=work_dir,
        timeout=task.timeout,
    )
    trace_path.write_text(result.stdout or "")

    verify_result = run_command(task.verify, cwd=work_dir, timeout=task.timeout)
    codex_ok = result.returncode == 0
    hooks_ok = len(hook_failures) == 0
    passed = verify_result.returncode == 0 and codex_ok and hooks_ok

    verify_exit_code = verify_result.returncode
    if verify_exit_code == 0 and not codex_ok:
        verify_exit_code = result.returncode or 1
    if verify_exit_code == 0 and not hooks_ok:
        verify_exit_code = 1

    verify_output = (verify_result.stdout or "") + (verify_result.stderr or "")
    if not codex_ok:
        verify_output += (
            f"\n[codex] exit={result.returncode}\n"
            f"{(result.stderr or '').strip()}\n"
        )
    if hook_warnings or hook_failures:
        verify_output += "\n[codex_hooks]\n"
        for warning in hook_warnings:
            verify_output += f"warning: {warning}\n"
        for failure in hook_failures:
            verify_output += f"failure: {failure}\n"

    wall_time = time.time() - start

    return TaskResult(
        task_name=task.name,
        passed=passed,
        reward=1.0 if passed else 0.0,
        cost_usd=None,
        num_turns=None,
        duration_ms=int(wall_time * 1000),
        wall_time_s=wall_time,
        input_tokens=None,
        output_tokens=None,
        cache_tokens=None,
        session_id=None,
        work_dir=str(work_dir),
        verify_exit_code=verify_exit_code,
        verify_output=verify_output,
    )


async def run_task_codex_sdk(
    task: Task,
    config_dir: str,
    model: str,
    work_dir: Path,
) -> TaskResult:
    """Run a single task using the shared Python Codex SDK runner."""
    start = time.time()

    _copy_harness_files(config_dir, work_dir)

    sdk_result = _run_codex_sdk_with_hooks_native(
        prompt=task.instruction,
        model=model,
        work_dir=work_dir,
        timeout=task.timeout,
    )

    (work_dir / "trace.raw.jsonl").write_text(sdk_result.raw_events_jsonl)
    (work_dir / "trace.jsonl").write_text(sdk_result.normalized_trace_jsonl)
    (work_dir / "final_response.txt").write_text(sdk_result.final_response)

    verify_result = run_command(task.verify, cwd=work_dir, timeout=task.timeout)
    codex_ok = sdk_result.exit_code == 0
    hooks_ok = len(sdk_result.hook_failures) == 0
    passed = verify_result.returncode == 0 and codex_ok and hooks_ok

    verify_exit_code = verify_result.returncode
    if verify_exit_code == 0 and not codex_ok:
        verify_exit_code = sdk_result.exit_code or 1
    if verify_exit_code == 0 and not hooks_ok:
        verify_exit_code = 1

    verify_output = (verify_result.stdout or "") + (verify_result.stderr or "")
    if not codex_ok:
        verify_output += (
            f"\n[codex_sdk] exit={sdk_result.exit_code}\n"
            f"{sdk_result.stderr.strip()}\n"
        )
    if sdk_result.hook_warnings or sdk_result.hook_failures:
        verify_output += "\n[codex_hooks]\n"
        for warning in sdk_result.hook_warnings:
            verify_output += f"warning: {warning}\n"
        for failure in sdk_result.hook_failures:
            verify_output += f"failure: {failure}\n"

    wall_time = time.time() - start

    return TaskResult(
        task_name=task.name,
        passed=passed,
        reward=1.0 if passed else 0.0,
        cost_usd=None,
        num_turns=None,
        duration_ms=int(wall_time * 1000),
        wall_time_s=wall_time,
        input_tokens=None,
        output_tokens=None,
        cache_tokens=None,
        session_id=None,
        work_dir=str(work_dir),
        verify_exit_code=verify_exit_code,
        verify_output=verify_output,
    )


async def run_task_claude_code(
    task: Task,
    config_dir: str,
    model: str,
    work_dir: Path,
) -> TaskResult:
    """Run a single task using Claude Code CLI (claude --print)."""
    start = time.time()

    _copy_harness_files(config_dir, work_dir)
    _ensure_claude_md(work_dir)

    permission_mode = os.environ.get("CLAUDE_PERMISSION_MODE", "bypassPermissions").strip()
    cmd = [
        "claude", "--print", "--verbose",
        "--output-format", "stream-json",
        "-p", task.instruction,
    ]
    if model:
        cmd.extend(["--model", model])
    if permission_mode:
        cmd.extend(["--permission-mode", permission_mode])

    trace_path = work_dir / "trace.jsonl"
    result = subprocess.run(
        cmd, cwd=str(work_dir),
        capture_output=True, text=True,
        timeout=task.timeout,
    )

    trace_path.write_text(result.stdout)

    verify_result = run_command(task.verify, cwd=work_dir, timeout=task.timeout)
    passed = verify_result.returncode == 0

    wall_time = time.time() - start

    return TaskResult(
        task_name=task.name,
        passed=passed,
        reward=1.0 if passed else 0.0,
        cost_usd=None,
        num_turns=None,
        duration_ms=int(wall_time * 1000),
        wall_time_s=wall_time,
        input_tokens=None,
        output_tokens=None,
        cache_tokens=None,
        session_id=None,
        work_dir=str(work_dir),
        verify_exit_code=verify_result.returncode,
        verify_output=(verify_result.stdout or "") + (verify_result.stderr or ""),
    )
