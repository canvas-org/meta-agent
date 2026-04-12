#!/usr/bin/env python3
from __future__ import annotations

"""Run the harness optimizer outer loop on Modal with persistent state.

Usage examples:
  modal run meta_agent/cloud_outer_loop.py --run-id smoke-a1 --do-smoke
  modal run --detach meta_agent/cloud_outer_loop.py --run-id exp-a1 --detach-run
  modal run meta_agent/cloud_outer_loop.py --run-id exp-a1 --do-status
"""

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

import modal


_FILE_PATH = Path(__file__).resolve()
LOCAL_PROJECT_ROOT = _FILE_PATH.parent.parent

APP_NAME = "harness-optimizer-cloud-loop"
RUNS_VOLUME_NAME = "harness-optimizer-runs-v1"
RUNS_VOLUME_MOUNT = "/persistent"
PROJECT_MOUNT = Path("/workspace/Harness-Optimizer")
PROJECT_IMAGE_COPY = Path("/opt/Harness-Optimizer")
LOCAL_MODAL_SOURCES_PATH = LOCAL_PROJECT_ROOT / "experience" / ".modal_sources.json"

RUNS_VOLUME = modal.Volume.from_name(RUNS_VOLUME_NAME, create_if_missing=True, version=2)

RUNTIME_SECRETS = [
    modal.Secret.from_name("codex-key-added", required_keys=["CODEX_API_KEY"]),
    modal.Secret.from_name("gemini-key", required_keys=["GEMINI_API_KEY"]),
]

_DOCKERFILE = str(LOCAL_PROJECT_ROOT / "benchmarks" / "artifacts_bench" / "Dockerfile.modal")

image = (
    modal.Image.from_dockerfile(_DOCKERFILE)
    .pip_install(
        "typing_extensions>=4.15",
        "claude-agent-sdk>=0.1.53",
        "pandas>=2.0",
        "pyarrow",
        "google-genai>=1.0",
        "pydantic>=2.0",
        "pyyaml>=6.0",
        "httpx>=0.27",
    )
    .env(
        {
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "PYTHONUNBUFFERED": "1",
        }
    )
    .add_local_dir(
        str(LOCAL_PROJECT_ROOT / "meta_agent"),
        str(PROJECT_IMAGE_COPY / "meta_agent"),
        copy=True,
        ignore=["**/__pycache__/**", "**/*.pyc"],
    )
    .add_local_dir(
        str(LOCAL_PROJECT_ROOT / "benchmarks"),
        str(PROJECT_IMAGE_COPY / "benchmarks"),
        copy=True,
        ignore=["**/__pycache__/**", "**/*.pyc"],
    )
    .add_local_dir(
        str(LOCAL_PROJECT_ROOT / "configs"),
        str(PROJECT_IMAGE_COPY / "configs"),
        copy=True,
        ignore=["**/__pycache__/**", "**/*.pyc"],
    )
    .add_local_file(
        str(LOCAL_PROJECT_ROOT / "data" / "artifacts_bench.parquet"),
        str(PROJECT_IMAGE_COPY / "data" / "artifacts_bench.parquet"),
        copy=True,
    )
    .add_local_file(
        str(LOCAL_PROJECT_ROOT / "SKILL.md"),
        str(PROJECT_IMAGE_COPY / "SKILL.md"),
        copy=True,
    )
    .add_local_file(
        str(LOCAL_PROJECT_ROOT / "SKILL_codex.md"),
        str(PROJECT_IMAGE_COPY / "SKILL_codex.md"),
        copy=True,
    )
)

app = modal.App(APP_NAME)

RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,120}$")
EVO_NAME_RE = re.compile(r"^evo_(\d{3})$")


class PreflightError(RuntimeError):
    """Raised when required runtime checks fail before launch."""


def _validate_run_id(run_id: str) -> None:
    if not RUN_ID_RE.match(run_id):
        raise ValueError(
            "Invalid run_id. Use letters, numbers, dot, underscore, dash (max 120 chars)."
        )


def _run_root(run_id: str) -> Path:
    return Path(RUNS_VOLUME_MOUNT) / "runs" / run_id


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
        return
    if path.is_dir():
        shutil.rmtree(path)


