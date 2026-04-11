"""ArtifactsBench adapter.

Runs Codex on ArtifactsBench tasks, renders HTML via Playwright,
and grades with a Gemini VLM judge using per-task checklists.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from meta_agent.benchmark import Benchmark
from meta_agent.paths import get_workspace_root
from meta_agent.task_runner import TaskResult


@dataclass
class ArtifactsBenchTask:
    index: int
    question: str
    checklist: list
    task_class: str
    difficulty: str


def load_artifacts_tasks(
    dataset_path: str,
    task_indexes: Optional[List[int]] = None,
) -> List[ArtifactsBenchTask]:
    import pandas as pd

    df = pd.read_parquet(dataset_path)
    if task_indexes:
        df = df[df["index"].isin(task_indexes)]
    tasks: List[ArtifactsBenchTask] = []
    for _, row in df.iterrows():
        checklist = (
            list(row["checklist"])
            if hasattr(row["checklist"], "__iter__")
            else json.loads(row["checklist"])
        )
        tasks.append(
            ArtifactsBenchTask(
                index=int(row["index"]),
                question=row["question"],
                checklist=checklist,
                task_class=row["class"],
                difficulty=row["difficulty"],
            )
        )
    return tasks


def extract_answer_from_codex_output(raw_output: str, work_dir: Path) -> str:
    """Extract code answer from Codex output or generated files."""
    if "<html" in raw_output.lower() or "```html" in raw_output:
        return raw_output

    _skip = {"node_modules", ".codex", ".git", "__pycache__", "venv", ".venv"}
    html_files = [
        f for f in work_dir.glob("**/*.html")
        if f.is_file() and not any(s in f.parts for s in _skip) and f.name != "AGENTS.md"
    ]

    if html_files:
        main_html = html_files[0]
        html_content = main_html.read_text()

        css_files = [
            f for f in work_dir.glob("**/*.css")
            if f.is_file() and not any(s in f.parts for s in _skip)
        ]
        js_files = [
            f for f in work_dir.glob("**/*.js")
            if f.is_file() and not any(s in f.parts for s in _skip)
        ]

        answer = f"```html\n{html_content}\n```"
        for css in css_files:
            answer += f"\n```css\n{css.read_text()}\n```"
        for js in js_files:
            answer += f"\n```javascript\n{js.read_text()}\n```"
        return answer

    return raw_output


def _init_workdir(config_dir: str, work_dir: Path, runtime: str) -> None:
    """Copy all harness files and git-init the work directory."""
    from meta_agent.task_runner import _copy_harness_files, _ensure_claude_md
    _copy_harness_files(config_dir, work_dir)
    if runtime == "claude_code_cli":
        _ensure_claude_md(work_dir)
    subprocess.run(["git", "init"], cwd=str(work_dir), capture_output=True)
    subprocess.run(["git", "add", "."], cwd=str(work_dir), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init", "--allow-empty"],
                   cwd=str(work_dir), capture_output=True)


def _build_cmd(task: ArtifactsBenchTask, model: str, runtime: str) -> list[str]:
    if runtime == "codex_cli":
        cmd = ["codex", "exec", "--full-auto", "--json", "--skip-git-repo-check"]
        if model:
            cmd.extend(["--model", model])
        cmd.append(task.question)
    elif runtime in {"claude_code_cli", "claude_sdk"}:
        cmd = [
            "claude", "--print", "--verbose",
            "--output-format", "stream-json",
            "--permission-mode", "bypassPermissions",
            "--max-turns", "30",
            "-p", task.question,
        ]
        if model:
            cmd.extend(["--model", model])
    else:
        cmd = []
    return cmd


_MAX_AGENT_RETRIES = 3


def _extract_text_from_jsonl(raw: str) -> str | None:
    """Extract text content from Codex --json or Claude stream-json output."""
    parts: list[str] = []
    for line in raw.strip().splitlines():
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if event.get("type") == "message" and isinstance(event.get("content"), str):
            parts.append(event["content"])
        elif event.get("type") == "assistant":
            for block in event.get("message", {}).get("content", []):
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block["text"])
    return "\n".join(parts) if parts else None


def _run_agent_sync(
    task: ArtifactsBenchTask,
    config_dir: str,
    model: str,
    work_dir: Path,
    timeout: int = 300,
    runtime: str = "codex_cli",
) -> tuple[str, str]:
    """Run Codex or Claude CLI with retries on empty output."""
    from meta_agent.task_runner import run_codex_cli_with_hooks, run_codex_sdk_with_hooks

    _init_workdir(config_dir, work_dir, runtime)
    cmd = _build_cmd(task, model, runtime)
    stdin_cfg = subprocess.DEVNULL if runtime == "claude_sdk" else None

    raw_output = ""
    for attempt in range(_MAX_AGENT_RETRIES):
        try:
            if runtime == "codex_cli":
                result, hook_failures, hook_warnings = run_codex_cli_with_hooks(
                    prompt=task.question,
                    model=model,
                    work_dir=work_dir,
                    timeout=timeout,
                )
                raw_output = result.stdout or ""
                stderr_text = (result.stderr or "").strip()
                if hook_warnings or hook_failures:
                    hook_diag = "\n".join(
                        [f"warning: {w}" for w in hook_warnings]
                        + [f"failure: {f}" for f in hook_failures]
                    )
                    stderr_text = (stderr_text + "\n[codex_hooks]\n" + hook_diag).strip()
            elif runtime == "codex_sdk":
                result, hook_failures, hook_warnings = run_codex_sdk_with_hooks(
                    prompt=task.question,
                    model=model,
                    work_dir=work_dir,
                    timeout=timeout,
                )
                raw_output = result.stdout or ""
                stderr_text = (result.stderr or "").strip()
                if hook_warnings or hook_failures:
                    hook_diag = "\n".join(
                        [f"warning: {w}" for w in hook_warnings]
                        + [f"failure: {f}" for f in hook_failures]
                    )
                    stderr_text = (stderr_text + "\n[codex_hooks]\n" + hook_diag).strip()
            else:
                if not cmd:
                    raise ValueError(f"Unsupported runtime: {runtime}")
                result = subprocess.run(
                    cmd, cwd=str(work_dir), capture_output=True,
                    text=True, timeout=timeout, stdin=stdin_cfg,
                )
                raw_output = result.stdout or ""
                stderr_text = (result.stderr or "").strip()

            if raw_output.strip():
                break

            stderr_snippet = stderr_text[:500]
            print(f"  [AGENT] Empty output for task {task.index} "
                  f"(attempt {attempt+1}/{_MAX_AGENT_RETRIES}, "
                  f"exit={result.returncode}, stderr={stderr_snippet!r})")
            if attempt < _MAX_AGENT_RETRIES - 1:
                time.sleep(5 * (attempt + 1))

        except subprocess.TimeoutExpired:
            print(f"  [AGENT] Timeout for task {task.index} "
                  f"(attempt {attempt+1}/{_MAX_AGENT_RETRIES}, {timeout}s)")
            if attempt < _MAX_AGENT_RETRIES - 1:
                time.sleep(5)

    (work_dir / "trace.jsonl").write_text(raw_output)

    text_output = _extract_text_from_jsonl(raw_output)
    answer = extract_answer_from_codex_output(text_output or raw_output, work_dir)
    return answer, raw_output


async def run_codex_task(
    task: ArtifactsBenchTask,
    config_dir: str,
    model: str,
    work_dir: Path,
    timeout: int = 300,
) -> tuple[str, str]:
    """Run Codex on one task in a thread so it doesn't block the event loop."""
    return await asyncio.to_thread(
        _run_agent_sync, task, config_dir, model, work_dir, timeout, "codex_cli"
    )


