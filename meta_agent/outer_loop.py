#!/usr/bin/env python3
from __future__ import annotations

"""Outer loop — evolves harness configs using Claude Code as the proposer.

Implements Algorithm 1 from the Meta-Harness paper:
  1. Invoke Claude Code with the proposer skill
  2. Claude Code reads experience store, diagnoses failures, writes new config
  3. Validate the new config (import, interface, smoke test)
  4. Evaluate on the search split via eval_runner
  5. Store results, repeat

Usage:
    python -m meta_agent.outer_loop \
        --iterations 5 \
        --model claude-haiku-4-5 \
        --tasks "cancel-async-tasks,filter-js-from-html,regex-log"
"""

import argparse
import importlib.util
import json
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from meta_agent.paths import PACKAGE_ROOT, get_experience_root, get_workspace_root, rel_to_workspace

try:
    from claude_agent_sdk import ClaudeAgentOptions
except ImportError:
    ClaudeAgentOptions = None  # type: ignore[assignment,misc]


SKILL_PATH = PACKAGE_ROOT / "SKILL.md"
SKILL_CODEX_PATH = PACKAGE_ROOT / "SKILL_codex.md"
SKILLS_DIR = get_experience_root() / "skills"

SPARK_CHARS = " ▁▂▃▄▅▆▇█"


def _spark(values: Any) -> str:
    vals = list(values)
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    span = hi - lo if hi > lo else 1.0
    return "".join(SPARK_CHARS[min(int((v - lo) / span * 8), 8)] for v in vals)


