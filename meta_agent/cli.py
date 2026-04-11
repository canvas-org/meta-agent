#!/usr/bin/env python3
from __future__ import annotations

"""CLI for querying the experience store.

Used by humans and the proposer agent to browse candidates, compare results,
and inspect failures.

Usage:
    python -m meta_agent.cli list
    python -m meta_agent.cli show <name>
    python -m meta_agent.cli diff <name1> <name2>
    python -m meta_agent.cli failures <name>
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from meta_agent.paths import get_workspace_root

EXPERIENCE_DIR = get_workspace_root() / "experience" / "candidates"


def load_scores(candidate_dir: Path) -> dict[str, Any] | None:
    scores_path = candidate_dir / "scores.json"
    if not scores_path.exists():
        return None
    try:
        return json.loads(scores_path.read_text())
    except json.JSONDecodeError:
        return None


def load_per_task(candidate_dir: Path) -> dict[str, dict[str, Any]]:
    """Load all per-task result files into a dict keyed by short task name."""
    per_task_dir = candidate_dir / "per_task"
    if not per_task_dir.exists():
        return {}
    results: dict[str, dict[str, Any]] = {}
    for f in sorted(per_task_dir.glob("*.json")):
        if f.name.endswith("_agent_result.json"):
            continue
        try:
            data = json.loads(f.read_text())
            short_name = data.get("short_name", f.stem)
            results[short_name] = data
        except json.JSONDecodeError:
            continue
    return results


def cmd_list(args: argparse.Namespace) -> None:
    """List all candidates sorted by pass rate."""
    if not EXPERIENCE_DIR.exists():
        print("No experience store found.")
        return

    candidates: list[dict[str, Any]] = []
    for d in sorted(EXPERIENCE_DIR.iterdir()):
        if not d.is_dir():
            continue
        scores = load_scores(d)
        if scores:
            candidates.append(scores)

    if not candidates:
        print("No candidates found.")
        return

    candidates.sort(key=lambda c: (c.get("mean_reward") or c.get("pass_rate", 0), -(c.get("total_cost_usd") or 999)), reverse=True)

    print(f"{'Name':<25} {'Reward':<10} {'Pass Rate':<12} {'Cost':<12} {'Turns':<8}")
    print("-" * 75)
    for c in candidates:
        name = c.get("name", "?")
        n_passed = c.get("n_passed", 0)
        n_tasks = c.get("n_tasks", 0)
        reward = c.get("mean_reward")
        rate = c.get("pass_rate", 0)
        cost = c.get("total_cost_usd")
        turns = c.get("median_turns")
        reward_str = f"{reward:.1%}" if reward is not None else "N/A"
        cost_str = f"${cost:.4f}" if cost is not None else "N/A"
        turns_str = str(turns) if turns is not None else "N/A"
        print(f"{name:<25} {reward_str:<10} {n_passed}/{n_tasks} ({rate:.0%}){'':<2} {cost_str:<12} {turns_str:<8}")


def cmd_show(args: argparse.Namespace) -> None:
    """Show detailed results for a candidate."""
    candidate_dir = EXPERIENCE_DIR / args.name
    if not candidate_dir.exists():
        print(f"Candidate '{args.name}' not found.")
        return

    summary_path = candidate_dir / "summary.md"
    if summary_path.exists():
        print(summary_path.read_text())
        return

    scores = load_scores(candidate_dir)
    if scores:
        print(json.dumps(scores, indent=2))


def cmd_diff(args: argparse.Namespace) -> None:
    """Show which tasks flipped between two candidates."""
    dir1 = EXPERIENCE_DIR / args.name1
    dir2 = EXPERIENCE_DIR / args.name2

    if not dir1.exists():
        print(f"Candidate '{args.name1}' not found.")
        return
    if not dir2.exists():
        print(f"Candidate '{args.name2}' not found.")
        return

    tasks1 = load_per_task(dir1)
    tasks2 = load_per_task(dir2)
    all_tasks = sorted(set(tasks1.keys()) | set(tasks2.keys()))

    if not all_tasks:
        print("No per-task data found for comparison.")
        return

    scores1 = load_scores(dir1) or {}
    scores2 = load_scores(dir2) or {}
    print(f"Comparing: {args.name1} ({scores1.get('pass_rate', 0):.0%}) vs {args.name2} ({scores2.get('pass_rate', 0):.0%})")
    print()

    flipped_to_pass: list[str] = []
    flipped_to_fail: list[str] = []
    both_pass: list[str] = []
    both_fail: list[str] = []

    for task in all_tasks:
        passed1 = tasks1.get(task, {}).get("passed", False)
        passed2 = tasks2.get(task, {}).get("passed", False)

        if not passed1 and passed2:
            flipped_to_pass.append(task)
        elif passed1 and not passed2:
            flipped_to_fail.append(task)
        elif passed1 and passed2:
            both_pass.append(task)
        else:
            both_fail.append(task)

    if flipped_to_pass:
        print(f"Gained ({len(flipped_to_pass)} tasks — failed in {args.name1}, passed in {args.name2}):")
        for t in flipped_to_pass:
            print(f"  + {t}")
    if flipped_to_fail:
        print(f"\nLost ({len(flipped_to_fail)} tasks — passed in {args.name1}, failed in {args.name2}):")
        for t in flipped_to_fail:
            print(f"  - {t}")
    if both_pass:
        print(f"\nBoth pass: {len(both_pass)} tasks")
    if both_fail:
        print(f"Both fail: {len(both_fail)} tasks")

    cost1 = scores1.get("total_cost_usd", 0) or 0
    cost2 = scores2.get("total_cost_usd", 0) or 0
    if cost1 > 0 and cost2 > 0:
        print(f"\nCost: ${cost1:.4f} → ${cost2:.4f} ({'+' if cost2 > cost1 else ''}{cost2 - cost1:.4f})")


def cmd_pareto(args: argparse.Namespace) -> None:
    """Show the Pareto frontier: candidates where no other has both higher accuracy and lower cost."""
    if not EXPERIENCE_DIR.exists():
        print("No experience store found.")
        return

    candidates: list[dict[str, Any]] = []
    for d in sorted(EXPERIENCE_DIR.iterdir()):
        if not d.is_dir():
            continue
        scores = load_scores(d)
        if scores and scores.get("total_cost_usd") is not None:
            candidates.append(scores)

    if not candidates:
        print("No candidates with cost data found.")
        return

    frontier: list[dict[str, Any]] = []
    for c in candidates:
        reward = c.get("mean_reward") or c.get("pass_rate", 0)
        cost = c.get("total_cost_usd", float("inf"))
        dominated = any(
            (other.get("mean_reward") or other.get("pass_rate", 0)) >= reward
            and (other.get("total_cost_usd", float("inf"))) <= cost
            and (
                (other.get("mean_reward") or other.get("pass_rate", 0)) > reward
                or other.get("total_cost_usd", float("inf")) < cost
            )
            for other in candidates
        )
        if not dominated:
            frontier.append(c)

    frontier.sort(key=lambda c: c.get("mean_reward") or c.get("pass_rate", 0), reverse=True)

    print(f"Pareto frontier ({len(frontier)}/{len(candidates)} candidates):\n")
    print(f"{'Name':<25} {'Reward':<10} {'Pass Rate':<12} {'Cost':<12} {'Turns':<8}")
    print("-" * 75)
    for c in frontier:
        name = c.get("name", "?")
        n_passed = c.get("n_passed", 0)
        n_tasks = c.get("n_tasks", 0)
        reward = c.get("mean_reward")
        rate = c.get("pass_rate", 0)
        cost = c.get("total_cost_usd")
        turns = c.get("median_turns")
        reward_str = f"{reward:.1%}" if reward is not None else "N/A"
        cost_str = f"${cost:.4f}" if cost is not None else "N/A"
        turns_str = str(turns) if turns is not None else "N/A"
        print(f"{name:<25} {reward_str:<10} {n_passed}/{n_tasks} ({rate:.0%}){'':<2} {cost_str:<12} {turns_str:<8}")

    if len(frontier) >= 2:
        best = frontier[0]
        cheapest = frontier[-1]
        print(f"\nBest accuracy: {best.get('name')} ({best.get('mean_reward') or best.get('pass_rate', 0):.1%}, ${best.get('total_cost_usd', 0):.4f})")
        print(f"Cheapest on frontier: {cheapest.get('name')} ({cheapest.get('mean_reward') or cheapest.get('pass_rate', 0):.1%}, ${cheapest.get('total_cost_usd', 0):.4f})")


def cmd_failures(args: argparse.Namespace) -> None:
    """List failed tasks with a summary from the trace."""
    candidate_dir = EXPERIENCE_DIR / args.name
    if not candidate_dir.exists():
        print(f"Candidate '{args.name}' not found.")
        return

    tasks = load_per_task(candidate_dir)
    failed = {name: data for name, data in tasks.items() if not data.get("passed", False)}

    if not failed:
        print(f"No failures found for '{args.name}'.")
        return

    print(f"Failed tasks for {args.name} ({len(failed)}/{len(tasks)}):\n")

    for name, data in sorted(failed.items()):
        cost = data.get("cost_usd")
        turns = data.get("num_turns")
        cost_str = f"${cost:.4f}" if cost else "N/A"
        turns_str = str(turns) if turns else "N/A"

        trace_path = candidate_dir / "per_task" / f"{name}_trace.jsonl"
        summary = ""
        if trace_path.exists():
            try:
                lines = trace_path.read_text().strip().split("\n")
                for line in reversed(lines):
                    record = json.loads(line)
                    if record.get("type") == "ResultMessage":
                        result_text = record.get("result", "")
                        if result_text:
                            summary = result_text[:120]
                        break
            except (json.JSONDecodeError, KeyError):
                pass

        print(f"  {name}  (cost={cost_str}, turns={turns_str})")
        if summary:
            print(f"    Last output: {summary}")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Query the experience store")
    parser.add_argument("--dir", default=None, help="Path to candidates directory (default: experience/candidates)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List all candidates ranked by pass rate")

    show_parser = subparsers.add_parser("show", help="Show detailed results for a candidate")
    show_parser.add_argument("name", help="Candidate name")

    diff_parser = subparsers.add_parser("diff", help="Compare two candidates")
    diff_parser.add_argument("name1", help="First candidate")
    diff_parser.add_argument("name2", help="Second candidate")

    failures_parser = subparsers.add_parser("failures", help="List failed tasks for a candidate")
    failures_parser.add_argument("name", help="Candidate name")

    subparsers.add_parser("pareto", help="Show Pareto frontier (accuracy vs cost)")

    args = parser.parse_args()

    global EXPERIENCE_DIR
    if args.dir:
        EXPERIENCE_DIR = Path(args.dir)

    cmd = args.command
    if cmd == "list":
        cmd_list(args)
    elif cmd == "show":
        cmd_show(args)
    elif cmd == "diff":
        cmd_diff(args)
    elif cmd == "failures":
        cmd_failures(args)
    elif cmd == "pareto":
        cmd_pareto(args)


if __name__ == "__main__":
    main()