def _ensure_symlink(link_path: Path, target_path: Path) -> None:
    if link_path.is_symlink() and link_path.resolve() == target_path.resolve():
        return
    if link_path.exists() or link_path.is_symlink():
        _remove_path(link_path)
    link_path.symlink_to(target_path)


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def _save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))


def _infer_benchmark_name_from_config_local(benchmark_path: str) -> str:
    config_path = Path(benchmark_path)
    if not config_path.is_absolute():
        config_path = (LOCAL_PROJECT_ROOT / benchmark_path).resolve()

    fallback = config_path.stem
    try:
        for raw_line in config_path.read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or not line.startswith("name:"):
                continue
            value = line.split(":", 1)[1].strip().strip("'\"")
            if value:
                return value
    except Exception:
        return fallback
    return fallback


def _register_modal_source_local(run_id: str, benchmark_path: str) -> None:
    benchmark_name = _infer_benchmark_name_from_config_local(benchmark_path)
    payload = _load_json(LOCAL_MODAL_SOURCES_PATH, {})
    if not isinstance(payload, dict):
        payload = {}

    benchmarks = payload.get("benchmarks")
    if not isinstance(benchmarks, dict):
        benchmarks = {}
        payload["benchmarks"] = benchmarks

    benchmarks[benchmark_name] = {
        "runId": run_id,
        "updatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    _save_json(LOCAL_MODAL_SOURCES_PATH, payload)


def _prepare_workspace(run_id: str) -> tuple[Path, Path]:
    _validate_run_id(run_id)
    run_root = _run_root(run_id)
    (run_root / "experience").mkdir(parents=True, exist_ok=True)
    (run_root / "jobs").mkdir(parents=True, exist_ok=True)
    (run_root / "logs").mkdir(parents=True, exist_ok=True)
    (run_root / "artifacts").mkdir(parents=True, exist_ok=True)

    # Rebuild ephemeral workspace from image copy on each invocation.
    if PROJECT_MOUNT.exists() or PROJECT_MOUNT.is_symlink():
        _remove_path(PROJECT_MOUNT)
    shutil.copytree(PROJECT_IMAGE_COPY, PROJECT_MOUNT, dirs_exist_ok=True)

    _ensure_symlink(PROJECT_MOUNT / "experience", run_root / "experience")
    _ensure_symlink(PROJECT_MOUNT / "jobs", run_root / "jobs")
    return PROJECT_MOUNT, run_root


def _resolve_workspace_path(workspace_root: Path, raw_path: str) -> Path:
    p = Path(raw_path)
    if p.is_absolute():
        return p
    return (workspace_root / p).resolve()


def _ensure_workspace_on_syspath(workspace_root: Path) -> None:
    workspace_str = str(workspace_root)
    if workspace_str not in sys.path:
        sys.path.insert(0, workspace_str)


def _collect_candidate_scores(candidates_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not candidates_dir.exists():
        return rows

    for candidate in sorted(candidates_dir.iterdir()):
        if not candidate.is_dir():
            continue
        scores_path = candidate / "scores.json"
        if not scores_path.exists():
            continue
        score = _load_json(scores_path, None)
        if not isinstance(score, dict):
            continue
        score["name"] = score.get("name") or candidate.name
        rows.append(score)

    rows.sort(
        key=lambda r: (
            float(r.get("pass_rate", 0.0)),
            -float(r.get("total_cost_usd", 10**9) or 10**9),
        ),
        reverse=True,
    )
    return rows


def _build_tasks_from_scores(score: dict[str, Any]) -> list[dict[str, Any]]:
    raw_passed = score.get("tasks_passed", [])
    raw_failed = score.get("tasks_failed", [])
    passed_tasks = [
        str(task_name) for task_name in raw_passed if isinstance(task_name, (str, int))
    ]
    failed_tasks = [
        str(task_name) for task_name in raw_failed if isinstance(task_name, (str, int))
    ]
    passed_set = set(passed_tasks)

    tasks: list[dict[str, Any]] = []
    for task_name in sorted(passed_set):
        tasks.append(
            {
                "task_name": task_name,
                "short_name": task_name,
                "reward": None,
                "passed": True,
                "cost_usd": None,
                "num_turns": None,
                "duration_ms": None,
                "wall_time_s": 0,
                "input_tokens": None,
                "output_tokens": None,
            }
        )
    for task_name in sorted(set(failed_tasks) - passed_set):
        tasks.append(
            {
                "task_name": task_name,
                "short_name": task_name,
                "reward": None,
                "passed": False,
                "cost_usd": None,
                "num_turns": None,
                "duration_ms": None,
                "wall_time_s": 0,
                "input_tokens": None,
                "output_tokens": None,
            }
        )
    return tasks


def _build_tasks_from_per_task(candidate_dir: Path) -> list[dict[str, Any]]:
    per_task_dir = candidate_dir / "per_task"
    if not per_task_dir.exists() or not per_task_dir.is_dir():
        return []

    tasks: list[dict[str, Any]] = []
    for task_file in sorted(per_task_dir.iterdir()):
        if not task_file.is_file():
            continue
        if not task_file.name.endswith(".json"):
            continue
        if task_file.name.endswith("_agent_result.json"):
            continue

        task = _load_json(task_file, None)
        if not isinstance(task, dict):
            continue

        task_name = str(task.get("task_name") or task_file.stem)
        task["task_name"] = task_name
        if not isinstance(task.get("short_name"), str):
            task["short_name"] = task_name
        tasks.append(task)

    return tasks


def _build_dashboard_index(run_root: Path, run_id: str) -> dict[str, Any]:
    experience_root = run_root / "experience"
    benchmarks: dict[str, Any] = {}
    if experience_root.exists():
        for bench_dir in sorted(experience_root.iterdir()):
            if not bench_dir.is_dir():
                continue

            candidates_dir = bench_dir / "candidates"
            runs: list[dict[str, Any]] = []
            if candidates_dir.exists():
                for candidate_dir in sorted(candidates_dir.iterdir()):
                    if not candidate_dir.is_dir():
                        continue
                    score = _load_json(candidate_dir / "scores.json", None)
                    if not isinstance(score, dict):
                        continue
                    score["name"] = score.get("name") or candidate_dir.name
                    tasks = _build_tasks_from_per_task(candidate_dir)
                    if not tasks:
                        tasks = _build_tasks_from_scores(score)
                    runs.append(
                        {
                            "name": candidate_dir.name,
                            "scores": score,
                            "config": None,
                            "harnessFiles": [],
                            "tasks": tasks,
                        }
                    )

            history = _load_json(bench_dir / "history.json", None)
            benchmarks[bench_dir.name] = {
                "runs": runs,
                "history": history if isinstance(history, dict) else None,
            }

    return {
        "run_id": run_id,
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "benchmarks": benchmarks,
    }

def _infer_next_iteration(candidates_dir: Path) -> int:
    highest = 0
    if not candidates_dir.exists():
        return 1
    for child in candidates_dir.iterdir():
        if not child.is_dir():
            continue
        match = EVO_NAME_RE.match(child.name)
        if not match:
            continue
        highest = max(highest, int(match.group(1)))
    return highest + 1


def _run_probe(cmd: list[str], env: dict[str, str], cwd: Path) -> tuple[int, str]:
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=40,
    )
    output = (result.stdout or "").strip() or (result.stderr or "").strip()
    return result.returncode, output


def _build_runtime_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["ARTIFACTS_USE_MODAL"] = "0"  # Mode A: keep all eval work in this container.
    # Modal already isolates this job. Fully bypass Codex's internal bwrap
    # sandbox, which fails loopback namespace setup in this environment.
    env.setdefault("CODEX_DANGEROUS_BYPASS", "1")
    env.pop("CODEX_SANDBOX_MODE", None)

    # Keep both vars in sync for compatibility across Codex CLI/SDK paths.
    codex_key = (env.get("CODEX_API_KEY") or "").strip()
    openai_key = (env.get("OPENAI_API_KEY") or "").strip()
    if codex_key and not openai_key:
        env["OPENAI_API_KEY"] = codex_key
    elif openai_key and not codex_key:
        env["CODEX_API_KEY"] = openai_key
    return env


def _run_preflight(
    *,
    workspace_root: Path,
    benchmark_path: str,
    holdout_benchmark: Optional[str],
    proposer_cli: str,
    env: dict[str, str],
) -> dict[str, Any]:
    _ensure_workspace_on_syspath(workspace_root)

    benchmark_abs = _resolve_workspace_path(workspace_root, benchmark_path)
    if not benchmark_abs.exists():
        raise PreflightError(f"Benchmark not found: {benchmark_abs}")

    if holdout_benchmark:
        holdout_abs = _resolve_workspace_path(workspace_root, holdout_benchmark)
        if not holdout_abs.exists():
            raise PreflightError(f"Holdout benchmark not found: {holdout_abs}")

    from meta_agent.benchmark import load_benchmark

    bench = load_benchmark(str(benchmark_abs))
    if bench.type == "artifacts_bench" and bench.artifacts_backend is not None:
        dataset = _resolve_workspace_path(workspace_root, bench.artifacts_backend.dataset_path)
        if not dataset.exists():
            raise PreflightError(f"Artifacts dataset not found: {dataset}")

    python_bin = shutil.which("python")
    node_bin = shutil.which("node")
    if not python_bin:
        raise PreflightError("python is not on PATH")
    if not node_bin:
        raise PreflightError("node is not on PATH")

    if proposer_cli == "codex":
        codex_bin = shutil.which("codex")
        if not codex_bin:
            raise PreflightError("codex CLI is not on PATH")
        key = (env.get("CODEX_API_KEY") or "").strip()
        if not key:
            raise PreflightError("CODEX_API_KEY is required for proposer_cli=codex")
        rc, out = _run_probe(["codex", "--version"], env=env, cwd=workspace_root)
        if rc != 0:
            raise PreflightError(f"codex --version failed: {out or f'exit={rc}'}")

    # Validate volume write/read path behavior early.
    run_test = workspace_root / "jobs" / "cloud_probe.txt"
    run_test.parent.mkdir(parents=True, exist_ok=True)
    run_test.write_text(f"probe:{time.time()}")
    if not run_test.exists():
        raise PreflightError("Volume write probe failed")

    return {
        "benchmark_name": bench.name,
        "benchmark_type": bench.type,
        "benchmark_path": str(benchmark_abs),
        "holdout_path": holdout_benchmark or "",
        "python": python_bin,
        "node": node_bin,
        "proposer_cli": proposer_cli,
    }


def _build_outer_loop_cmd(
    *,
    benchmark_path: str,
    iterations: int,
    start_from: int,
    model: str,
    fast: bool,
    concurrency: int,
    proposer_model: str,
    proposer_cli: str,
    holdout_benchmark: Optional[str],
    batch_size: Optional[int],
    seed: Optional[int],
    baseline: Optional[str],
    evolve_skill: bool,
    skill_evolve_every: int,
) -> list[str]:
    cmd: list[str] = [
        sys.executable,
        "-m",
        "meta_agent.outer_loop",
        "--benchmark",
        benchmark_path,
        "--iterations",
        str(iterations),
        "--start-from",
        str(start_from),
        "--model",
        model,
        "--concurrency",
        str(concurrency),
        "--proposer-model",
        proposer_model,
        "--proposer-cli",
        proposer_cli,
    ]
    if fast:
        cmd.append("--fast")
    if holdout_benchmark:
        cmd.extend(["--holdout-benchmark", holdout_benchmark])
    if batch_size is not None:
        cmd.extend(["--batch-size", str(batch_size)])
    if seed is not None:
        cmd.extend(["--seed", str(seed)])
    if baseline:
        cmd.extend(["--baseline", baseline])
    if evolve_skill:
        cmd.append("--evolve-skill")
        cmd.extend(["--skill-evolve-every", str(skill_evolve_every)])
    return cmd


def _run_outer_loop_subprocess(
    *,
    workspace_root: Path,
    run_root: Path,
    cmd: list[str],
    env: dict[str, str],
) -> tuple[int, Path]:
    log_path = run_root / "logs" / f"outer_loop_{int(time.time())}.log"
    print(f"[CLOUD] Running command: {' '.join(cmd)}")
    print(f"[CLOUD] Streaming logs to {log_path}")
    sys.stdout.flush()

    with log_path.open("w") as log_file:
        process = subprocess.Popen(
            cmd,
            cwd=str(workspace_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line.rstrip())
            log_file.write(line)
        rc = process.wait()
    return rc, log_path


def _write_run_state(
    *,
    run_root: Path,
    run_id: str,
    status: str,
    message: str,
    params: dict[str, Any],
    next_iteration: int,
    result_summary: Optional[dict[str, Any]] = None,
) -> None:
    payload = {
        "run_id": run_id,
        "status": status,
        "message": message,
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "next_iteration": next_iteration,
        "params": params,
        "result_summary": result_summary or {},
    }
    _save_json(run_root / "state.json", payload)


def _load_next_iteration_from_state(run_root: Path) -> Optional[int]:
    state = _load_json(run_root / "state.json", {})
    next_value = state.get("next_iteration")
    if isinstance(next_value, int) and next_value > 0:
        return next_value
    return None


@app.function(
    image=image,
    volumes={RUNS_VOLUME_MOUNT: RUNS_VOLUME},
    secrets=RUNTIME_SECRETS,
    timeout=24 * 60 * 60,
    cpu=8,
    memory=32768,
)
def run_outer_loop_cloud(
    run_id: str,
    benchmark: str = "benchmarks/artifacts_bench/benchmark.yaml",
    holdout_benchmark: Optional[str] = None,
    iterations: int = 1,
    iterations_per_step: int = 1,
    model: str = "gpt-5.3-codex",
    fast: bool = False,
    concurrency: int = 4,
    proposer_model: str = "gpt-5.3-codex",
    proposer_cli: str = "codex",
    batch_size: Optional[int] = None,
    seed: Optional[int] = None,
    baseline: Optional[str] = None,
    evolve_skill: bool = False,
    skill_evolve_every: int = 5,
    resume: bool = True,
    start_from: Optional[int] = None,
) -> dict[str, Any]:
    if iterations <= 0:
        raise ValueError("iterations must be >= 1")
    if iterations_per_step <= 0:
        raise ValueError("iterations_per_step must be >= 1")
    if skill_evolve_every <= 0:
        raise ValueError("skill_evolve_every must be >= 1")

    workspace_root, run_root = _prepare_workspace(run_id)
    env = _build_runtime_env()

    started_at = dt.datetime.now(dt.timezone.utc).isoformat()
    params: dict[str, Any] = {
        "benchmark": benchmark,
        "holdout_benchmark": holdout_benchmark,
        "iterations": iterations,
        "iterations_per_step": iterations_per_step,
        "model": model,
        "fast": fast,
        "concurrency": concurrency,
        "proposer_model": proposer_model,
        "proposer_cli": proposer_cli,
        "batch_size": batch_size,
        "seed": seed,
        "baseline": baseline,
        "evolve_skill": evolve_skill,
        "skill_evolve_every": skill_evolve_every,
        "resume": resume,
        "start_from": start_from,
    }

    try:
        preflight = _run_preflight(
            workspace_root=workspace_root,
            benchmark_path=benchmark,
            holdout_benchmark=holdout_benchmark,
            proposer_cli=proposer_cli,
            env=env,
        )
    except Exception as exc:
        summary = {
            "ok": False,
            "run_id": run_id,
            "started_at": started_at,
            "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "error": str(exc),
            "phase": "preflight",
        }
        _save_json(run_root / "artifacts" / "run_summary.json", summary)
        _write_run_state(
            run_root=run_root,
            run_id=run_id,
            status="failed",
            message=f"preflight failed: {exc}",
            params=params,
            next_iteration=1,
            result_summary=summary,
        )
        _save_json(
            run_root / "artifacts" / "index.json",
            _build_dashboard_index(run_root, run_id),
        )
        RUNS_VOLUME.commit()
        return summary

    bench_name = str(preflight["benchmark_name"])
    candidates_dir = run_root / "experience" / bench_name / "candidates"
    inferred_start = _infer_next_iteration(candidates_dir)
    state_next = _load_next_iteration_from_state(run_root)

    if start_from is not None:
        current_iteration = start_from
    elif resume:
        candidates = [inferred_start]
        if state_next is not None:
            candidates.append(state_next)
        current_iteration = max(candidates)
    else:
        current_iteration = 1

    _write_run_state(
        run_root=run_root,
        run_id=run_id,
        status="running",
        message="outer loop in progress",
        params=params,
        next_iteration=current_iteration,
    )
    _save_json(
        run_root / "artifacts" / "index.json",
        _build_dashboard_index(run_root, run_id),
    )
    RUNS_VOLUME.commit()

    remaining = iterations
    chunk_logs: list[str] = []
    last_rc = 0

    while remaining > 0:
        chunk_iterations = min(iterations_per_step, remaining)
        _write_run_state(
            run_root=run_root,
            run_id=run_id,
            status="running",
            message=(
                f"running chunk: start={current_iteration}, "
                f"iterations={chunk_iterations}, remaining={remaining}"
            ),
            params=params,
            next_iteration=current_iteration,
        )
        RUNS_VOLUME.commit()

        cmd = _build_outer_loop_cmd(
            benchmark_path=benchmark,
            iterations=chunk_iterations,
            start_from=current_iteration,
            model=model,
            fast=fast,
            concurrency=concurrency,
            proposer_model=proposer_model,
            proposer_cli=proposer_cli,
            holdout_benchmark=holdout_benchmark,
            batch_size=batch_size,
            seed=seed,
            baseline=baseline,
            evolve_skill=evolve_skill,
            skill_evolve_every=skill_evolve_every,
        )

        last_rc, log_path = _run_outer_loop_subprocess(
            workspace_root=workspace_root,
            run_root=run_root,
            cmd=cmd,
            env=env,
        )
        chunk_logs.append(str(log_path))
        if last_rc != 0:
            break

        current_iteration += chunk_iterations
        remaining -= chunk_iterations

        _write_run_state(
            run_root=run_root,
            run_id=run_id,
            status="running",
            message="chunk completed",
            params=params,
            next_iteration=current_iteration,
        )
        _save_json(
            run_root / "artifacts" / "index.json",
            _build_dashboard_index(run_root, run_id),
        )
        RUNS_VOLUME.commit()

    score_rows = _collect_candidate_scores(candidates_dir)
    best = score_rows[0] if score_rows else None
    finished_at = dt.datetime.now(dt.timezone.utc).isoformat()
    completed = iterations - remaining if last_rc == 0 else max(iterations - remaining, 0)

    summary = {
        "ok": last_rc == 0,
        "run_id": run_id,
        "benchmark_name": bench_name,
        "started_at": started_at,
        "finished_at": finished_at,
        "return_code": last_rc,
        "requested_iterations": iterations,
        "completed_iterations": completed,
        "next_iteration": current_iteration if last_rc == 0 else current_iteration,
        "candidates_found": len(score_rows),
        "best_candidate": best,
        "chunk_logs": chunk_logs,
        "preflight": preflight,
    }
    _save_json(run_root / "artifacts" / "run_summary.json", summary)

    final_status = "succeeded" if last_rc == 0 else "failed"
    final_message = (
        "outer loop completed"
        if last_rc == 0
        else f"outer loop exited with code {last_rc}"
    )
    _write_run_state(
        run_root=run_root,
        run_id=run_id,
        status=final_status,
        message=final_message,
        params=params,
        next_iteration=current_iteration,
        result_summary=summary,
    )
    _save_json(
        run_root / "artifacts" / "index.json",
        _build_dashboard_index(run_root, run_id),
    )
    RUNS_VOLUME.commit()
    return summary


@app.function(
    image=image,
    volumes={RUNS_VOLUME_MOUNT: RUNS_VOLUME},
    secrets=RUNTIME_SECRETS,
    timeout=10 * 60,
)
def smoke_cloud(
    run_id: str = "smoke-a1",
    benchmark: str = "benchmarks/artifacts_bench/benchmark.yaml",
    holdout_benchmark: Optional[str] = None,
    proposer_cli: str = "codex",
) -> dict[str, Any]:
    workspace_root, run_root = _prepare_workspace(run_id)
    env = _build_runtime_env()

    try:
        preflight = _run_preflight(
            workspace_root=workspace_root,
            benchmark_path=benchmark,
            holdout_benchmark=holdout_benchmark,
            proposer_cli=proposer_cli,
            env=env,
        )
        ok = True
        error = ""
    except Exception as exc:
        preflight = {}
        ok = False
        error = str(exc)

    marker_path = run_root / "artifacts" / "smoke.json"
    marker = {
        "ok": ok,
        "error": error,
        "run_id": run_id,
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "workspace_root": str(workspace_root),
        "project_mount_exists": workspace_root.exists(),
        "python_bin": shutil.which("python"),
        "node_bin": shutil.which("node"),
        "codex_bin": shutil.which("codex"),
        "codex_key_suffix": f"...{(env.get('CODEX_API_KEY') or '')[-4:]}",
        "openai_key_suffix": f"...{(env.get('OPENAI_API_KEY') or '')[-4:]}",
        "preflight": preflight,
    }
    _save_json(marker_path, marker)
    _write_run_state(
        run_root=run_root,
        run_id=run_id,
        status="smoke_passed" if ok else "smoke_failed",
        message="smoke completed" if ok else f"smoke failed: {error}",
        params={"benchmark": benchmark, "holdout_benchmark": holdout_benchmark, "proposer_cli": proposer_cli},
        next_iteration=1,
        result_summary=marker,
    )
    RUNS_VOLUME.commit()
    RUNS_VOLUME.reload()
    return marker


@app.function(
    image=image,
    volumes={RUNS_VOLUME_MOUNT: RUNS_VOLUME},
    secrets=RUNTIME_SECRETS,
    timeout=5 * 60,
)
def get_run_status_cloud(run_id: str) -> dict[str, Any]:
    _validate_run_id(run_id)
    run_root = _run_root(run_id)
    RUNS_VOLUME.reload()
    state = _load_json(run_root / "state.json", {})
    summary = _load_json(run_root / "artifacts" / "run_summary.json", {})
    smoke = _load_json(run_root / "artifacts" / "smoke.json", {})
    return {
        "run_id": run_id,
        "run_root": str(run_root),
        "state": state,
        "summary": summary,
        "smoke": smoke,
    }


@app.local_entrypoint()
def main(
    run_id: str,
    do_smoke: bool = False,
    do_status: bool = False,
    detach_run: bool = False,
    benchmark: str = "benchmarks/artifacts_bench/benchmark.yaml",
    holdout_benchmark: str = "",
    iterations: int = 1,
    iterations_per_step: int = 1,
    model: str = "gpt-5.3-codex",
    fast: bool = False,
    concurrency: int = 4,
    allow_high_concurrency: bool = False,
    proposer_model: str = "gpt-5.3-codex",
    proposer_cli: str = "codex",
    batch_size: int = 0,
    seed: int = -1,
    baseline: str = "",
    evolve_skill: bool = False,
    skill_evolve_every: int = 5,
    resume: bool = True,
    start_from: int = 0,
) -> None:
    if concurrency > 6 and not allow_high_concurrency:
        raise ValueError(
            "Mode A default guard: concurrency > 6 is blocked. "
            "Pass --allow-high-concurrency if you explicitly want this."
        )

    holdout_arg = holdout_benchmark.strip() or None
    batch_arg = batch_size if batch_size > 0 else None
    seed_arg = seed if seed >= 0 else None
    baseline_arg = baseline.strip() or None
    start_arg = start_from if start_from > 0 else None

    if do_smoke:
        result = smoke_cloud.remote(
            run_id=run_id,
            benchmark=benchmark,
            holdout_benchmark=holdout_arg,
            proposer_cli=proposer_cli,
        )
        print(json.dumps(result, indent=2))
        return

    if do_status:
        result = get_run_status_cloud.remote(run_id=run_id)
        print(json.dumps(result, indent=2))
        return

    _register_modal_source_local(run_id=run_id, benchmark_path=benchmark)

    kwargs: dict[str, Any] = {
        "run_id": run_id,
        "benchmark": benchmark,
        "holdout_benchmark": holdout_arg,
        "iterations": iterations,
        "iterations_per_step": iterations_per_step,
        "model": model,
        "fast": fast,
        "concurrency": concurrency,
        "proposer_model": proposer_model,
        "proposer_cli": proposer_cli,
        "batch_size": batch_arg,
        "seed": seed_arg,
        "baseline": baseline_arg,
        "evolve_skill": evolve_skill,
        "skill_evolve_every": skill_evolve_every,
        "resume": resume,
        "start_from": start_arg,
    }

    if detach_run:
        call = run_outer_loop_cloud.spawn(**kwargs)
        print(
            json.dumps(
                {
                    "run_id": run_id,
                    "status": "spawned",
                    "call_id": call.object_id,
                    "note": "Use --do-status to poll run state by run_id.",
                },
                indent=2,
            )
        )
        return

    result = run_outer_loop_cloud.remote(**kwargs)
    print(json.dumps(result, indent=2))