async def run_codex_sdk_task(
    task: ArtifactsBenchTask,
    config_dir: str,
    model: str,
    work_dir: Path,
    timeout: int = 300,
) -> tuple[str, str]:
    """Run Codex SDK on one task in a thread so it doesn't block the event loop."""
    return await asyncio.to_thread(
        _run_agent_sync, task, config_dir, model, work_dir, timeout, "codex_sdk"
    )


async def run_claude_task(
    task: ArtifactsBenchTask,
    config_dir: str,
    model: str,
    work_dir: Path,
    timeout: int = 300,
) -> tuple[str, str]:
    """Run Claude Agent SDK natively — no CLI, works on Modal."""
    from claude_agent_sdk import (
        query, ClaudeAgentOptions,
        AssistantMessage, ResultMessage, TextBlock, ToolUseBlock,
    )

    _init_workdir(config_dir, work_dir, "claude_sdk")  # SDK doesn't need .codex dir

    agents_md = work_dir / "AGENTS.md"
    system_prompt = agents_md.read_text() if agents_md.exists() else ""

    options = ClaudeAgentOptions(
        model=model,
        cwd=str(work_dir),
        system_prompt=system_prompt,
        permission_mode="bypassPermissions",
        max_turns=30,
    )

    raw_parts: list[str] = []
    trace: list[dict] = []

    try:
        async for message in query(prompt=task.question, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        raw_parts.append(block.text)
                    elif isinstance(block, ToolUseBlock):
                        trace.append({"tool": block.name})
            elif isinstance(message, ResultMessage):
                trace.append({
                    "result": True,
                    "turns": message.num_turns,
                    "cost": message.total_cost_usd,
                })
    except Exception as e:
        trace.append({"error": str(e)})

    raw_output = "\n".join(raw_parts)
    (work_dir / "trace.jsonl").write_text(
        "\n".join(json.dumps(t) for t in trace)
    )
    answer = extract_answer_from_codex_output(raw_output, work_dir)
    return answer, raw_output


def render_and_screenshot(
    answer: str,
    task_index: int,
    screenshots_dir: Path,
    num_screenshots: int = 3,
) -> Optional[List[Path]]:
    """Extract HTML from answer, render in browser, take screenshots."""
    from benchmarks.artifacts_bench.lib.extract_ans import (
        extract_last_html_or_svg_block,
    )
    from benchmarks.artifacts_bench.lib.code_parser import extract_html
    from benchmarks.artifacts_bench.lib.screenshot import capture_html_screenshots

    extracted = extract_last_html_or_svg_block(answer)
    if extracted["type"] not in ("html", "svg"):
        return None

    if extracted["type"] == "html":
        try:
            html_code = extract_html(answer)
        except Exception:
            html_code = extracted["content"]
    else:
        html_code = extracted["content"]

    html_path = screenshots_dir / f"html_{task_index}.html"
    html_path.write_text(html_code, encoding="utf-8")

    img_paths = [
        screenshots_dir / f"screenshot_{task_index}_{i + 1}.png"
        for i in range(num_screenshots)
    ]
    capture_html_screenshots(
        str(html_path), [str(p) for p in img_paths], num_screenshots
    )

    existing = [p for p in img_paths if p.exists()]
    return existing if existing else None


def judge_artifact(
    task: ArtifactsBenchTask,
    answer: str,
    screenshot_paths: Optional[List[Path]],
) -> tuple[Optional[float], str]:
    """Score an artifact using Gemini as VLM judge. Returns (score 0-100, judge_reasoning)."""
    from google import genai
    from google.genai import types

    from benchmarks.artifacts_bench.lib.judge_prompt import (
        get_prompt_mllm_checklist,
    )
    from benchmarks.artifacts_bench.lib.extract_ans import extract_mllm_overall

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable not set")

    client = genai.Client(api_key=api_key)

    checklist_str = json.dumps(list(task.checklist))
    truncated_answer = answer[:30000] if len(answer) > 30000 else answer
    prompt_text = get_prompt_mllm_checklist(checklist_str, task.question, truncated_answer)

    content_parts: list[types.Part] = [types.Part.from_text(text=prompt_text)]

    if screenshot_paths:
        for img_path in screenshot_paths:
            with open(img_path, "rb") as f:
                img_bytes = f.read()
            content_parts.append(
                types.Part.from_bytes(data=img_bytes, mime_type="image/png")
            )

    from pydantic import BaseModel, Field
    from typing import List

    class DimensionReview(BaseModel):
        score: int = Field(description="Score 0-10 for this dimension")
        title: str = Field(description="Title of the evaluation dimension")
        review: str = Field(description="Detailed review for this dimension")

    class JudgeResult(BaseModel):
        dimensions: List[DimensionReview] = Field(description="Per-dimension scores and reviews")
        overall_score: int = Field(description="Overall score 0-100 aggregating all dimensions")

    max_retries = 8
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=content_parts,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": JudgeResult,
                },
            )

            if not response.text:
                reason = getattr(response.candidates[0], "finish_reason", "unknown") if response.candidates else "no_candidates"
                if attempt < max_retries - 1:
                    print(f"  [JUDGE] Empty response for task {task.index} (attempt {attempt+1}, reason={reason}), retrying...")
                    time.sleep(3 * (attempt + 1))
                    continue
                print(f"  [JUDGE] Empty response for task {task.index} after {max_retries} retries (reason={reason})")
                return None, ""

            parsed = json.loads(response.text)
            return float(parsed["overall_score"]), response.text

        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            if attempt < max_retries - 1:
                print(f"  [JUDGE] Parse error for task {task.index} (attempt {attempt+1}): {e}, retrying...")
                time.sleep(2)
                continue
            print(f"  [JUDGE] Parse error for task {task.index} after {max_retries} retries: {e}")
            return None, response.text if response and response.text else ""

        except Exception as e:
            if attempt < max_retries - 1:
                delay = 3 * (attempt + 1)
                time.sleep(delay)
                continue
            print(f"  [JUDGE] Gemini API error for task {task.index} (after {max_retries} retries): {e}")
            return None, ""


