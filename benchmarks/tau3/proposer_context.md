# Tau-bench — Proposer Context

## Framework: Claude Agent SDK

The agent runs via the Claude Agent SDK (Python). You control its behavior through a Python config module.

### Available levers

**System prompt** — Appended instructions that shape the agent's behavior. Cheapest lever.

**Hooks** — Python async functions that fire at lifecycle events (PreToolUse, PostToolUse, Stop, UserPromptSubmit). Can inject context, block tool calls, modify inputs, reject stop attempts. Hooks are stateful — they can track agent behavior across turns.

**Custom MCP tools** — Give the agent capabilities it doesn't have natively (workspace validation, structured queries, etc.).

**Permission callbacks** — Transparently rewrite tool inputs without the agent knowing (e.g., sanitize commands, inject flags).

**Subagents** — Delegate to a separate agent (e.g., a cheaper model for verification). Roughly doubles cost per task.

**Config options** — max_turns, max_budget_usd, thinking mode, effort level, allowed/disallowed tools, sandbox.

### Output format

Write a Python config module to the staging path:

```python
from claude_agent_sdk import ClaudeAgentOptions, HookMatcher
from meta_agent.run_context import RunContext

def build_options(ctx: RunContext) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        system_prompt={"type": "preset", "preset": "claude_code", "append": "..."},
        cwd=ctx.cwd,
        model=ctx.model,
        permission_mode="bypassPermissions",
        max_turns=200,
        hooks={...},
    )
```

`RunContext` provides: `cwd`, `model`, `task_instruction`.

### SDK reference

**Hook signature:**
```python
async def my_hook(input_data: dict, tool_use_id: str | None, context) -> dict:
    return {}  # no-op
```

**Hook events:** PreToolUse, PostToolUse, Stop, UserPromptSubmit, PostToolUseFailure

**Hook actions:**
- Inject context: `{"hookSpecificOutput": {"additionalContext": "..."}}`
- Block tool call: `{"hookSpecificOutput": {"permissionDecision": "deny", "permissionDecisionReason": "..."}}`
- Modify input: `{"hookSpecificOutput": {"updatedInput": {...}}}`
- Reject stop: `{"reason": "...", "continue_": True}`

**Stop hook lifecycle:** First attempt → `stop_hook_active = False`, return `continue_: True` to reject. Second attempt → `stop_hook_active = True`, return `{}` to allow.

**Custom tools:**
```python
from claude_agent_sdk import tool, create_sdk_mcp_server

@tool("check_workspace", "Check workspace", {"path": str})
async def check_workspace(args):
    return {"content": [{"type": "text", "text": "..."}]}

server = create_sdk_mcp_server(name="harness", tools=[check_workspace])
```

**Subagents:**
```python
from claude_agent_sdk import AgentDefinition

agents={"verifier": AgentDefinition(
    description="Verify the solution",
    prompt="Check output matches requirements.",
    tools=["Read", "Bash"], model="haiku",
)}
```

### Framework-specific constraints

- Do not read or modify test/verification scripts
- Do not add a custom tool when a prompt or hook would suffice
- Do not add a subagent unless the main agent lacks a structurally different capability
- Cost: hooks are free, prompt appends are cheap, tools are cheap per call, subagents roughly double cost

## How scoring works

Tasks are binary pass/fail. The official tau evaluator checks whether the agent followed the correct policy and resolved the customer request. The optimization target is pass_rate (fraction of tasks passed).

### What to look for in traces

Each `_trace.jsonl` line is a JSON object:
- `AssistantMessage` → `ThinkingBlock` (reasoning), `ToolUseBlock` (tool calls)
- `UserMessage` → `ToolResultBlock` (output, `is_error`)
- `ResultMessage` (cost, turns, duration)

In failed traces, look for: repeated failing commands, early stopping without verification, ignored errors, broad edits without narrowing, wasted turns before first useful action.
