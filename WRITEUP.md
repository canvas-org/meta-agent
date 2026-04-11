**We built [meta-agent](https://github.com/canvas-org/meta-agent): an open-source library that automatically improves agent harnesses from execution traces.**

Point it at an existing agent and a set of tasks. A proposer reads failed traces and writes one targeted harness update at a time — changes to prompts, hooks, tools, or configuration files. The update is kept only if it improves holdout accuracy.

- tau-bench v3 airline: 67% → 87% holdout accuracy
- ArtifactsBench web applications (Codex): 40.9% → 68.8% average reward

meta-agent supports three harness types: Claude Agent SDK (Python config), Claude Code (AGENTS.md), and Codex (AGENTS.md + hooks). Try it here: [github.com/canvas-org/meta-agent](https://github.com/canvas-org/meta-agent)

## Why

Recent work shows that optimizing the harness layer can materially improve agent performance. On TerminalBench-2, vanilla Claude Code with Claude Haiku 4.5 scores 27.5%. The best hand-engineered harness on the same model reaches 35.5%, with no fine-tuning ([Meta-Harness, Lee et al. 2026](https://arxiv.org/abs/2603.28052)).

Recent systems have also shown that harnesses can be improved automatically through iterative search ([Autoresearch, Karpathy 2026](https://github.com/karpathy/autoresearch); [Meta-Harness, Lee et al. 2026](https://arxiv.org/abs/2603.28052)). But those methods usually depend on strong evaluation signals during search, such as labels, tests, or deterministic checks. Agents typically run on messy customer workflows where labeled reward is sparse or unavailable.

meta-agent is built for exactly that production setting: it reads traces from the running agent, uses an LLM judge to score them, and proposes harness updates from the failure patterns it finds.

## How it works

You provide an existing agent, a stream of unlabeled production traces, and a small labeled holdout set.

1. **Read.** Collect traces from the running agent.
2. **Judge.** Score those traces with an LLM judge.
3. **Propose.** Read failed traces, identify a recurring failure pattern, and write one targeted harness update.
4. **Validate.** Evaluate the new harness on the holdout set and keep it only if holdout accuracy improves.
5. **Repeat.** Continue the loop with the updated harness.

**Filesystem memory.** meta-agent stores each harness candidate, its scores, and its traces on disk. This gives the proposer persistent memory across iterations. Before proposing a change, it can search prior candidates, per-task traces, and scores to see what failed, what has already been tried, and which changes actually improved performance.

## What the optimizer can change

The optimizer can modify the parts of the harness that most directly shape agent behavior:

- the system prompt
- lifecycle hooks for tool use
- stop conditions and error handling
- custom tools, permission logic, and subagents
- other control logic around the model

It makes one targeted change at a time and prefers the smallest effective fix.

## Results

### tau-bench v3 (67% → 87%)

[Tau-bench v3](https://github.com/sierra-research/tau-bench) is a benchmark for conversational customer service agents. We evaluate on the airline domain (50 tasks), where the agent must follow policy and resolve customer requests.

![results_graph](./images/results_graph.png)

**Setup:** We split the 50 airline tasks into 35 for search and 15 for holdout. The agent used Haiku 4.5, the proposer used Opus 4.6, and holdout was always graded with the official tau evaluator.

Starting from a 67% baseline, the judge-based search run reached 87% holdout accuracy. In our current setup, this was higher than the 80% reached by our labeled-search variant.

### ArtifactsBench (40.9% → 68.8%)

[ArtifactsBench](https://arxiv.org/abs/2501.15985) evaluates agents on generating web applications from natural language descriptions. A Gemini VLM judge scores each output on a per-task checklist covering functionality, visual design, and correctness.

**Setup:** We split 438 web application tasks into 300 for search and 138 for holdout. The agent used Codex (gpt-5.3-codex) with the `codex` harness (AGENTS.md). The proposer (Opus 4.6) optimized the AGENTS.md file that Codex reads before each task.

Starting from a 40.9% baseline average reward, the optimizer reached 68.8% on the search split. The proposer discovered that adding structured instructions for HTML generation, responsive design patterns, and file organization to AGENTS.md produced consistent improvements.

_Note: These are single-run results. We plan to expand with multiple runs and variance estimates in future work._

## Harness Evolution

The harness did not improve in one shot. The proposer iterated through multiple attempts, learning from failure traces at each step. The table below shows the progression for our best-performing run (Critique, 87% holdout).

![harness_before_after](./images/harness_before_after.png)

| Epoch | What the proposer tried | Holdout | What happened |
|-------|------------------------|---------|---------------|
| 0 | Vanilla config | 67% | Agent quits after one greeting |
| 1 | Modified the **system prompt** + added a **stop condition** | 53% | Agent stays engaged but makes up answers instead of looking things up |
| 2 | Rewrote the **system prompt** with tool-use rules | 47% | Agent uses tools now but too many instructions confused it |
| 3 | Supplemented the **system prompt** with a **skill** (domain knowledge) | **80%** | Agent already knew how to use tools, it just needed the right policy |
| 4 | Expanded the **skill** with additional policy rules | 73% | New rules were too narrow and didn't generalize to holdout |
| 5 | Fixed the **skill** (proposer caught its own factual errors) | **87%** | Agent was following instructions correctly but the instructions were wrong |

Starting from a vanilla harness, the proposer first added a stop condition to keep the agent from quitting early, but this reduced holdout because the agent stayed engaged while hallucinating answers. It then rewrote the system prompt with tool-use rules, which encouraged tool use but added too much prompt overhead.

The key improvement came when the proposer moved business rules into a skill, raising holdout to 80%. It then tried to expand the skill with more policy rules, but these were too task-specific and holdout dropped to 73%. In the final step, the proposer reviewed what it had, caught factual errors in the skill, and corrected them, reaching 87%.

The full improvement came from just three harness components: a stop condition, a rewritten system prompt, and a skill containing domain rules.

## What we learned

**Judge-based search is viable on unlabeled traces.** We use an LLM judge to score unlabeled search traces during optimization, while keeping a small labeled holdout set to decide whether to keep an update. In our current setup, this reached 87% holdout accuracy, compared with 80% for our labeled-search variant.

**Persistent trace memory helps.** Giving the proposer access to prior harnesses, traces, and scores helped it avoid repeating failed changes and even catch errors in its own earlier policy rules.

**Proposer tends to overfit.** Early iterations often fixed the specific traces the proposer saw rather than writing general behavioral rules, which improved search accuracy while hurting holdout. We reduced this by adding a simple instruction: "State your change as a rule about agent behavior. If you can only justify it by pointing to specific traces, it's too narrow."

**The proposer prompt matters.** Small changes to the proposer's instructions had a large effect on optimization quality.

## Workbench

meta-agent includes a Next.js workbench for browsing optimization results. It reads the experience store and displays the optimization trajectory, per-epoch harness diffs, execution traces, judge feedback, and candidate comparisons.

```bash
cd ui && npm install && npm run dev
```

## Try it

```bash
git clone https://github.com/canvas-org/meta-agent
cd meta-agent
pip install -e .

python -m meta_agent.outer_loop \
    --benchmark benchmarks/tau3/benchmark.yaml \
    --iterations 10 \
    --model claude-haiku-4-5
```

Point it at your agent, a task set, and a labeled holdout split. In our experiments, the optimizer found its best harness within 4-10 iterations. You get a ranked set of harnesses with per-task traces explaining what changed and why.

## What's next

- **Production integration.** A `meta_agent.log()` API for logging traces from production agents, and a `meta-agent propose` command that runs the proposer once against collected traces.
- **Better proposer optimization.** The proposer has strong leverage on final harness quality. We want to explore a meta-loop that improves the proposer's own instructions over time, including better failure abstraction and clustering.
- **More benchmarks.** We plan to evaluate on broader agent benchmarks, including SWE-bench Multimodal, TerminalBench-2, and domain-specific tasks.