async def run_artifacts_tasks(
    benchmark: Benchmark,
    config_path: str,
    model: str,
    concurrency: int,
    task_filter: Optional[List[str]] = None,
    runtime: str = "codex_cli",
) -> List[TaskResult]:
    """Run agent on ArtifactsBench tasks, render, and judge."""
    if os.environ.get("ARTIFACTS_USE_MODAL") == "1":
        return _run_artifacts_tasks_modal_sync(
            benchmark, config_path, model, concurrency, task_filter, runtime,
        )

    import concurrent.futures
    loop = asyncio.get_event_loop()
    loop.set_default_executor(concurrent.futures.ThreadPoolExecutor(max_workers=concurrency * 3))

    backend = benchmark.artifacts_backend
    assert backend is not None

    tasks = load_artifacts_tasks(
        dataset_path=str(get_workspace_root() / backend.dataset_path),
        task_indexes=backend.task_indexes,
    )
    if task_filter:
        filter_set = {int(x) for x in task_filter}
        tasks = [t for t in tasks if t.index in filter_set]

    n_total = len(tasks)
    print(f"  [ARTIFACTS] Running {n_total} tasks, concurrency={concurrency}")

    screenshots_dir = get_workspace_root() / "data" / "artifacts_screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    sem = asyncio.Semaphore(concurrency)
    all_results: List[TaskResult] = []
    completed = 0
    _lock = asyncio.Lock()

    async def _run_one(task: ArtifactsBenchTask) -> None:
        nonlocal completed
        async with sem:
            work_dir = Path(tempfile.mkdtemp(prefix=f"artifacts_{task.index}_"))
            try:
                if runtime == "claude_sdk":
                    answer, _trace = await run_claude_task(
                        task, config_path, model, work_dir, backend.timeout
                    )
                elif runtime == "claude_code_cli":
                    answer, _trace = await asyncio.to_thread(
                        _run_agent_sync, task, config_path, model, work_dir, backend.timeout, "claude_code_cli"
                    )
                elif runtime == "codex_sdk":
                    answer, _trace = await run_codex_sdk_task(
                        task, config_path, model, work_dir, backend.timeout
                    )
                elif runtime == "codex_cli":
                    answer, _trace = await run_codex_task(
                        task, config_path, model, work_dir, backend.timeout
                    )
                else:
                    raise ValueError(f"Unsupported artifacts runtime: {runtime}")

                img_paths = await asyncio.to_thread(
                    render_and_screenshot,
                    answer, task.index, screenshots_dir,
                )

                score, judge_reasoning = await asyncio.to_thread(
                    judge_artifact, task, answer, img_paths,
                )

                # Save judge reasoning so the proposer can read it
                judge_path = work_dir / "judge_feedback.md"
                judge_path.write_text(judge_reasoning or "")

                reward = score / 100.0 if score is not None else 0.0

                result = TaskResult(
                    task_name=str(task.index),
                    passed=score is not None and score >= 50,
                    reward=reward,
                    cost_usd=None,
                    num_turns=None,
                    duration_ms=None,
                    wall_time_s=0.0,
                    input_tokens=None,
                    output_tokens=None,
                    cache_tokens=None,
                    session_id=None,
                    work_dir=str(work_dir),
                    verify_exit_code=0 if score is not None else 1,
                    verify_output=f"score={score}" if score is not None else "no_score",
                )

            except Exception as e:
                result = TaskResult(
                    task_name=str(task.index),
                    passed=False,
                    reward=0.0,
                    cost_usd=None,
                    num_turns=None,
                    duration_ms=0,
                    wall_time_s=0.0,
                    input_tokens=None,
                    output_tokens=None,
                    cache_tokens=None,
                    session_id=None,
                    work_dir=str(work_dir),
                    verify_exit_code=1,
                    verify_output=f"Error: {e}",
                )

            async with _lock:
                all_results.append(result)
                completed += 1
                avg_so_far = sum(r.reward for r in all_results) / len(all_results) * 100
                status = "DONE" if result.reward > 0 else "FAIL"
                print(
                    f"  [{completed:>3}/{n_total}] {status}  "
                    f"task={task.index:<6} {result.verify_output:<16} "
                    f"avg={avg_so_far:.1f}",
                    flush=True,
                )

    await asyncio.gather(*[_run_one(t) for t in tasks])

    n_scored = sum(1 for r in all_results if r.reward > 0)
    avg_reward = (
        sum(r.reward for r in all_results) / len(all_results) if all_results else 0
    )
    print(
        f"  [ARTIFACTS] Complete: {n_scored}/{n_total} scored, avg reward={avg_reward:.3f}"
    )
    return all_results


