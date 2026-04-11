#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import statistics
import tempfile
from pathlib import Path
import time
from typing import Any, List, Optional

from meta_agent.benchmark import Benchmark, Task, load_benchmark, TauBackend
from meta_agent.paths import get_benchmark_candidates_dir, get_workspace_root
from meta_agent.task_runner import (
    TaskResult,
    run_command,
    run_task,
    run_task_claude_code,
    run_task_codex,
    run_task_codex_sdk,
)

def get_experience_dir(bench_name: str) -> Path:
    return get_benchmark_candidates_dir(bench_name)


def build_experience_dir(
    name: str, config_path: str, model: str, results: List[TaskResult],
    experience_dir: Optional[Path] = None,
) -> Path:
    base = experience_dir or (get_workspace_root() / "experience" / "candidates")
    candidate_dir = base / name
    per_task_dir = candidate_dir / "per_task"
    per_task_dir.mkdir(parents=True, exist_ok=True)

    src_config = Path(config_path)
    if src_config.is_dir():
        dest_config = candidate_dir
        if src_config.resolve() != dest_config.resolve():
            for item in src_config.iterdir():
                dest = dest_config / item.name
                if item.is_dir():
                    if dest.resolve() != item.resolve():
                        shutil.copytree(item, dest, dirs_exist_ok=True)
                elif dest.resolve() != item.resolve():
                    shutil.copy2(item, dest)
    else:
        dest_config = candidate_dir / "config.py"
        if src_config.resolve() != dest_config.resolve():
            shutil.copy2(config_path, dest_config)

    trials: List[dict[str, Any]] = []
    for r in results:
        trial: dict[str, Any] = {
            "task_name": r.task_name,
            "short_name": r.task_name,
            "reward": r.reward,
            "passed": r.passed,
            "cost_usd": r.cost_usd,
            "num_turns": r.num_turns,
            "duration_ms": r.duration_ms,
            "wall_time_s": r.wall_time_s,
            "input_tokens": r.input_tokens,
            "output_tokens": r.output_tokens,
            "cache_tokens": r.cache_tokens,
            "session_id": r.session_id,
            "trial_dir": r.work_dir,
        }
        trials.append(trial)

        task_result_path = per_task_dir / f"{r.task_name}.json"
        task_result_path.write_text(json.dumps(trial, indent=2))

        work_dir = Path(r.work_dir)
        trace_src = work_dir / "trace.jsonl"
        if trace_src.exists():
            shutil.copy2(trace_src, per_task_dir / f"{r.task_name}_trace.jsonl")
        result_src = work_dir / "result.json"
        if result_src.exists():
            shutil.copy2(result_src, per_task_dir / f"{r.task_name}_agent_result.json")
        judge_src = work_dir / "judge_feedback.md"
        if judge_src.exists():
            shutil.copy2(judge_src, per_task_dir / f"{r.task_name}_judge_feedback.md")

    n_tasks = len(trials)
    n_passed = sum(1 for t in trials if t["passed"])
    rewards = [t["reward"] for t in trials if t["reward"] is not None]
    costs = [t["cost_usd"] for t in trials if t["cost_usd"] is not None]
    turns = [t["num_turns"] for t in trials if t["num_turns"] is not None]

    scores: dict[str, Any] = {
        "name": name,
        "config_path": config_path,
        "model": model,
        "n_tasks": n_tasks,
        "n_passed": n_passed,
        "pass_rate": n_passed / n_tasks if n_tasks > 0 else 0.0,
        "mean_reward": statistics.mean(rewards) if rewards else None,
        "mean_cost_usd": statistics.mean(costs) if costs else None,
        "total_cost_usd": sum(costs) if costs else None,
        "median_turns": statistics.median(turns) if turns else None,
        "tasks_passed": [t["short_name"] for t in trials if t["passed"]],
        "tasks_failed": [t["short_name"] for t in trials if not t["passed"]],
    }
    (candidate_dir / "scores.json").write_text(json.dumps(scores, indent=2))

    lines = [
        f"# {name}",
        "",
        f"**Model:** {model}",
        f"**Config:** {config_path}",
        f"**Pass rate:** {n_passed}/{n_tasks} ({scores['pass_rate']:.1%})",
        f"**Total cost:** ${scores['total_cost_usd']:.4f}" if scores["total_cost_usd"] else "**Total cost:** N/A",
        f"**Median turns:** {scores['median_turns']}" if scores["median_turns"] else "**Median turns:** N/A",
        "",
        "## Per-task results",
        "",
        "| Task | Result | Cost | Turns |",
        "|------|--------|------|-------|",
    ]
    for t in sorted(trials, key=lambda x: x["short_name"]):
        status = "PASS" if t["passed"] else "FAIL"
        cost = f"${t['cost_usd']:.4f}" if t["cost_usd"] else "N/A"
        trns = str(t["num_turns"]) if t["num_turns"] else "N/A"
        lines.append(f"| {t['short_name']} | {status} | {cost} | {trns} |")

    (candidate_dir / "summary.md").write_text("\n".join(lines) + "\n")
    return candidate_dir


