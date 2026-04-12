import fs from "fs";
import path from "path";
import type { DashboardData, Run, TaskResult, BenchmarkData, HarnessFile } from "./types";

const PROJECT_ROOT = path.resolve(process.cwd(), "..");
const EXPERIENCE_ROOT = path.join(PROJECT_ROOT, "experience");

export interface CollectRunsOptions {
  benchmark?: string;
  candidate?: string;
  includeTasks?: boolean;
  includeHarnessFiles?: boolean;
}

function readJson(filePath: string): unknown {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf-8"));
  } catch {
    return null;
  }
}

function readText(filePath: string): string | null {
  try {
    return fs.readFileSync(filePath, "utf-8");
  } catch {
    return null;
  }
}

export function collectRuns(options: CollectRunsOptions = {}): DashboardData {
  const {
    benchmark,
    candidate,
    includeTasks = true,
    includeHarnessFiles = true,
  } = options;
  const benchmarks: DashboardData = {};

  if (!fs.existsSync(EXPERIENCE_ROOT)) return benchmarks;

  for (const benchName of fs.readdirSync(EXPERIENCE_ROOT).sort()) {
    if (benchmark && benchName !== benchmark) continue;
    const benchDir = path.join(EXPERIENCE_ROOT, benchName);
    if (!fs.statSync(benchDir).isDirectory()) continue;

    const candidatesDir = path.join(benchDir, "candidates");
    if (!fs.existsSync(candidatesDir)) continue;

    const runs: Run[] = [];

    for (const candName of fs.readdirSync(candidatesDir).sort()) {
      if (candidate && candName !== candidate) continue;
      const candDir = path.join(candidatesDir, candName);
      if (!fs.statSync(candDir).isDirectory()) continue;

      const scoresPath = path.join(candDir, "scores.json");
      const scores = readJson(scoresPath) as Run["scores"] | null;
      if (!scores) continue;

      let config: string | null = null;
      const harnessFiles: HarnessFile[] = [];

      if (includeHarnessFiles) {
        config =
          readText(path.join(candDir, "AGENTS.md")) ??
          readText(path.join(candDir, "CLAUDE.md")) ??
          readText(path.join(candDir, "config.py"));

        function categorize(relPath: string): HarnessFile["category"] {
          if (relPath === "AGENTS.md" || relPath === "CLAUDE.md") return "system_prompt";
          if (relPath.includes("hooks.json") || relPath.startsWith(".codex/hooks/") || relPath.endsWith(".sh")) return "hooks";
          if (relPath === ".codex/config.toml") return "config";
          if (relPath.startsWith(".codex/skills/")) return "skills";
          if (relPath.startsWith(".codex/agents/")) return "agents";
          if (relPath.startsWith(".claude/rules/")) return "rules";
          return "other";
        }

        const harnessPatterns = ["AGENTS.md", "CLAUDE.md"];
        for (const f of harnessPatterns) {
          const txt = readText(path.join(candDir, f));
          if (txt) harnessFiles.push({ path: f, content: txt, category: categorize(f) });
        }

        for (const subdir of [".codex", ".claude"]) {
          const dirPath = path.join(candDir, subdir);
          if (fs.existsSync(dirPath) && fs.statSync(dirPath).isDirectory()) {
            const walk = (dir: string): void => {
              for (const entry of fs.readdirSync(dir)) {
                const full = path.join(dir, entry);
                const rel = path.relative(candDir, full);
                if (fs.statSync(full).isDirectory()) { walk(full); continue; }
                const txt = readText(full);
                if (txt) harnessFiles.push({ path: rel, content: txt, category: categorize(rel) });
              }
            };
            walk(dirPath);
          }
        }

        for (const f of fs.readdirSync(candDir)) {
          if (f.endsWith(".sh")) {
            const txt = readText(path.join(candDir, f));
            if (txt) harnessFiles.push({ path: f, content: txt, category: "hooks" });
          }
        }
      }

      const tasks: TaskResult[] = [];
      const perTaskDir = path.join(candDir, "per_task");

      if (includeTasks && fs.existsSync(perTaskDir) && fs.statSync(perTaskDir).isDirectory()) {
        for (const fname of fs.readdirSync(perTaskDir).sort()) {
          if (!fname.endsWith(".json") || fname.endsWith("_agent_result.json")) continue;

          const task = readJson(path.join(perTaskDir, fname)) as TaskResult | null;
          if (!task) continue;

          const stem = fname.replace(/\.json$/, "");
          const fbPath = path.join(perTaskDir, `${stem}_judge_feedback.md`);
          const fbText = readText(fbPath);
          if (fbText) {
            try {
              task.judge = JSON.parse(fbText);
            } catch {
              task.judge_raw = fbText.slice(0, 2000);
            }
          }

          tasks.push(task);
        }
      }

      runs.push({ name: candName, scores, config, harnessFiles, tasks });
    }

    const historyData = readJson(path.join(benchDir, "history.json")) as BenchmarkData["history"];
    benchmarks[benchName] = { runs, history: historyData };
  }

  return benchmarks;
}