def _run_artifacts_tasks_modal_sync(
    benchmark: Benchmark,
    config_path: str,
    model: str,
    concurrency: int,
    task_filter: Optional[List[str]] = None,
    runtime: str = "codex_cli",
) -> List[TaskResult]:
    """Run tasks via Modal — each task in its own cloud container (sync)."""
    from benchmarks.artifacts_bench.modal_runner import get_remote_fn
    run_task = get_remote_fn()

    backend = benchmark.artifacts_backend
    assert backend is not None

    tasks = load_artifacts_tasks(
        dataset_path=str(get_workspace_root() / backend.dataset_path),
        task_indexes=backend.task_indexes,
    )
    if task_filter:
        filter_set = {int(x) for x in task_filter}
        tasks = [t for t in tasks if t.index in filter_set]

    n_total = len(tasks)
    print(f"  [ARTIFACTS-MODAL] Running {n_total} tasks on Modal")

    agents_md_path = Path(config_path) / "AGENTS.md"
    config_agents_md = agents_md_path.read_text() if agents_md_path.exists() else ""
    claude_md_path = Path(config_path) / "CLAUDE.md"
    config_claude_md = claude_md_path.read_text() if claude_md_path.exists() else ""

    config_codex_dir_files: Optional[dict[str, bytes]] = None
    codex_dir = Path(config_path) / ".codex"
    if codex_dir.is_dir():
        config_codex_dir_files = {}
        for f in codex_dir.rglob("*"):
            if f.is_file():
                config_codex_dir_files[str(f.relative_to(codex_dir))] = f.read_bytes()

    task_datas = [
        {"index": t.index, "question": t.question, "checklist": t.checklist,
         "task_class": t.task_class, "difficulty": t.difficulty}
        for t in tasks
    ]

    results_list: List[TaskResult] = []
    completed = 0

    shared_kwargs = {
        "config_agents_md": config_agents_md,
        "config_claude_md": config_claude_md,
        "config_codex_dir_files": config_codex_dir_files,
        "model": model,
        "runtime": runtime,
        "task_timeout": backend.timeout,
    }

    for result_dict in run_task.map(task_datas, kwargs=shared_kwargs):
        completed += 1
        work_dir = Path(tempfile.mkdtemp(prefix=f"modal_result_{result_dict['task_name']}_"))
        (work_dir / "judge_feedback.md").write_text(result_dict.get("judge_feedback", ""))
        (work_dir / "trace.jsonl").write_text(result_dict.get("trace", ""))

        result = TaskResult(
            task_name=result_dict["task_name"],
            passed=result_dict["passed"],
            reward=result_dict["reward"],
            cost_usd=None, num_turns=None, duration_ms=None,
            wall_time_s=0.0,
            input_tokens=None, output_tokens=None, cache_tokens=None,
            session_id=None,
            work_dir=str(work_dir),
            verify_exit_code=result_dict["verify_exit_code"],
            verify_output=result_dict["verify_output"],
        )
        results_list.append(result)

        avg_so_far = sum(r.reward for r in results_list) / len(results_list) * 100
        status = "DONE" if result.reward > 0 else "FAIL"
        print(
            f"  [{completed:>3}/{n_total}] {status}  "
            f"task={result.task_name:<6} {result.verify_output:<16} "
            f"avg={avg_so_far:.1f}",
            flush=True,
        )

    n_scored = sum(1 for r in results_list if r.reward > 0)
    avg_reward = sum(r.reward for r in results_list) / len(results_list) if results_list else 0
    print(f"  [ARTIFACTS-MODAL] Complete: {n_scored}/{n_total} scored, avg reward={avg_reward:.3f}")
    return results_list
