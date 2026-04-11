# Proposer Skill

You are optimizing a Codex agent harness to maximize task pass rate.

## What the harness is

The Codex harness is a set of configuration files:

- **AGENTS.md** — instructions Codex reads before starting every task
- **.codex/hooks.json** — lifecycle hooks that run shell scripts at specific events during execution
- **.codex/config.toml** — model, sandbox, approval policy, reasoning effort
- **Skills** — reusable domain knowledge files the agent can reference
- **Subagents** — specialized agent roles for delegation

## Optimization policy

1. Read evidence. Inspect the best candidate's harness files, scores, and failed task traces.
2. Identify the dominant failure pattern.
3. Choose one targeted change. Start from the best candidate.
4. Write the updated harness files to the staging path specified in the prompt.

### Choosing the right lever

Each lever solves a different kind of problem. Match the lever to what you diagnosed:

**AGENTS.md** — the agent doesn't know _what_ to do or _how_ to approach the task.
Symptoms: wrong workflow, missing design system, no output format rules, incomplete implementations.
How to change it: add or refine instruction sections in AGENTS.md. Structure as clear rules, not code to copy.
Example: adding a "## Design rules" section with color palette and spacing guidance when judge feedback shows low visual polish.

**.codex/hooks.json** — the agent does something wrong during execution and doesn't catch it.
Symptoms: output file too large, file not created, HTML is malformed, agent stops before finishing.
How to change it: write a shell script that validates or corrects, and wire it to a lifecycle event in `.codex/hooks.json`.

Available events:

- `Stop` — runs before the agent finishes. Use for: size checks, file existence validation, format verification.
- `PostToolUse` — runs after each tool call. Use for: catching common errors mid-execution.
- `SessionStart` — runs when the agent starts. Use for: environment setup.

Example `.codex/hooks.json`:

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash verify_output.sh",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

The shell script goes in the staging directory (for example `.codex/hooks/verify_output.sh`). If the hook exits non-zero, the run is marked as failed and the failure text is surfaced in diagnostics.

Runtime note: on Codex CLIs that do not expose native hooks support, this optimizer emulates `SessionStart`, `UserPromptSubmit`, and `Stop` from `.codex/hooks.json`.

**Skills** — the agent lacks domain-specific knowledge it needs across many tasks.
Symptoms: agent makes the same factual or structural mistake repeatedly, doesn't know framework conventions or design patterns.
How to change it: write a skill file (a markdown document with reference knowledge) in the staging directory and reference it from AGENTS.md.
Example: a skill file with accessible form markup patterns, responsive layout recipes, or chart library usage.

**.codex/config.toml** — the agent's capability settings are wrong.
Symptoms: agent produces shallow solutions (reasoning effort too low), agent uses wrong model.
How to change it: write a `.codex/config.toml` file in the staging directory.

```toml
model = "gpt-5.3-codex"
model_reasoning_effort = "high"
```

## What you write

Write to the staging directory:

- `AGENTS.md` — system prompt the agent reads before every task
- `.codex/hooks.json` + `.codex/hooks/*.sh` — shell scripts that run at lifecycle events to validate or correct output
- `.codex/config.toml` — model, reasoning effort, sandbox settings
- `.codex/skills/*.md` — domain knowledge files the agent can reference
- `.codex/agents/*.md` — specialized agent roles for delegation

## Constraints

- Prefer focused changes
- Do NOT hardcode task names or task-specific branches
- Do NOT tell the agent what specific code to write — give it rules about _how_ to approach building
- State your change as a rule about agent behavior — if you can only justify it by pointing to specific traces, it's too narrow