def import_time() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _run_proposer_cli(
    prompt: str,
    system_append: str,
    label: str,
    cli: str = "claude",
    trace_path: Optional[Path] = None,
    max_turns: int = 50,
    model: Optional[str] = None,
) -> int:
    """Run a proposer CLI with stream-json, print summaries, optionally save full trace.

    Returns the process exit code.
    """
    if cli == "codex":
        codex_danger = os.environ.get("CODEX_DANGEROUS_BYPASS", "").strip() in {"1", "true", "yes"}
        cmd = ["codex", "exec", "--json", "--skip-git-repo-check"]
        if not codex_danger:
            cmd.append("--full-auto")
        if model:
            cmd.extend(["--model", model])
        codex_sandbox = os.environ.get("CODEX_SANDBOX_MODE", "").strip()
        if codex_sandbox and not codex_danger:
            cmd.extend(["--sandbox", codex_sandbox])
        if codex_danger:
            cmd.append("--dangerously-bypass-approvals-and-sandbox")
        cmd.append(prompt)
    else:
        permission_mode = os.environ.get("CLAUDE_PERMISSION_MODE", "bypassPermissions").strip()

        if model and os.environ.get("CLAUDE_CODE_USE_BEDROCK") == "1":
            _bedrock_map = {
                "claude-haiku-4-5": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
                "claude-sonnet-4-6": "us.anthropic.claude-sonnet-4-6",
                "claude-opus-4-6": "us.anthropic.claude-opus-4-6-v1",
            }
            model = _bedrock_map.get(model, model)

        cmd = [
            "claude",
            "--print",
            "--verbose",
            "--output-format", "stream-json",
            "--append-system-prompt", system_append,
            "--allowedTools", "Read,Write,Edit,Bash,Glob,Grep",
            "--max-turns", str(max_turns),
            "-p", prompt,
        ]
        if model:
            cmd.extend(["--model", model])
        if permission_mode:
            cmd.extend(["--permission-mode", permission_mode])

    print(f"[LOOP] Invoking {label}...")
    sys.stdout.flush()

    trace_file = open(trace_path, "w") if trace_path else None

    try:
        process = subprocess.Popen(
            cmd,
            cwd=str(get_workspace_root()),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        for line in process.stdout:
            line = line.rstrip()
            if not line:
                continue
            if trace_file:
                trace_file.write(line + "\n")
            try:
                event = json.loads(line)
                event_type = event.get("type", "")
                if event_type == "assistant":
                    content = event.get("message", {}).get("content", [])
                    for block in content:
                        if block.get("type") == "text":
                            text = block["text"].strip()
                            if text:
                                print(f"  [{label.upper()}] {text[:300]}")
                elif event_type == "result":
                    cost = event.get("cost_usd", 0)
                    turns = event.get("num_turns", 0)
                    print(f"  [{label.upper()}] Done — {turns} turns, ${cost:.3f}")
            except json.JSONDecodeError:
                pass
            sys.stdout.flush()

        return process.wait()
    finally:
        if trace_file:
            trace_file.close()


def invoke_proposer(
    staging_dir: Path,
    experience_dir: Path,
    bench_name: str,
    trace_path: Optional[Path] = None,
    model: Optional[str] = None,
    harness: str = "claude_agent_sdk",
    proposer_cli: str = "claude",
) -> bool:
    """Invoke a proposer CLI to write a new config."""
    from meta_agent.benchmark import FILE_BASED_HARNESSES

    staging_dir.mkdir(parents=True, exist_ok=True)

    for item in staging_dir.iterdir():
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()

    exp_rel = rel_to_workspace(experience_dir)
    staging_rel = rel_to_workspace(staging_dir)

    if harness == "codex":
        skill_path = SKILL_CODEX_PATH
        output_instruction = (
            f"write improved harness files to {staging_rel}/. "
            f"This includes AGENTS.md, .codex/hooks.json, .codex/hooks/*.sh, "
            f".codex/config.toml, .codex/skills/*.md, and .codex/agents/*.md"
        )
    elif harness == "claude_code":
        skill_path = SKILL_CODEX_PATH
        output_instruction = (
            f"write an improved CLAUDE.md (or AGENTS.md plus CLAUDE.md that imports it), "
            f"and optionally .claude/rules/*.md to {staging_rel}/"
        )
    else:
        skill_path = SKILL_PATH
        output_instruction = f"write an improved config module to {staging_rel}/config.py"

    prompt = (
        f"Read the {skill_path.name} file first, then follow its instructions. "
        f"You are optimizing for the '{bench_name}' benchmark. "
        f"The experience store for this benchmark is at '{exp_rel}/'. "
        f"Use `python -m meta_agent.cli --dir {exp_rel} list` to see prior candidates. "
        f"Use `python -m meta_agent.cli --dir {exp_rel} show <name>` or `failures <name>` for details. "
        f"Examine the experience store, diagnose failures in the current best candidate, "
        f"and {output_instruction}"
    )

    system_append = f"Read {skill_path} for your full instructions."

    rc = _run_proposer_cli(
        prompt=prompt,
        system_append=system_append,
        label="proposer",
        cli=proposer_cli,
        trace_path=trace_path,
        model=model,
    )

    if rc != 0:
        print(f"[LOOP] Proposer exited with code {rc}")
        return False

    if harness == "codex":
        agents_md = staging_dir / "AGENTS.md"
        if not agents_md.exists():
            print(f"[LOOP] Proposer did not write {agents_md}")
            return False
        print(f"[LOOP] Proposer wrote {harness} harness to {staging_dir}")
    elif harness == "claude_code":
        claude_md = staging_dir / "CLAUDE.md"
        agents_md = staging_dir / "AGENTS.md"
        if not claude_md.exists() and not agents_md.exists():
            print(f"[LOOP] Proposer did not write CLAUDE.md or AGENTS.md in {staging_dir}")
            return False
        print(f"[LOOP] Proposer wrote {harness} harness to {staging_dir}")
    else:
        config_path = staging_dir / "config.py"
        if not config_path.exists():
            print(f"[LOOP] Proposer did not write {config_path}")
            return False
        print(f"[LOOP] Proposer wrote config to {config_path}")

    return True


SKILL_EVOLVER_PROMPT_TEMPLATE = """\
You are improving the skill document (SKILL.md) that guides a harness optimization proposer.

The proposer is a coding agent that reads execution traces from failed tasks, diagnoses why \
they failed, and writes improved harness configs. SKILL.md tells it how to do this — what to \
read, what to change, what to avoid, how to reason.

Your job: analyze how the proposer actually behaved over the last {n_iters} iterations \
({iter_names}), compare that to the outcomes (did pass rate improve?), and make targeted \
edits to SKILL.md that correct bad patterns or reinforce good ones.

## What you have

1. The current SKILL.md at the project root.
2. Proposer reasoning traces at {exp_dir}/<name>/proposer_trace.jsonl — these \
show every file the proposer read, every tool call, its reasoning (ThinkingBlocks).
3. Scores at {exp_dir}/<name>/scores.json — pass_rate, cost, tasks passed/failed.
4. The configs the proposer wrote at {exp_dir}/<name>/config.py.

Analyze iterations: {iter_names}

## What to look for

Read the proposer traces and scores. Identify:

- REPEATED FAILURES: Does the proposer keep trying a class of change that consistently \
regresses? (e.g. modifying prompt templates, changing hook logic, adding subagents) \
→ Add a warning or constraint to SKILL.md about that pattern.

- MISSED SIGNALS: Does the proposer skip reading traces for certain tasks, or always \
start from the same parent candidate, or never use `cli diff`? \
→ Add a process step reminding it.

- BUNDLED CHANGES: Does the proposer stack multiple unrelated changes despite the skill \
saying "one change at a time"? \
→ Strengthen the constraint with a concrete example of what went wrong.

- SUCCESSFUL PATTERNS: Did certain types of changes consistently improve pass rate? \
→ Add a positive heuristic (e.g. "additive modifications that don't touch existing \
logic are safer than structural rewrites").

- STAGNATION: Is the proposer cycling through similar ideas without progress? \
→ Add guidance to try a fundamentally different lever.

## Rules

- Make TARGETED edits to the existing SKILL.md. Do NOT rewrite it from scratch.
- Add at most 3 new observations or refinements per evolution step.
- Do NOT add task-specific guidance (no "for task X, try Y").
- Do NOT change the config module contract (build_options signature, ClaudeAgentOptions).
- Do NOT change the directory layout, CLI, or SDK reference sections — those are factual.
- Focus on PROCESS guidance (how to reason, what to inspect, what to avoid) \
not CONTENT guidance (what specific hooks to write).
- If the proposer is improving consistently, make minimal or no changes.
- Preserve all existing sections. Add new guidance inline or append a \
"## Lessons learned" section.

## Output

Write the updated skill to {staging_dir}/SKILL.md
Write a brief (3-5 sentence) summary of what you changed and why to \
{staging_dir}/skill_evolution_notes.md
"""


def _load_skill_history() -> list[dict[str, Any]]:
    path = SKILLS_DIR / "history.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text()).get("versions", [])
    except (json.JSONDecodeError, KeyError):
        return []


def _save_skill_history(versions: list[dict[str, Any]]) -> None:
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    (SKILLS_DIR / "history.json").write_text(
        json.dumps({"versions": versions}, indent=2)
    )


def _backup_skill(version: int) -> Path:
    """Copy current SKILL.md to the versioned archive."""
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    dest = SKILLS_DIR / f"SKILL_v{version:03d}.md"
    if SKILL_PATH.exists():
        shutil.copy2(SKILL_PATH, dest)
    return dest


def validate_skill(skill_path: Path) -> bool:
    """Basic sanity checks on an evolved skill document."""
    if not skill_path.exists():
        print("[LOOP] FAIL: Evolved skill file not found")
        return False

    content = skill_path.read_text()

    if len(content) < 200:
        print("[LOOP] FAIL: Evolved skill is suspiciously short")
        return False

    required = ["build_options", "ClaudeAgentOptions", "experience/staging/config.py"]
    for token in required:
        if token not in content:
            print(f"[LOOP] FAIL: Evolved skill is missing required reference: {token}")
            return False

    if SKILL_PATH.exists():
        original_len = len(SKILL_PATH.read_text())
        if original_len > 0 and len(content) > original_len * 2:
            print(f"[LOOP] FAIL: Evolved skill is >2x the original size ({len(content)} vs {original_len} chars)")
            return False

    print("[LOOP] PASS: Evolved skill is valid")
    return True


def invoke_skill_evolver(
    iterations_analyzed: list[str],
    staging_dir: Path,
    experience_dir: Path,
    model: Optional[str] = None,
) -> bool:
    """Run the meta-proposer to evolve SKILL.md based on proposer behavior."""
    staging_dir.mkdir(parents=True, exist_ok=True)
    for f in staging_dir.iterdir():
        if f.name in ("SKILL.md", "skill_evolution_notes.md"):
            f.unlink()

    iter_names = ", ".join(iterations_analyzed)
    exp_rel = rel_to_workspace(experience_dir)
    staging_rel = rel_to_workspace(staging_dir)
    prompt = SKILL_EVOLVER_PROMPT_TEMPLATE.format(
        n_iters=len(iterations_analyzed),
        iter_names=iter_names,
        exp_dir=exp_rel,
        staging_dir=staging_rel,
    )

    trace_path = SKILLS_DIR / f"evolver_trace_v{len(_load_skill_history()):03d}.jsonl"
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)

    rc = _run_proposer_cli(
        prompt=prompt,
        system_append="You are a meta-proposer improving a skill document. Read SKILL.md first, then analyze the proposer traces.",
        label="skill-evolver",
        trace_path=trace_path,
        max_turns=30,
        model=model,
    )

    if rc != 0:
        print(f"[LOOP] Skill evolver exited with code {rc}")
        return False

    staged_skill = staging_dir / "SKILL.md"
    if not staged_skill.exists():
        print(f"[LOOP] Skill evolver did not write {staging_dir}/SKILL.md")
        return False

    if not validate_skill(staged_skill):
        print("[LOOP] Evolved skill failed validation, keeping current SKILL.md")
        return False

    versions = _load_skill_history()

    if not versions and SKILL_PATH.exists():
        _backup_skill(0)
        versions.append({"version": 0, "path": "SKILL_v000.md", "source": "original"})

    next_version = max((v["version"] for v in versions), default=-1) + 1

    shutil.copy2(staged_skill, SKILL_PATH)
    _backup_skill(next_version)

    versions.append({
        "version": next_version,
        "path": f"SKILL_v{next_version:03d}.md",
        "source": "evolved",
        "iterations_analyzed": iterations_analyzed,
        "timestamp": import_time(),
    })
    _save_skill_history(versions)

    notes_path = staging_dir / "skill_evolution_notes.md"
    notes = notes_path.read_text() if notes_path.exists() else "(no notes)"
    print(f"[LOOP] Skill evolved to v{next_version}: {notes[:200]}")
    return True


