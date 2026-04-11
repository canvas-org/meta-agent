"""SWE-bench Multimodal adapter.

Runs Codex on SWE-bench tasks, captures git diffs, then grades via the
official SWE-bench Docker harness (swebench.harness.run_evaluation).
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from meta_agent.benchmark import Benchmark
from meta_agent.paths import get_workspace_root
from meta_agent.task_runner import (
    TaskResult,
    _copy_harness_files,
    run_codex_cli_with_hooks,
    run_codex_sdk_with_hooks,
)


@dataclass
class SWEBenchTask:
    repo: str
    instance_id: str
    base_commit: str
    patch: str
    test_patch: str
    problem_statement: str
    hints_text: str
    image_assets: str
    version: str
    FAIL_TO_PASS: str
    PASS_TO_PASS: str


_SWEBENCH_COLUMNS = [f.name for f in SWEBenchTask.__dataclass_fields__.values()]


def load_swebench_tasks(
    dataset_path: str,
    task_ids: Optional[List[str]] = None,
) -> List[SWEBenchTask]:
    import pandas as pd

    df = pd.read_parquet(dataset_path, columns=_SWEBENCH_COLUMNS)
    if task_ids:
        df = df[df["instance_id"].isin(task_ids)]
    return [SWEBenchTask(**row) for _, row in df.iterrows()]


def setup_workspace(task: SWEBenchTask, work_dir: Path, cache_dir: Path) -> None:
    """Clone repo (cached) and checkout the base commit."""
    repo_key = task.repo.replace("/", "__")
    cached_repo = cache_dir / repo_key

    if not cached_repo.exists():
        subprocess.run(
            ["git", "clone", f"https://github.com/{task.repo}.git", str(cached_repo)],
            check=True,
            capture_output=True,
        )

    shutil.copytree(str(cached_repo), str(work_dir), dirs_exist_ok=True)

    subprocess.run(
        ["git", "checkout", "-f", task.base_commit],
        cwd=str(work_dir),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "clean", "-fdx"],
        cwd=str(work_dir),
        check=True,
        capture_output=True,
    )


def build_prompt(task: SWEBenchTask) -> str:
    prompt = f"Fix the following GitHub issue:\n\n{task.problem_statement}"

    if task.image_assets:
        try:
            assets = (
                json.loads(task.image_assets)
                if isinstance(task.image_assets, str)
                else task.image_assets
            )
            urls = assets.get("problem_statement", [])
            if urls:
                prompt += "\n\nRelevant images:\n"
                for url in urls:
                    prompt += f"- {url}\n"
        except (json.JSONDecodeError, AttributeError):
            pass

    return prompt


async def run_single_task(
    task: SWEBenchTask,
    config_dir: str,
    model: str,
    work_dir: Path,
    runtime: str = "codex_cli",
    timeout: int = 600,
) -> tuple[TaskResult, dict[str, str]]:
    """Run Codex on one task. Returns (TaskResult, prediction_dict)."""
    start = time.time()

    _copy_harness_files(config_dir, work_dir)

    prompt = build_prompt(task)
    try:
        if runtime == "codex_sdk":
            result, hook_failures, hook_warnings = run_codex_sdk_with_hooks(
                prompt=prompt,
                model=model,
                work_dir=work_dir,
                timeout=timeout,
            )
        elif runtime == "codex_cli":
            result, hook_failures, hook_warnings = run_codex_cli_with_hooks(
                prompt=prompt,
                model=model,
                work_dir=work_dir,
                timeout=timeout,
            )
        else:
            raise ValueError(f"Unsupported runtime for swebench_m: {runtime}")
        (work_dir / "trace.jsonl").write_text(result.stdout or "")
    except subprocess.TimeoutExpired:
        result = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=f"TIMEOUT after {timeout}s")
        hook_failures = []
        hook_warnings = []
        (work_dir / "trace.jsonl").write_text("")

    diff_result = subprocess.run(
        ["git", "diff"],
        cwd=str(work_dir),
        capture_output=True,
        text=True,
    )
    model_patch = diff_result.stdout

    wall_time = time.time() - start

    prediction: dict[str, str] = {
        "instance_id": task.instance_id,
        "model_name_or_path": model or "codex",
        "model_patch": model_patch,
    }

    task_result = TaskResult(
        task_name=task.instance_id,
        passed=False,
        reward=0.0,
        cost_usd=None,
        num_turns=None,
        duration_ms=int(wall_time * 1000),
        wall_time_s=wall_time,
        input_tokens=None,
        output_tokens=None,
        cache_tokens=None,
        session_id=None,
        work_dir=str(work_dir),
        verify_exit_code=1 if hook_failures else max(1, result.returncode),
        verify_output=(
            f"patch_length={len(model_patch)}"
            + (
                "\n[codex_hooks]\n"
                + "\n".join([f"warning: {w}" for w in hook_warnings] + [f"failure: {f}" for f in hook_failures])
                if hook_warnings or hook_failures else ""
            )
        ),
    )

    return task_result, prediction


def grade_predictions(
    predictions: List[dict[str, str]],
    run_id: str,
    max_workers: int = 4,
) -> dict[str, bool]:
    """Run the SWE-bench Docker grader. Returns {instance_id: passed}."""
    predictions_path = Path(tempfile.mktemp(suffix=".jsonl"))
    with open(predictions_path, "w") as f:
        for pred in predictions:
            f.write(json.dumps(pred) + "\n")

    subprocess.run(
        [
            sys.executable,
            "-m",
            "swebench.harness.run_evaluation",
            "--dataset_name",
            "princeton-nlp/SWE-bench_Multimodal",
            "--split",
            "dev",
            "--predictions_path",
            str(predictions_path),
            "--max_workers",
            str(max_workers),
            "--run_id",
            run_id,
        ],
        check=True,
    )

    results: dict[str, bool] = {}
    for candidate in [
        Path(f"evaluation_results/{run_id}/results.json"),
        Path(f"logs/run_evaluation/{run_id}/report.json"),
    ]:
        if candidate.exists():
            report = json.loads(candidate.read_text())
            for instance_id in report.get("resolved", []):
                results[instance_id] = True
            break

    predictions_path.unlink(missing_ok=True)
    return results


async def run_swebench_tasks(
    benchmark: Benchmark,
    config_path: str,
    model: str,
    concurrency: int,
    task_filter: Optional[List[str]] = None,
    runtime: str = "codex_cli",
) -> List[TaskResult]:
    """Run Codex on SWE-bench tasks, then grade with the official Docker harness."""
    backend = benchmark.swebench_backend
    assert backend is not None

    tasks = load_swebench_tasks(
        dataset_path=str(get_workspace_root() / backend.dataset_path),
        task_ids=backend.task_ids,
    )
    if task_filter:
        filter_set = set(task_filter)
        tasks = [t for t in tasks if t.instance_id in filter_set]

    n_total = len(tasks)
    print(f"  [SWEBENCH] Running {n_total} tasks, concurrency={concurrency}")

    cache_dir = get_workspace_root() / "data" / "repo_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    sem = asyncio.Semaphore(concurrency)
    all_results: List[TaskResult] = []
    all_predictions: List[dict[str, str]] = []
    completed = 0
    _lock = asyncio.Lock()

    async def _run_one(task: SWEBenchTask) -> None:
        nonlocal completed
        async with sem:
            work_dir = Path(tempfile.mkdtemp(prefix=f"swebench_{task.instance_id}_"))
            try:
                setup_workspace(task, work_dir, cache_dir)
                result, prediction = await run_single_task(
                    task, config_path, model, work_dir, runtime, backend.timeout
                )
                async with _lock:
                    all_results.append(result)
                    all_predictions.append(prediction)
                    completed += 1
                    patch_len = len(prediction["model_patch"])
                    print(
                        f"  [{completed:>3}/{n_total}] DONE  {task.instance_id:<40} "
                        f"patch={patch_len} chars  {result.wall_time_s:.0f}s",
                        flush=True,
                    )
            except Exception as e:
                async with _lock:
                    all_results.append(
                        TaskResult(
                            task_name=task.instance_id,
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
                    )
                    all_predictions.append(
                        {
                            "instance_id": task.instance_id,
                            "model_name_or_path": model or "codex",
                            "model_patch": "",
                        }
                    )
                    completed += 1
                    print(
                        f"  [{completed:>3}/{n_total}] ERROR {task.instance_id:<40} "
                        f"{type(e).__name__}: {e}",
                        flush=True,
                    )

    await asyncio.gather(*[_run_one(t) for t in tasks])

    print(f"  [SWEBENCH] Grading {len(all_predictions)} predictions via Docker...")
    run_id = f"meta_agent_{int(time.time())}"
    grading_results = grade_predictions(all_predictions, run_id, max_workers=concurrency)

    n_passed = 0
    for result in all_results:
        passed = grading_results.get(result.task_name, False)
        result.passed = passed
        result.reward = 1.0 if passed else 0.0
        result.verify_exit_code = 0 if passed else 1
        if passed:
            n_passed += 1

    print(f"  [SWEBENCH] Grading complete: {n_passed}/{n_total} resolved")
    return all_results
