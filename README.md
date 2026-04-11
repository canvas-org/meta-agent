# meta-agent

Automatic harness optimization for AI agents. Given a set of tasks and an agent, meta-agent iteratively diagnoses failures from execution traces and proposes targeted harness improvements, validated on a held-out split.

**Results:**

- tau-bench v3 airline: 67% → 87% holdout accuracy
- ArtifactsBench (Codex): 40.9% → 68.8% average reward

See [WRITEUP.md](WRITEUP.md) for full methodology and results.

![results_graph.png](./images/results_graph.png)

## Prerequisites

- Python 3.11+
- Node.js 18+ (only if using `runtime: codex_sdk`)
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) (proposer + `claude_code` harness)
- `ANTHROPIC_API_KEY`

## Quick start

```bash
git clone https://github.com/canvas-org/meta-agent
cd meta-agent
pip install -e .
cp .env.example .env   # set ANTHROPIC_API_KEY
source .env

# Run a baseline eval on the example benchmark
python -m meta_agent.eval_runner \
    --benchmark benchmarks/example/benchmark.yaml \
    --config configs/vanilla.py \
    --name baseline \
    --model claude-haiku-4-5

# Run the optimization loop
python -m meta_agent.outer_loop \
    --benchmark benchmarks/example/benchmark.yaml \
    --iterations 5 \
    --model claude-haiku-4-5
```

## Supported harnesses

| Harness            | Config surface                              | Default runtime   | Proposer skill |
| ------------------ | ------------------------------------------- | ----------------- | -------------- |
| `codex`            | AGENTS.md, .codex/hooks.json, .codex/       | `codex_cli`       | SKILL_codex.md |
| `claude_code`      | CLAUDE.md (or AGENTS.md via import bridge)  | `claude_code_cli` | SKILL_codex.md |
| `claude_agent_sdk` | Python config (system prompt, hooks, tools) | `claude_sdk`      | SKILL.md       |

Specify the harness in your benchmark YAML:

```yaml
name: my-benchmark
harness: codex
# runtime is inferred from harness (codex → codex_cli)
```

To compare Codex CLI vs Codex SDK, keep `harness: codex` and set:

```yaml
runtime: codex_sdk
```

Then install the TS SDK runner dependencies once:

```bash
cd meta_agent/codex_sdk && npm install
```

## Optimize from existing traces

If you already have traces from your agent, you can skip the benchmark pipeline and go straight to optimization.

### 1. Prepare your traces

Create a directory with a `manifest.json` and one JSONL trace file per task:

```
my-traces/
├── manifest.json
├── task-1.jsonl
├── task-2.jsonl
└── ...
```

`manifest.json` is a list of tasks with pass/fail:

```json
[
  {"task_id": "task-1", "passed": true},
  {"task_id": "task-2", "passed": false},
  {"task_id": "task-3", "passed": false, "reward": 0.3}
]
```

Each `.jsonl` file is the agent's execution trace for that task (tool calls, reasoning, outputs). Any JSONL format works — the proposer reads the raw trace and reasons about it.

### 2. Ingest and propose

```bash
# Load traces into the experience store
python -m meta_agent.ingest \
    --traces ./my-traces/ \
    --project my-agent \
    --name baseline \
    --config ./my-harness/

# Run the proposer to get a harness diff
python -m meta_agent.propose \
    --project my-agent \
    --harness claude_code

# Or apply the proposed changes directly
python -m meta_agent.propose \
    --project my-agent \
    --harness claude_code \
    --apply
```

To iterate: apply the proposal, re-run your agent, ingest new traces with a new `--name`, and propose again. The proposer has memory of all prior candidates and avoids repeating failed approaches.

---

## Optimize with the benchmark pipeline

For automated end-to-end optimization (run agent + evaluate + propose + repeat), use the benchmark pipeline.

### 1. Define tasks

```yaml
name: my-app
tasks:
  - name: resolve-billing
    instruction: "Customer was double-charged. Look up their account and resolve it."
    workspace: ./workspaces/billing
    verify: ["python", "check.py"]
```

| Field         | Description                              |
| ------------- | ---------------------------------------- |
| `instruction` | Prompt given to the agent                |
| `workspace`   | Directory with files the agent needs     |
| `verify`      | Command to check success (exit 0 = pass) |
| `setup`       | Optional pre-run command                 |
| `timeout`     | Kill after N seconds (default: 300)      |

### 2. Write a baseline config

For `claude_agent_sdk` harness — a Python file exporting `build_options(ctx) -> ClaudeAgentOptions`:

```python
from claude_agent_sdk import ClaudeAgentOptions
from meta_agent.run_context import RunContext

def build_options(ctx: RunContext) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        system_prompt={"type": "preset", "preset": "claude_code"},
        cwd=ctx.cwd,
        model=ctx.model,
        permission_mode="bypassPermissions",
        max_turns=200,
        thinking={"type": "adaptive"},
    )
```

For `codex` or `claude_code` harness — write an AGENTS.md file in a config directory. See `configs/codex_vanilla/` for an example.

### 3. Run

```bash
# Baseline
python -m meta_agent.eval_runner \
    --benchmark path/to/benchmark.yaml \
    --config path/to/config \
    --name baseline \
    --model claude-haiku-4-5

# Optimize (--proposer-model is the model that reads traces and writes configs)
python -m meta_agent.outer_loop \
    --benchmark path/to/benchmark.yaml \
    --iterations 10 \
    --model claude-haiku-4-5 \
    --proposer-model claude-opus-4-6
```

Results go to `experience/<benchmark>/candidates/`.

## Reproducing results

### tau-bench (67% → 87%)

```bash
pip install "tau2 @ git+https://github.com/sierra-research/tau2-bench.git"

python -m meta_agent.outer_loop \
    --benchmark benchmarks/tau3/benchmark.yaml \
    --holdout-benchmark benchmarks/tau3/benchmark_holdout.yaml \
    --iterations 10 \
    --model claude-haiku-4-5 \
    --proposer-model claude-opus-4-6
```

### ArtifactsBench (40.9% → 68.8%)

Requires `GEMINI_API_KEY` for the VLM judge and the dataset parquet in `data/`.

```bash
pip install -e ".[artifacts]"
playwright install chromium

python -m meta_agent.outer_loop \
    --benchmark benchmarks/artifacts_bench/benchmark.yaml \
    --holdout-benchmark benchmarks/artifacts_bench/benchmark_holdout.yaml \
    --iterations 10 \
    --model gpt-5.3-codex \
    --proposer-model claude-opus-4-6 \
    --proposer-cli claude
```

## Workbench UI

A Next.js dashboard for browsing optimization results, traces, and harness diffs.

```bash
cd ui && npm install && npm run dev
```

## Project structure

```
meta_agent/                # Core library (outer loop, eval runner, task runner, CLI, dashboard)
benchmarks/
├── example/               # Local demo (fibonacci + calculator)
├── tau3/                  # tau-bench v3 (airline, retail)
├── artifacts_bench/       # ArtifactsBench (web applications)
└── swebench_m/            # SWE-bench Multimodal
configs/                   # Starter harness configs
ui/                        # Next.js workbench
images/                    # Writeup figures
SKILL.md                   # Proposer instructions (claude_agent_sdk)
SKILL_codex.md             # Proposer instructions (codex, claude_code)
WRITEUP.md                 # Results and methodology
```

## License

MIT
