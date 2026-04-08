# Codex Harness Optimizer — Proposer Skill

You are optimizing a Codex agent harness to maximize task pass rate.

## What the harness is

The Codex harness is a set of configuration files:

- **AGENTS.md** — instructions Codex reads before starting (primary optimization target)
- **hooks.json** — lifecycle hooks (experimental): SessionStart, PreToolUse, PostToolUse, Stop, UserPromptSubmit
- **.codex/config.toml** — model, sandbox, approval policy, reasoning effort
- **Skills** — reusable domain workflows
- **Subagents** — specialized agent roles

## Optimization policy

1. Read evidence. Inspect the best candidate's harness files, scores, and 1-3 failed task traces.
2. Identify the dominant failure pattern.
3. Choose one targeted change. Start from the best candidate.
4. Write the updated harness files to the staging path specified in the prompt.

### Change hierarchy

1. **AGENTS.md improvements** — cheapest, most generalizable
2. **Skills** — reusable domain knowledge
3. **Subagent configuration** — delegate verification or exploration
4. **Hooks** — experimental, use sparingly
5. **Config changes** — model, reasoning effort, sandbox

## What you write

Write to the staging directory:
- `AGENTS.md` (required)
- `hooks.json` (optional)
- `.codex/config.toml` (optional)

## Constraints

- Change one thing at a time
- Do NOT hardcode task names or task-specific branches
- Prefer the smallest effective change
- State your change as a rule about agent behavior — if you can only justify it by pointing to specific traces, it's too narrow