async def run_local_tasks(
    tasks: List[Task],
    config_path: str,
    model: str,
    concurrency: int,
    keep_workspaces: bool,
    keep_failed: bool,
    runtime: str = "claude_sdk",
) -> List[TaskResult]:
    sem = asyncio.Semaphore(concurrency)

    async def _run_one(task: Task) -> TaskResult:
        async with sem:
            tmp = Path(tempfile.mkdtemp(prefix=f"task_{task.name}_"))
            work_dir = tmp / task.name
            shutil.copytree(task.workspace, str(work_dir))

            if task.setup:
                run_command(task.setup, cwd=work_dir, timeout=task.timeout)

            if runtime == "codex_cli":
                result = await run_task_codex(task, config_path, model, work_dir)
            elif runtime == "codex_sdk":
                result = await run_task_codex_sdk(task, config_path, model, work_dir)
            elif runtime == "claude_code_cli":
                result = await run_task_claude_code(task, config_path, model, work_dir)
            else:
                result = await run_task(task, config_path, model, work_dir)

            if not keep_workspaces and not (keep_failed and not result.passed):
                shutil.rmtree(tmp, ignore_errors=True)
            return result

    return list(await asyncio.gather(*[_run_one(t) for t in tasks]))


def run_tau_tasks(
    benchmark: Benchmark,
    config_path: str,
    model: str,
    concurrency: int,
    task_filter: Optional[List[str]] = None,
) -> List[TaskResult]:
    """Run tau3-bench tasks in parallel through the Claude Agent SDK via MCP tools."""
    import importlib
    _sdk = importlib.import_module("benchmarks.tau3.sdk_adapter")

    tau_backend = benchmark.tau_backend
    assert tau_backend is not None

    domains = tau_backend.domains
    if task_filter:
        domains = [d for d in domains if d in task_filter]
        if not domains:
            domains = tau_backend.domains

    user_model: Optional[str] = tau_backend.user_model if tau_backend.user_model else None

    from tau2.runner import get_tasks as _get_tasks

    trace_dir = Path(tempfile.mkdtemp(prefix="tau_traces_"))

    task_list: list[tuple[str, Any]] = []
    for domain in domains:
        tasks = _get_tasks(domain)
        if tau_backend.task_ids:
            id_set = set(tau_backend.task_ids)
            tasks = [t for t in tasks if str(t.id) in id_set]
        for task in tasks:
            task_list.append((domain, task))

    if tau_backend.sample_size and tau_backend.sample_size < len(task_list):
        import random
        full_count = len(task_list)
        task_list = random.sample(task_list, tau_backend.sample_size)
        print(f"  [TAU] Sampled {len(task_list)} from {full_count} tasks")

    if tau_backend.task_ids and not task_list:
        sample_ids = [str(t.id) for t in _get_tasks(domains[0])[:3]]
        raise ValueError(
            f"task_ids filter matched 0 tasks. "
            f"Actual IDs look like: {sample_ids}"
        )

    n_total = len(task_list)
    print(f"  [TAU] Running {n_total} tasks across {len(domains)} domain(s), concurrency={concurrency}")

    sem = asyncio.Semaphore(concurrency)
    completed = 0
    running_passed = 0
    _lock = asyncio.Lock()

    TASK_TIMEOUT_S = 300
    MAX_RETRIES = 10
    RETRY_DELAY_S = 15

    async def _run_one(domain: str, task: Any) -> TaskResult:
        nonlocal completed, running_passed
        task_id = str(task.id)
        task_name = f"{domain}_{task_id}"

        last_err: Exception | None = None
        r = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with sem:
                    r = await asyncio.wait_for(
                        _sdk.run_tau_task_sdk(
                            domain=domain,
                            task_id=task_id,
                            config_path=config_path,
                            model=model,
                            user_model=user_model,
                            judge_model=tau_backend.judge_model,
                            judge_strategy=tau_backend.judge_strategy,
                        ),
                        timeout=TASK_TIMEOUT_S,
                    )
                break
            except asyncio.TimeoutError:
                last_err = asyncio.TimeoutError()
                break
            except Exception as e:
                last_err = e
                if attempt < MAX_RETRIES:
                    print(
                        f"  [{task_name}] Attempt {attempt}/{MAX_RETRIES} failed "
                        f"({type(e).__name__}), retrying in {RETRY_DELAY_S}s...",
                        flush=True,
                    )
                    await asyncio.sleep(RETRY_DELAY_S)
                    continue

        if r is None:
            async with _lock:
                completed += 1
                rate = running_passed / completed
                print(
                    f"  [{completed:>3}/{n_total}] ERROR  {task_name:<20}  "
                    f"{type(last_err).__name__}: {last_err}  pass_rate={rate:.0%}",
                    flush=True,
                )
            return TaskResult(
                task_name=task_name,
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
                work_dir="",
                verify_exit_code=1,
                verify_output=f"ERROR: {type(last_err).__name__}: {last_err}",
            )

        task_trace_dir = trace_dir / task_name
        task_trace_dir.mkdir(parents=True, exist_ok=True)
        with open(task_trace_dir / "trace.jsonl", "w") as f:
            for msg in r.messages:
                f.write(json.dumps(msg) + "\n")
            grading: dict[str, Any] = {"type": "grading", "gold_reward": r.gold_reward, "reward": r.reward}
            if tau_backend.judge_model:
                grading["judge_model"] = tau_backend.judge_model
            f.write(json.dumps(grading) + "\n")

        async with _lock:
            completed += 1
            if r.passed:
                running_passed += 1
            mark = "PASS" if r.passed else "FAIL"
            rate = running_passed / completed
            gold_tag = ""
            if tau_backend.judge_model:
                gm = "G+" if r.gold_reward > 0 else "G-"
                jm = "J+" if r.passed else "J-"
                gold_tag = f"  {jm} {gm}"
            print(
                f"  [{completed:>3}/{n_total}] {mark}  {task_name:<20} "
                f"turns={r.num_turns:<3} cost=${r.cost_usd or 0:.3f}  "
                f"{r.duration_s:.0f}s  pass_rate={rate:.0%}{gold_tag}",
                flush=True,
            )

        return TaskResult(
            task_name=task_name,
            passed=r.passed,
            reward=r.reward,
            cost_usd=r.cost_usd,
            num_turns=r.num_turns,
            duration_ms=int(r.duration_s * 1000),
            wall_time_s=r.duration_s,
            input_tokens=None,
            output_tokens=None,
            cache_tokens=None,
            session_id=r.session_id,
            work_dir=str(task_trace_dir),
            verify_exit_code=0 if r.passed else 1,
            verify_output="",
        )

    async def _run_all() -> List[TaskResult]:
        coros = [_run_one(domain, task) for domain, task in task_list]
        return list(await asyncio.gather(*coros))

    return asyncio.run(_run_all())


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a config on a benchmark")
    parser.add_argument("--benchmark", required=True, help="Path to benchmark YAML")
    parser.add_argument("--config", required=True, help="Path to config module")
    parser.add_argument("--name", required=True, help="Candidate name in experience store")
    parser.add_argument("--model", default="claude-haiku-4-5", help="Model identifier")
    parser.add_argument("--fast", action="store_true", help="Use benchmark's fast_tasks subset")
    parser.add_argument("--tasks", default=None, help="Comma-separated task names to run")
    parser.add_argument("--concurrency", type=int, default=4, help="Parallel task count")
    parser.add_argument("--keep-workspaces", action="store_true", help="Keep all temp dirs")
    parser.add_argument("--keep-failed", action="store_true", help="Keep temp dirs for failed tasks")
    parser.add_argument("--dry-run", action="store_true", help="Print config and exit")
    args = parser.parse_args()

    bench = load_benchmark(args.benchmark)
    experience_dir = get_experience_dir(bench.name)

    task_filter: Optional[List[str]] = None
    if args.fast:
        task_filter = bench.fast_tasks
        selected = [t for t in bench.tasks if t.name in bench.fast_tasks]
    elif args.tasks:
        task_filter = [n.strip() for n in args.tasks.split(",")]
        selected = [t for t in bench.tasks if t.name in set(task_filter)]
    else:
        selected = bench.tasks

    task_display = task_filter if task_filter else [t.name for t in selected] if selected else "all"
    print(f"[EVAL] Benchmark: {bench.name} (type={bench.type})")
    print(f"[EVAL] Config: {args.config}")
    print(f"[EVAL] Name: {args.name}")
    print(f"[EVAL] Model: {args.model}")
    print(f"[EVAL] Tasks: {task_display}")
    print(f"[EVAL] Concurrency: {args.concurrency}")

    if args.dry_run:
        print("[EVAL] Dry run — exiting.")
        return

    eval_start = time.time()
    print(f"[EVAL] Starting at {time.strftime('%H:%M:%S')}...")
    print()

    if bench.type == "local":
        results = asyncio.run(run_local_tasks(
            tasks=selected,
            config_path=args.config,
            model=args.model,
            concurrency=args.concurrency,
            keep_workspaces=args.keep_workspaces,
            keep_failed=args.keep_failed,
            runtime=bench.runtime,
        ))
    elif bench.type in ("tau", "tau3"):
        results = run_tau_tasks(
            benchmark=bench,
            config_path=args.config,
            model=args.model,
            concurrency=args.concurrency,
            task_filter=task_filter,
        )
    elif bench.type == "swebench_m":
        from benchmarks.swebench_m.adapter import run_swebench_tasks
        results = asyncio.run(run_swebench_tasks(
            benchmark=bench,
            config_path=args.config,
            model=args.model,
            concurrency=args.concurrency,
            task_filter=task_filter,
            runtime=bench.runtime,
        ))
    elif bench.type == "artifacts_bench":
        if os.environ.get("ARTIFACTS_USE_MODAL") == "1":
            from benchmarks.artifacts_bench.adapter import _run_artifacts_tasks_modal_sync
            results = _run_artifacts_tasks_modal_sync(
                benchmark=bench,
                config_path=args.config,
                model=args.model,
                concurrency=args.concurrency,
                task_filter=task_filter,
                runtime=bench.runtime,
            )
        else:
            from benchmarks.artifacts_bench.adapter import run_artifacts_tasks
            results = asyncio.run(run_artifacts_tasks(
                benchmark=bench,
                config_path=args.config,
                model=args.model,
                concurrency=args.concurrency,
                task_filter=task_filter,
                runtime=bench.runtime,
            ))
    else:
        raise ValueError(f"Unknown benchmark type: {bench.type}")

    elapsed = time.time() - eval_start
    candidate_dir = build_experience_dir(
        name=args.name,
        config_path=args.config,
        model=args.model,
        results=results,
        experience_dir=experience_dir,
    )

    scores = json.loads((candidate_dir / "scores.json").read_text())
    n_passed = scores["n_passed"]
    n_tasks = scores["n_tasks"]
    pass_rate = scores["pass_rate"]
    total_cost = scores.get("total_cost_usd") or 0

    print(f"\n{'='*60}")
    print(f"  {args.name}  —  {n_passed}/{n_tasks} ({pass_rate:.0%})")
    print(f"{'='*60}")
    print()
    for r in sorted(results, key=lambda x: x.task_name):
        mark = "PASS" if r.passed else "FAIL"
        cost = f"${r.cost_usd:.3f}" if r.cost_usd else "  N/A"
        turns = f"{r.num_turns:>3}" if r.num_turns else "N/A"
        dur = f"{(r.duration_ms / 1000):.0f}s" if r.duration_ms else "N/A"
        print(f"  {mark}  {r.task_name:<30}  {turns} turns  {cost}  {dur}")
    print()
    cost_str = f"${total_cost:.4f}" if total_cost else "N/A"
    turns_str = str(scores.get("median_turns", "N/A"))
    print(f"  Total cost: {cost_str}  |  Median turns: {turns_str}  |  Wall time: {elapsed:.0f}s")
    print(f"  Saved to: {candidate_dir}")
    print()


if __name__ == "__main__":
    main()
