"""Run the proposer once against an experience store and output a harness diff.

Usage:
    python -m meta_agent.propose \
        --project my-agent \
        --harness claude_code \
        --model claude-opus-4-6
"""
from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path
from typing import Optional

from meta_agent.benchmark import FILE_BASED_HARNESSES
from meta_agent.outer_loop import invoke_proposer
from meta_agent.paths import get_experience_root


def propose(
    project: str,
    harness: str = "claude_agent_sdk",
    model: Optional[str] = "claude-opus-4-6",
    proposer_cli: str = "claude",
    apply: bool = False,
) -> bool:
    """Run the proposer once and print the resulting diff.

    Returns True if the proposer produced output.
    """
    experience_dir = get_experience_root() / project / "candidates"
    staging_dir = get_experience_root() / project / "staging"

    if not experience_dir.exists() or not any(experience_dir.iterdir()):
        print(f"[PROPOSE] No candidates found in {experience_dir}")
        print(f"[PROPOSE] Run `python -m meta_agent.ingest` first to load traces.")
        return False

    success = invoke_proposer(
        staging_dir=staging_dir,
        experience_dir=experience_dir,
        bench_name=project,
        model=model,
        harness=harness,
        proposer_cli=proposer_cli,
    )

    if not success:
        return False

    file_based = harness in FILE_BASED_HARNESSES
    _print_diff(staging_dir, experience_dir, file_based)

    if apply:
        _apply_to_latest(staging_dir, experience_dir, file_based)

    return True


def _print_diff(staging_dir: Path, experience_dir: Path, file_based: bool) -> None:
    """Print a unified diff between the best candidate's config and the proposed one."""
    best = _find_best_candidate(experience_dir)
    if not best:
        return

    if file_based:
        changed = False
        rel_files = _collect_harness_files(best) | _collect_harness_files(staging_dir)
        for rel_path in sorted(rel_files, key=str):
            changed = _diff_file(
                best / rel_path,
                staging_dir / rel_path,
                str(rel_path),
                print_no_changes=False,
            ) or changed
        if not changed:
            print("[PROPOSE] No harness file changes detected")
    else:
        _diff_file(best / "config.py", staging_dir / "config.py", "config.py")


def _collect_harness_files(config_dir: Path) -> set[Path]:
    files: set[Path] = set()
    for name in ("AGENTS.md", "CLAUDE.md"):
        f = config_dir / name
        if f.is_file():
            files.add(Path(name))

    for f in config_dir.glob("*.sh"):
        if f.is_file():
            files.add(f.relative_to(config_dir))

    for dirname in (".codex", ".claude"):
        root = config_dir / dirname
        if not root.is_dir():
            continue
        for f in root.rglob("*"):
            if f.is_file():
                files.add(f.relative_to(config_dir))
    return files


def _diff_file(old_path: Path, new_path: Path, label: str, print_no_changes: bool = True) -> bool:
    old_lines = old_path.read_text().splitlines(keepends=True) if old_path.exists() else []
    new_lines = new_path.read_text().splitlines(keepends=True) if new_path.exists() else []

    diff = list(difflib.unified_diff(old_lines, new_lines, fromfile=f"a/{label}", tofile=f"b/{label}"))
    if diff:
        print(f"\n{'='*60}")
        print(f"  Proposed changes to {label}")
        print(f"{'='*60}\n")
        sys.stdout.writelines(diff)
        print()
        return True
    if print_no_changes:
        print(f"[PROPOSE] No changes to {label}")
    return False


def _find_best_candidate(experience_dir: Path) -> Optional[Path]:
    """Find the candidate with the highest reward/pass_rate."""
    import json

    best_score = -1.0
    best_path: Optional[Path] = None
    for d in experience_dir.iterdir():
        if not d.is_dir():
            continue
        scores_path = d / "scores.json"
        if not scores_path.exists():
            continue
        try:
            scores = json.loads(scores_path.read_text())
            reward = scores.get("mean_reward") or scores.get("pass_rate", 0)
            if reward > best_score:
                best_score = reward
                best_path = d
        except (json.JSONDecodeError, KeyError):
            continue
    return best_path


def _apply_to_latest(staging_dir: Path, experience_dir: Path, file_based: bool) -> None:
    """Copy the proposed config into a new candidate directory."""
    import json
    import shutil

    existing = [d.name for d in experience_dir.iterdir() if d.is_dir()]
    next_idx = len(existing)
    new_name = f"proposed_{next_idx:03d}"
    new_dir = experience_dir / new_name
    new_dir.mkdir(parents=True, exist_ok=True)

    if file_based:
        for item in staging_dir.iterdir():
            dest = new_dir / item.name
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)
    else:
        config_src = staging_dir / "config.py"
        if config_src.exists():
            shutil.copy2(config_src, new_dir / "config.py")

    print(f"[PROPOSE] Applied to {new_dir}")
    print(f"[PROPOSE] Re-run your agent with this config and ingest new traces to continue iterating.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the proposer once against ingested traces")
    parser.add_argument("--project", required=True, help="Project name (must match what was used in ingest)")
    parser.add_argument("--harness", default="claude_agent_sdk",
                        choices=["claude_agent_sdk", "claude_code", "codex"],
                        help="Harness type (determines what the proposer writes)")
    parser.add_argument("--model", default="claude-opus-4-6", help="Model for the proposer")
    parser.add_argument("--proposer-cli", default="claude", choices=["claude", "codex"],
                        help="CLI to use as the proposer agent")
    parser.add_argument("--apply", action="store_true",
                        help="Save the proposed config as a new candidate in the experience store")
    args = parser.parse_args()

    success = propose(
        project=args.project,
        harness=args.harness,
        model=args.model,
        proposer_cli=args.proposer_cli,
        apply=args.apply,
    )
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