def validate_config(config_path: Path, bench_type: str = "local", harness: str = "claude_agent_sdk") -> bool:
    """Validate a config for the given benchmark type and harness."""
    from meta_agent.benchmark import FILE_BASED_HARNESSES

    print(f"[LOOP] Validating {config_path} (type={bench_type}, harness={harness})...")

    if harness in FILE_BASED_HARNESSES:
        config_dir = config_path if config_path.is_dir() else config_path.parent
        if harness == "codex":
            agents_md = config_dir / "AGENTS.md"
            if not agents_md.exists():
                print(f"[LOOP] FAIL: No AGENTS.md found in {config_dir}")
                return False
        elif harness == "claude_code":
            claude_md = config_dir / "CLAUDE.md"
            agents_md = config_dir / "AGENTS.md"
            if not claude_md.exists() and not agents_md.exists():
                print(f"[LOOP] FAIL: No CLAUDE.md (or AGENTS.md fallback) found in {config_dir}")
                return False

        hooks_json = config_dir / ".codex" / "hooks.json"
        if hooks_json.exists():
            try:
                json.loads(hooks_json.read_text())
            except json.JSONDecodeError as e:
                print(f"[LOOP] FAIL: .codex/hooks.json is not valid JSON: {e}")
                return False

        codex_toml = config_dir / ".codex" / "config.toml"
        if codex_toml.exists():
            try:
                import tomllib as _tomllib  # type: ignore

                _tomllib.loads(codex_toml.read_text())
            except ModuleNotFoundError:
                pass
            except Exception as e:
                print(f"[LOOP] FAIL: .codex/config.toml is not valid TOML: {e}")
                return False

        print(f"[LOOP] PASS: {harness} config is valid")
        return True

    try:
        spec = importlib.util.spec_from_file_location("candidate_config", str(config_path))
        if spec is None or spec.loader is None:
            print(f"[LOOP] FAIL: Cannot create module spec")
            return False
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as e:
        print(f"[LOOP] FAIL: Import error: {e}")
        return False

    if not hasattr(module, "build_options"):
        print(f"[LOOP] FAIL: No build_options function")
        return False

    if not callable(module.build_options):
        print(f"[LOOP] FAIL: build_options is not callable")
        return False

    try:
        from meta_agent.run_context import RunContext
        ctx = RunContext(cwd="/app", model="claude-haiku-4-5", task_instruction="test")
        options = module.build_options(ctx)
        if not isinstance(options, ClaudeAgentOptions):
            print(f"[LOOP] FAIL: build_options returned {type(options).__name__}, expected ClaudeAgentOptions")
            return False
    except Exception as e:
        print(f"[LOOP] FAIL: build_options(ctx) raised: {e}")
        return False

    print(f"[LOOP] PASS: Config is valid")
    return True


