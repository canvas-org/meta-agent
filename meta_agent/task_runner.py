from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, List, Optional, Union

if TYPE_CHECKING:
    from claude_agent_sdk import ClaudeAgentOptions

from meta_agent.benchmark import Task
from meta_agent.run_context import RunContext


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

    agents_md = Path(config_dir) / "AGENTS.md"
    if agents_md.exists():
        shutil.copy2(agents_md, work_dir / "AGENTS.md")

    codex_config_src = Path(config_dir) / ".codex"
    codex_config_dst = work_dir / ".codex"
    if codex_config_src.is_dir():
        shutil.copytree(codex_config_src, codex_config_dst, dirs_exist_ok=True)

    cmd = ["codex", "exec", "--full-auto", "--json"]
    if model:
        cmd.extend(["--model", model])
    cmd.append(task.instruction)

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
