#!/usr/bin/env bash
set -euo pipefail

VOLUME="harness-optimizer-runs-v1"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
EXPERIENCE_DIR="$PROJECT_ROOT/experience"

usage() {
  local code="${1:-1}"
  cat <<EOF
Usage:
  $0 --list
  $0 <run-id> [--benchmark <name>] [--candidate <name>] [--with-harness] [--with-per-task] [--clean-benchmark]

Description:
  Sync cloud run data from Modal volume into local experience/.
  Default sync is lightweight: history.json + candidate scores/summary only.
  Use --with-harness and --with-per-task for deeper candidate-level sync.

Examples:
  $0 --list
  $0 b50-h50-baseline-i10-v2-0411-0804
  $0 b50-h50-baseline-i10-v2-0411-0804 --benchmark artifacts-bench-search --clean-benchmark
  $0 b50-h50-baseline-i10-v2-0411-0804 --benchmark artifacts-bench-search --candidate evo_004 --with-harness --with-per-task
EOF
  exit "$code"
}

if [[ "${1:-}" == "--list" ]]; then
  echo "Available runs on Modal volume '$VOLUME':"
  modal volume ls "$VOLUME" /runs/
  exit 0
fi

RUN_ID=""
TARGET_BENCH=""
TARGET_CANDIDATE=""
WITH_HARNESS=0
WITH_PER_TASK=0
CLEAN_BENCHMARK=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --benchmark)
      [[ $# -lt 2 ]] && usage 1
      TARGET_BENCH="$2"
      shift 2
      ;;
    --candidate)
      [[ $# -lt 2 ]] && usage 1
      TARGET_CANDIDATE="$2"
      shift 2
      ;;
    --with-harness)
      WITH_HARNESS=1
      shift
      ;;
    --with-per-task)
      WITH_PER_TASK=1
      shift
      ;;
    --clean-benchmark)
      CLEAN_BENCHMARK=1
      shift
      ;;
    -h|--help)
      usage 0
      ;;
    -*)
      echo "Unknown option: $1" >&2
      usage 1
      ;;
    *)
      if [[ -z "$RUN_ID" ]]; then
        RUN_ID="$1"
      else
        echo "Unexpected extra argument: $1" >&2
        usage 1
      fi
      shift
      ;;
  esac
done

if [[ -z "$RUN_ID" ]]; then
  usage 1
fi
if [[ -n "$TARGET_CANDIDATE" && -z "$TARGET_BENCH" ]]; then
  echo "--candidate requires --benchmark" >&2
  exit 1
fi

REMOTE_ROOT="/runs/$RUN_ID/experience"
echo "Syncing run '$RUN_ID' from Modal volume..."
echo "  Remote: $VOLUME:$REMOTE_ROOT"
echo "  Local:  $EXPERIENCE_DIR"
echo ""

modal volume ls "$VOLUME" "$REMOTE_ROOT" 2>/dev/null >/dev/null || {
  echo "Error: no experience data found for run '$RUN_ID'" >&2
  exit 1
}

bench_paths=()
if [[ -n "$TARGET_BENCH" ]]; then
  bench_paths=("$REMOTE_ROOT/$TARGET_BENCH")
else
  while IFS= read -r bench_path; do
    [[ -n "$bench_path" ]] && bench_paths+=("$bench_path")
  done < <(modal volume ls "$VOLUME" "$REMOTE_ROOT" 2>/dev/null | awk '{print $NF}')
fi

for bench_path in "${bench_paths[@]}"; do
  [[ -z "$bench_path" ]] && continue
  bench_name="$(basename "$bench_path")"
  local_bench="$EXPERIENCE_DIR/$bench_name"
  candidates_remote="$bench_path/candidates"

  echo "Syncing benchmark: $bench_name"
  mkdir -p "$local_bench"
  if [[ "$CLEAN_BENCHMARK" -eq 1 && -z "$TARGET_CANDIDATE" ]]; then
    rm -rf "$local_bench/candidates"
  fi

  # Always sync history for trajectory and holdout lines.
  modal volume get --force "$VOLUME" "$bench_path/history.json" "$local_bench/history.json" 2>/dev/null || true

  cand_paths=()
  if [[ -n "$TARGET_CANDIDATE" ]]; then
    cand_paths=("$candidates_remote/$TARGET_CANDIDATE")
  else
    while IFS= read -r cand_path; do
      [[ -n "$cand_path" ]] && cand_paths+=("$cand_path")
    done < <(modal volume ls "$VOLUME" "$candidates_remote" 2>/dev/null | awk '{print $NF}')
  fi

  for cand_path in "${cand_paths[@]}"; do
    [[ -z "$cand_path" ]] && continue
    cand_name="$(basename "$cand_path")"
    local_cand="$local_bench/candidates/$cand_name"
    mkdir -p "$local_cand"

    # Lightweight defaults for dashboard/overview.
    modal volume get --force "$VOLUME" "$cand_path/scores.json" "$local_cand/scores.json" 2>/dev/null || true
    modal volume get --force "$VOLUME" "$cand_path/summary.md" "$local_cand/summary.md" 2>/dev/null || true

    if [[ "$WITH_HARNESS" -eq 1 ]]; then
      modal volume get --force "$VOLUME" "$cand_path/AGENTS.md" "$local_cand/AGENTS.md" 2>/dev/null || true
      modal volume get --force "$VOLUME" "$cand_path/CLAUDE.md" "$local_cand/CLAUDE.md" 2>/dev/null || true
      modal volume get --force "$VOLUME" "$cand_path/config.py" "$local_cand/config.py" 2>/dev/null || true
      modal volume get --force "$VOLUME" "$cand_path/.codex" "$local_cand/.codex" 2>/dev/null || true
      modal volume get --force "$VOLUME" "$cand_path/.claude" "$local_cand/.claude" 2>/dev/null || true
    fi

    if [[ "$WITH_PER_TASK" -eq 1 ]]; then
      local_per_task="$local_cand/per_task"
      rm -rf "$local_per_task"
      mkdir -p "$local_per_task"
      while IFS= read -r remote_task_file; do
        [[ -z "$remote_task_file" ]] && continue
        task_file_name="$(basename "$remote_task_file")"
        # Dashboard task table only needs per-task JSON scores.
        if [[ "$task_file_name" != *.json ]] || [[ "$task_file_name" == *_agent_result.json ]]; then
          continue
        fi
        modal volume get --force "$VOLUME" "$remote_task_file" "$local_per_task/$task_file_name" 2>/dev/null || true
      done < <(modal volume ls "$VOLUME" "$cand_path/per_task" 2>/dev/null | awk '{print $NF}')
    fi

    echo "  ✓ $cand_name"
  done
done

echo ""
echo "Sync complete."