def run_evaluation(
    config_path: Path,
    name: str,
    model: str,
    benchmark_path: str,
    fast: bool,
    tasks: Optional[str],
    concurrency: int,
    experience_dir: Optional[Path] = None,
) -> Optional[dict[str, Any]]:
    """Run eval_runner and return scores."""
    cmd = [
        sys.executable, "-m", "meta_agent.eval_runner",
        "--benchmark", benchmark_path,
        "--config", str(config_path),
        "--name", name,
        "--model", model,
        "--concurrency", str(concurrency),
    ]
    if fast:
        cmd.append("--fast")
    elif tasks:
        cmd.extend(["--tasks", tasks])

    print(f"[LOOP] Running evaluation: {name}")
    result = subprocess.run(cmd, cwd=str(get_workspace_root()))

    if result.returncode != 0:
        print(f"[LOOP] Evaluation failed with code {result.returncode}")
        return None

    exp_dir = experience_dir or (get_experience_root() / "candidates")
    scores_path = exp_dir / name / "scores.json"
    if not scores_path.exists():
        print(f"[LOOP] No scores.json found")
        return None

    return json.loads(scores_path.read_text())


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the harness optimization loop")
    parser.add_argument("--benchmark", required=True, help="Path to benchmark YAML")
    parser.add_argument("--iterations", type=int, default=5, help="Number of evolution iterations")
    parser.add_argument("--model", default="claude-haiku-4-5", help="Model for evaluation")
    parser.add_argument("--fast", action="store_true", help="Use benchmark's fast_tasks subset")
    parser.add_argument("--concurrency", type=int, default=4, help="Parallel task count")
    parser.add_argument("--start-from", type=int, default=1, help="Starting iteration number (for resuming)")
    parser.add_argument("--proposer-model", default="claude-opus-4-6",
                        help="Model for the proposer agent (default: claude-opus-4-6)")
    parser.add_argument("--baseline", default=None, nargs="?", const="configs/vanilla.py",
                        help="Run a baseline config before the loop (default: configs/vanilla.py)")
    parser.add_argument("--evolve-skill", action="store_true", help="Enable skill co-evolution (meta-proposer rewrites SKILL.md periodically)")
    parser.add_argument("--skill-evolve-every", type=int, default=5, help="Run skill evolution every N iterations (requires --evolve-skill)")
    parser.add_argument("--holdout-benchmark", default=None,
                        help="Path to held-out benchmark YAML for per-epoch validation (traces not visible to proposer)")
    parser.add_argument("--proposer-cli", default="claude",
                        choices=["claude", "codex"],
                        help="CLI to use as the proposer agent (default: claude)")
    parser.add_argument("--batch-size", type=int, default=None,
                        help="Number of search tasks per epoch (samples from full pool). If omitted, uses all tasks.")
    parser.add_argument("--seed", type=int, default=None,
                        help="Random seed for task batching (reproducible shuffling). If omitted, non-deterministic.")
    args = parser.parse_args()

    from meta_agent.benchmark import load_benchmark, FILE_BASED_HARNESSES
    bench = load_benchmark(args.benchmark)

    experience_dir = get_experience_root() / bench.name / "candidates"
    staging_dir = get_experience_root() / bench.name / "staging"
    experience_dir.mkdir(parents=True, exist_ok=True)

    holdout_dir: Optional[Path] = None
    if args.holdout_benchmark:
        holdout_bench = load_benchmark(args.holdout_benchmark)
        holdout_dir = get_experience_root() / holdout_bench.name / "candidates"
        holdout_dir.mkdir(parents=True, exist_ok=True)

    if not SKILL_PATH.exists():
        print(f"[LOOP] ERROR: {SKILL_PATH} not found")
        sys.exit(1)

    # Build the full task pool for batching
    all_task_names: list[str] = []
    if args.fast and bench.fast_tasks:
        all_task_names = list(bench.fast_tasks)
    elif bench.tasks:
        all_task_names = [t.name for t in bench.tasks]
    elif bench.artifacts_backend and bench.artifacts_backend.task_indexes:
        all_task_names = [str(idx) for idx in bench.artifacts_backend.task_indexes]
    elif bench.tau_backend and bench.tau_backend.task_ids:
        all_task_names = list(bench.tau_backend.task_ids)
    elif bench.swebench_backend and bench.swebench_backend.task_ids:
        all_task_names = list(bench.swebench_backend.task_ids)

    # Set up batch iterator (DataLoader-style: shuffle, slice, reshuffle on exhaustion)
    batch_size = args.batch_size
    batch_rng = random.Random(args.seed) if args.seed is not None else random.Random()
    batch_queue: list[str] = []

    def _pop_batch() -> list[str]:
        """Pop one batch from the queue, refilling with a fresh shuffle when needed."""
        nonlocal batch_queue
        if len(batch_queue) < batch_size:
            fresh = list(all_task_names)
            batch_rng.shuffle(fresh)
            batch_queue.extend(fresh)
        batch = batch_queue[:batch_size]
        batch_queue = batch_queue[batch_size:]
        return batch

    def next_batch() -> Optional[str]:
        """Return comma-separated task names for the next batch, or None for all tasks."""
        if batch_size is None or not all_task_names:
            return None
        return ",".join(_pop_batch())

    # When resuming (--start-from > 1), advance the RNG past already-consumed
    # batches so each epoch gets a distinct sample. Each prior epoch consumed
    # one batch; the baseline (if present) also consumed one.
    if batch_size and args.start_from > 1:
        has_baseline = args.baseline is not None and any(
            (d / "scores.json").exists()
            for d in (Path(get_workspace_root()) / "experience" / bench.name / "candidates").iterdir()
            if d.is_dir() and d.name == "baseline"
        ) if (Path(get_workspace_root()) / "experience" / bench.name / "candidates").exists() else False
        n_skip = (args.start_from - 1) + (1 if has_baseline else 0)
        for _ in range(n_skip):
            _pop_batch()
        print(f"[LOOP] Batch RNG: skipped {n_skip} batches for resume at start_from={args.start_from}")

    print(f"[LOOP] === Harness Optimizer Outer Loop ===")
    print(f"[LOOP] Benchmark: {bench.name} (type={bench.type}, harness={bench.harness}, runtime={bench.runtime})")
    print(f"[LOOP] Experience: {rel_to_workspace(experience_dir)}")
    print(f"[LOOP] Iterations: {args.iterations}")
    print(f"[LOOP] Eval model: {args.model}")
    print(f"[LOOP] Proposer model: {args.proposer_model}")
    print(f"[LOOP] Concurrency: {args.concurrency}")
    print(f"[LOOP] Fast: {args.fast}")
    print(f"[LOOP] Task pool: {len(all_task_names)} tasks")
    if batch_size:
        print(f"[LOOP] Batch size: {batch_size} (seed={args.seed})")
    if args.evolve_skill:
        print(f"[LOOP] Skill evolution: every {args.skill_evolve_every} iterations")
    if holdout_dir:
        print(f"[LOOP] Holdout: {args.holdout_benchmark}")
    print()

    cli_dir = str(experience_dir)
    subprocess.run([sys.executable, "-m", "meta_agent.cli", "--dir", cli_dir, "list"], cwd=str(get_workspace_root()))
    print()

    has_candidates = any(
        (d / "scores.json").exists()
        for d in experience_dir.iterdir()
        if d.is_dir()
    ) if experience_dir.exists() else False

    if args.baseline is not None and not has_candidates:
        baseline_config = args.baseline
        baseline_batch = next_batch()
        if baseline_batch:
            print(f"[LOOP] Running baseline: {baseline_config} (batch: {baseline_batch})")
        else:
            print(f"[LOOP] Running baseline: {baseline_config}")
        baseline_scores = run_evaluation(
            config_path=Path(baseline_config),
            name="baseline",
            model=args.model,
            benchmark_path=args.benchmark,
            fast=args.fast if not baseline_batch else False,
            tasks=baseline_batch,
            concurrency=args.concurrency,
            experience_dir=experience_dir,
        )
        if baseline_scores:
            rate = baseline_scores["pass_rate"]
            print(f"[LOOP] Baseline: {baseline_scores['n_passed']}/{baseline_scores['n_tasks']} ({rate:.0%})")
        else:
            print("[LOOP] Baseline evaluation failed")
        print()
        subprocess.run([sys.executable, "-m", "meta_agent.cli", "--dir", cli_dir, "list"], cwd=str(get_workspace_root()))
        print()

    history: list[dict[str, Any]] = []
    history_path = get_experience_root() / bench.name / "history.json"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    if history_path.exists():
        try:
            history = json.loads(history_path.read_text()).get("iterations", [])
        except (json.JSONDecodeError, KeyError):
            pass

    experiment_config: dict[str, Any] = {
        "description": bench.description or None,
        "harness": bench.harness,
        "runtime": bench.runtime,
        "bench_type": bench.type,
        "n_search_tasks": len(all_task_names),
        "n_total_tasks": len(all_task_names),
        "batch_size": batch_size,
        "seed": args.seed,
        "holdout_benchmark": args.holdout_benchmark or None,
        "proposer_model": args.proposer_model,
        "proposer_cli": getattr(args, "proposer_cli", "claude"),
        "max_iterations": args.iterations,
        "concurrency": args.concurrency,
        "fast": args.fast,
        "timeout": getattr(bench, "backend", None) and getattr(bench.backend, "timeout", None),
    }

    def _write_history() -> None:
        history_path.write_text(json.dumps({
            "benchmark": bench.name,
            "model": args.model,
            "config": experiment_config,
            "iterations": history,
        }, indent=2))

    if not history:
        baseline_scores_path = experience_dir / "baseline" / "scores.json"
        if baseline_scores_path.exists():
            bs = json.loads(baseline_scores_path.read_text())
            history.append({
                "name": "baseline",
                "reward": bs.get("mean_reward") or bs["pass_rate"],
                "pass_rate": bs["pass_rate"],
                "n_passed": bs["n_passed"],
                "n_tasks": bs["n_tasks"],
                "cost_usd": bs.get("total_cost_usd"),
                "timestamp": import_time(),
            })
            _write_history()

    best_rate = max((h.get("reward", h.get("pass_rate", 0)) for h in history), default=0.0)
    iterations_since_skill_evolve: list[str] = []

    for i in range(args.start_from, args.start_from + args.iterations):
        evo_name = f"evo_{i:03d}"
        total_iters = args.start_from + args.iterations - 1
        print(f"\n{'='*60}")
        print(f"  EPOCH {i}/{total_iters}  ({evo_name})")
        print(f"{'='*60}")
        print(f"\n  [1/3] Proposing new config...")

        candidate_dir = experience_dir / evo_name
        candidate_dir.mkdir(parents=True, exist_ok=True)
        proposer_trace = candidate_dir / "proposer_trace.jsonl"

        success = invoke_proposer(
            staging_dir=staging_dir,
            experience_dir=experience_dir,
            bench_name=bench.name,
            trace_path=proposer_trace,
            model=args.proposer_model,
            harness=bench.harness,
            proposer_cli=args.proposer_cli,
        )
        if not success:
            print(f"[LOOP] Proposer failed, skipping iteration {i}")
            continue

        file_based = bench.harness in FILE_BASED_HARNESSES
        config_path = staging_dir if file_based else staging_dir / "config.py"
        print(f"  [2/3] Validating config...")
        if not validate_config(config_path, bench_type=bench.type, harness=bench.harness):
            print(f"  [2/3] FAILED — skipping epoch {i}")
            continue

        if file_based:
            src_dir = staging_dir if staging_dir.is_dir() else config_path.parent
            for item in src_dir.iterdir():
                dest = candidate_dir / item.name
                if item.is_dir():
                    shutil.copytree(item, dest, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dest)
        else:
            shutil.copy2(config_path, candidate_dir / "config.py")

        print(f"  [3/3] Evaluating on benchmark...")
        eval_config = candidate_dir if file_based else candidate_dir / "config.py"
        batch_tasks = next_batch()
        if batch_tasks:
            print(f"  [BATCH] Tasks: {batch_tasks}")
        scores = run_evaluation(
            config_path=eval_config,
            name=evo_name,
            model=args.model,
            benchmark_path=args.benchmark,
            fast=args.fast if not batch_tasks else False,
            tasks=batch_tasks,
            concurrency=args.concurrency,
            experience_dir=experience_dir,
        )

        if scores:
            reward = scores.get("mean_reward") or scores["pass_rate"]
            cost = scores.get("total_cost_usd") or 0
            is_best = reward > best_rate
            if is_best:
                best_rate = reward
            arrow = " *** NEW BEST ***" if is_best else ""

            print(f"\n  {'─'*50}")
            print(f"  EPOCH {i} RESULT: {reward:.1%}  cost=${cost:.3f}{arrow}")
            print(f"  Best so far: {best_rate:.1%}")

            history.append({
                "name": evo_name,
                "reward": reward,
                "pass_rate": scores["pass_rate"],
                "n_passed": scores["n_passed"],
                "n_tasks": scores["n_tasks"],
                "cost_usd": cost,
                "timestamp": import_time(),
            })
            _write_history()

            rates = " -> ".join(f"{h.get('reward', h.get('pass_rate', 0)):.0%}" for h in history[-8:])
            spark = _spark(h.get("reward", h.get("pass_rate", 0)) for h in history)
            print(f"  History: {rates}  {spark}")

            if holdout_dir and args.holdout_benchmark:
                holdout_name = f"{evo_name}_holdout"
                print(f"  [HOLDOUT] Evaluating on held-out split...")
                holdout_scores = run_evaluation(
                    config_path=candidate_dir if file_based else candidate_dir / "config.py",
                    name=holdout_name,
                    model=args.model,
                    benchmark_path=args.holdout_benchmark,
                    fast=False,
                    tasks=None,
                    concurrency=args.concurrency,
                    experience_dir=holdout_dir,
                )
                if holdout_scores:
                    ho_reward = holdout_scores.get("mean_reward") or holdout_scores["pass_rate"]
                    ho_cost = holdout_scores.get("total_cost_usd") or 0
                    print(f"  [HOLDOUT] {ho_reward:.1%}  cost=${ho_cost:.3f}")
                    history[-1]["holdout_reward"] = ho_reward
                    history[-1]["holdout_cost"] = ho_cost
                    history[-1]["holdout_n_passed"] = holdout_scores.get("n_passed", 0)
                    history[-1]["holdout_n_tasks"] = holdout_scores.get("n_tasks", 0)
                    history[-1]["holdout_pass_rate"] = holdout_scores.get("pass_rate", 0)
                    _write_history()
                else:
                    print(f"  [HOLDOUT] FAILED")

            print(f"  {'─'*50}")
        else:
            print(f"\n  EPOCH {i} RESULT: FAILED (no scores)")

        iterations_since_skill_evolve.append(evo_name)

        if (
            args.evolve_skill
            and len(iterations_since_skill_evolve) >= args.skill_evolve_every
        ):
            print(f"\n{'='*60}")
            print(f"  Skill Evolution — analyzing {len(iterations_since_skill_evolve)} iterations")
            print(f"{'='*60}\n")
            evolved = invoke_skill_evolver(
                iterations_since_skill_evolve,
                staging_dir=staging_dir,
                experience_dir=experience_dir,
                model=args.proposer_model,
            )
            if evolved:
                iterations_since_skill_evolve = []
            else:
                print("[LOOP] Skill evolution failed, continuing with current SKILL.md")

        print()
        subprocess.run([sys.executable, "-m", "meta_agent.cli", "--dir", cli_dir, "list"], cwd=str(get_workspace_root()))

    print(f"\n{'='*60}")
    print(f"  Evolution complete — {len(history)} iterations")
    print(f"  Best: {best_rate:.0%}")
    print(f"{'='*60}\n")
    subprocess.run([sys.executable, "-m", "meta_agent.cli", "--dir", cli_dir, "list"], cwd=str(get_workspace_root()))


if __name__ == "__main__":
    main()
