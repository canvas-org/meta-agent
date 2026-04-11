# ArtifactsBench — Proposer Context

## Framework: Codex CLI

The agent runs via `codex exec --full-auto`. You control its behavior through files in the workspace root.

### Available levers

**AGENTS.md (required)** — Instructions Codex reads before starting. Write rules about _how_ to build, not code to copy.

**Skills** — Markdown files in `.codex/skills/` that Codex can reference. Use for reusable domain knowledge (e.g., design system patterns, accessibility rules, testing checklists).

**Subagents** — Agent role definitions in `.codex/agents/`. Use to delegate review or verification to a separate agent pass.

**.codex/hooks.json** — Lifecycle hooks that run shell scripts at events: SessionStart, PreToolUse, PostToolUse, UserPromptSubmit, Stop.

**.codex/config.toml** — Model settings, reasoning effort, sandbox configuration.

### Output format

Write the following files to the staging directory:

- `AGENTS.md` (required)
- `.codex/config.toml` (optional)
- `.codex/hooks.json` (optional)
- `.codex/skills/*.md` (optional)
- `.codex/agents/*.md` (optional)

### Framework-specific constraints

- The agent must produce a single self-contained HTML file — do not instruct it to use npm, React, or build tools
- Do not tell the agent what specific code to write — give it rules about _how_ to approach building
- If repeated AGENTS.md-only changes plateau, consider hooks for deterministic validation and correction steps

## How scoring works

Each task has a unique 10-item checklist. Roughly half the items are vision-oriented (design quality, visual polish, interaction smoothness, aesthetics, user experience) and half are code-oriented (core functionality, robustness, engineering quality, innovation, redundancy). Each item is scored 0-10 by a VLM judge. The overall score is the weighted average.

A score of 60 means the artifact mostly works but has significant gaps. 80 means solid with minor issues. 90+ means polished and complete.

When diagnosing, focus on which dimensions are consistently low across multiple tasks — that reveals a systemic harness issue, not a task-specific one.

## Judge details

The VLM judge (Gemini) receives:
- The task prompt and checklist
- The agent's code output (truncated to ~30K characters)
- 3 screenshots of the rendered artifact

If the code output exceeds the judge's context window, the judge cannot see the full artifact. This means oversized outputs may score poorly even if functionally correct.
