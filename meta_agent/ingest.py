"""Ingest flat trace files into the experience store format.

User-facing input format:

    my-traces/
    ├── manifest.json           # [{"task_id": "x", "passed": true}, ...]
    ├── x.jsonl                 # trace for task x
    ├── y.jsonl                 # trace for task y
    └── ...

Usage:
    python -m meta_agent.ingest \
        --traces ./my-traces/ \
        --project my-agent \
        --name baseline \
        --config ./my-harness/
"""
from __future__ import annotations

import argparse
import json
import shutil
import statistics
from pathlib import Path
from typing import Any, Dict, List, Optional

from meta_agent.paths import get_experience_root


def ingest(
    traces_dir: Path,
    project: str,
    name: str,
    config_path: Optional[Path] = None,
    model: str = "unknown",
) -> Path:
    """Read a flat traces directory and write experience store format.

    Returns the candidate directory path.
    """
    traces_dir = Path(traces_dir).resolve()
    manifest_path = traces_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"No manifest.json found in {traces_dir}")

    manifest: List[Dict[str, Any]] = json.loads(manifest_path.read_text())

    candidate_dir = get_experience_root() / project / "candidates" / name
    per_task_dir = candidate_dir / "per_task"
    per_task_dir.mkdir(parents=True, exist_ok=True)

    if config_path:
        config_path = Path(config_path).resolve()
        if config_path.is_dir():
            for item in config_path.iterdir():
                dest = candidate_dir / item.name
                if item.is_dir():
                    shutil.copytree(item, dest, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dest)
        elif config_path.is_file():
            shutil.copy2(config_path, candidate_dir / config_path.name)

    trials: List[Dict[str, Any]] = []
    for entry in manifest:
        task_id = str(entry["task_id"])
        passed = bool(entry.get("passed", False))
        reward = float(entry.get("reward", 1.0 if passed else 0.0))

        trial: Dict[str, Any] = {
            "task_name": task_id,
            "short_name": task_id,
            "passed": passed,
            "reward": reward,
            "cost_usd": entry.get("cost_usd"),
            "num_turns": entry.get("num_turns"),
            "duration_ms": entry.get("duration_ms"),
            "wall_time_s": entry.get("wall_time_s"),
            "input_tokens": entry.get("input_tokens"),
            "output_tokens": entry.get("output_tokens"),
            "cache_tokens": entry.get("cache_tokens"),
            "session_id": entry.get("session_id"),
            "trial_dir": str(traces_dir),
        }
        trials.append(trial)

        (per_task_dir / f"{task_id}.json").write_text(json.dumps(trial, indent=2))

        trace_src = traces_dir / f"{task_id}.jsonl"
        if trace_src.exists():
            shutil.copy2(trace_src, per_task_dir / f"{task_id}_trace.jsonl")

        judge_src = traces_dir / f"{task_id}_judge.md"
        if judge_src.exists():
            shutil.copy2(judge_src, per_task_dir / f"{task_id}_judge_feedback.md")

    n_tasks = len(trials)
    n_passed = sum(1 for t in trials if t["passed"])
    rewards = [t["reward"] for t in trials if t["reward"] is not None]
    costs = [t["cost_usd"] for t in trials if t["cost_usd"] is not None]
    turns = [t["num_turns"] for t in trials if t["num_turns"] is not None]

    scores: Dict[str, Any] = {
        "name": name,
        "config_path": str(config_path) if config_path else "",
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

    print(f"[INGEST] {n_passed}/{n_tasks} passed ({scores['pass_rate']:.0%})")
    print(f"[INGEST] Written to {candidate_dir}")
    return candidate_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest traces into the experience store")
    parser.add_argument("--traces", required=True, help="Directory with manifest.json + trace JSONL files")
    parser.add_argument("--project", required=True, help="Project name (groups candidates in the experience store)")
    parser.add_argument("--name", required=True, help="Candidate name (e.g. 'baseline', 'v2')")
    parser.add_argument("--config", default=None, help="Path to current harness config (file or directory)")
    parser.add_argument("--model", default="unknown", help="Model identifier (metadata only)")
    args = parser.parse_args()

    ingest(
        traces_dir=Path(args.traces),
        project=args.project,
        name=args.name,
        config_path=Path(args.config) if args.config else None,
        model=args.model,
    )


if __name__ == "__main__":
    main()
