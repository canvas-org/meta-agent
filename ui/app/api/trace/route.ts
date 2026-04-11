import { NextRequest, NextResponse } from "next/server";
import fs from "fs";
import path from "path";

export const dynamic = "force-dynamic";

const PROJECT_ROOT = path.resolve(process.cwd(), "..");
const EXPERIENCE_ROOT = path.join(PROJECT_ROOT, "experience");

interface TraceEvent {
  type: "message" | "tool_call" | "tool_result" | "error" | "meta" | "text";
  content: string;
  tool?: string;
  timestamp?: string;
}

function parseTrace(content: string): { events: TraceEvent[]; format: "jsonl" | "text" } {
  const lines = content.trim().split("\n");
  const events: TraceEvent[] = [];
  let hasJson = false;

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) continue;

    try {
      const obj = JSON.parse(trimmed);
      hasJson = true;

      // Codex --json format: item.completed with item.type
      if (obj.type === "item.completed" && obj.item) {
        const item = obj.item;
        if (item.type === "agent_message") {
          events.push({ type: "message", content: item.text || "" });
        } else if (item.type === "command_execution") {
          events.push({
            type: "tool_call",
            content: item.text || item.output || "(executed)",
            tool: item.tool_name || "shell",
          });
        } else if (item.type === "file_edit" || item.type === "file_create") {
          events.push({
            type: "tool_call",
            content: item.text || `${item.type}: ${item.file_path || ""}`,
            tool: item.type,
          });
        } else {
          events.push({ type: "meta", content: item.text || JSON.stringify(item).slice(0, 200) });
        }
        continue;
      }

      // Codex --json: thread/turn lifecycle events
      if (obj.type === "thread.started" || obj.type === "turn.started" || obj.type === "turn.completed") {
        events.push({ type: "meta", content: obj.type });
        continue;
      }

      // Claude stream-json format: assistant messages with content blocks
      if (obj.type === "assistant" && obj.message?.content) {
        for (const block of obj.message.content) {
          if (block.type === "text") {
            events.push({ type: "message", content: block.text || "" });
          } else if (block.type === "tool_use") {
            events.push({
              type: "tool_call",
              content: JSON.stringify(block.input || {}, null, 2).slice(0, 500),
              tool: block.name || "unknown",
            });
          }
        }
        continue;
      }

      // Claude stream-json: tool results
      if (obj.type === "user" && obj.message?.content) {
        for (const block of obj.message.content) {
          if (block.type === "tool_result") {
            const text = typeof block.content === "string" ? block.content : JSON.stringify(block.content || "").slice(0, 500);
            events.push({
              type: "tool_result",
              content: text,
              tool: block.tool_use_id || "",
            });
          }
        }
        continue;
      }

      // Claude stream-json: result
      if (obj.type === "result") {
        events.push({
          type: "meta",
          content: `Done — ${obj.num_turns || "?"} turns, $${(obj.cost_usd || 0).toFixed(3)}`,
        });
        continue;
      }

      // Claude SDK adapter format: {tool: "name"} or {result: true}
      if (obj.tool) {
        events.push({ type: "tool_call", content: "", tool: obj.tool });
        continue;
      }
      if (obj.result !== undefined) {
        events.push({ type: "meta", content: `turns=${obj.turns || "?"} cost=$${(obj.cost || 0).toFixed(3)}` });
        continue;
      }
      if (obj.error) {
        events.push({ type: "error", content: String(obj.error) });
        continue;
      }

      events.push({ type: "meta", content: JSON.stringify(obj).slice(0, 200) });
    } catch {
      // Not JSON -- accumulate as plain text
      if (!hasJson) {
        events.push({ type: "text", content: trimmed });
      }
    }
  }

  if (!hasJson && events.length === 0) {
    events.push({ type: "text", content: content.slice(0, 50000) });
  }

  return { events, format: hasJson ? "jsonl" : "text" };
}

export function GET(req: NextRequest): NextResponse {
  const benchmark = req.nextUrl.searchParams.get("benchmark");
  const candidate = req.nextUrl.searchParams.get("candidate");
  const task = req.nextUrl.searchParams.get("task");

  if (!benchmark || !candidate || !task) {
    return NextResponse.json({ error: "Missing benchmark, candidate, or task" }, { status: 400 });
  }

  const tracePath = path.join(
    EXPERIENCE_ROOT, benchmark, "candidates", candidate, "per_task", `${task}_trace.jsonl`
  );

  if (!fs.existsSync(tracePath)) {
    return NextResponse.json({ events: [], format: "none", size: 0 });
  }

  const content = fs.readFileSync(tracePath, "utf-8");
  if (!content.trim()) {
    return NextResponse.json({ events: [], format: "empty", size: 0 });
  }

  const { events, format } = parseTrace(content);

  return NextResponse.json({
    events,
    format,
    size: content.length,
  });
}
