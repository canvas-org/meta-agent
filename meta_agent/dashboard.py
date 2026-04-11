#!/usr/bin/env python3
"""Generate a self-contained HTML dashboard from the experience store.

Usage:
    python -m meta_agent.dashboard
    python -m meta_agent.dashboard --open
"""
from __future__ import annotations

import argparse
import json
import webbrowser
from pathlib import Path
from typing import Any

from meta_agent.paths import get_experience_root, get_workspace_root


def collect_runs(experience_root: Path) -> dict[str, Any]:
    benchmarks: dict[str, Any] = {}

    for bench_dir in sorted(experience_root.iterdir()):
        if not bench_dir.is_dir():
            continue
        candidates_dir = bench_dir / "candidates"
        if not candidates_dir.exists():
            continue

        bench_name = bench_dir.name
        runs: list[dict[str, Any]] = []

        for cand_dir in sorted(candidates_dir.iterdir()):
            if not cand_dir.is_dir():
                continue

            scores_path = cand_dir / "scores.json"
            if not scores_path.exists():
                continue

            try:
                scores = json.loads(scores_path.read_text())
            except json.JSONDecodeError:
                continue

            run: dict[str, Any] = {
                "name": cand_dir.name,
                "scores": scores,
                "config": None,
                "tasks": [],
            }

            claude_md = cand_dir / "CLAUDE.md"
            agents_md = cand_dir / "AGENTS.md"
            config_py = cand_dir / "config.py"
            if claude_md.exists():
                run["config"] = claude_md.read_text()
            elif agents_md.exists():
                run["config"] = agents_md.read_text()
            elif config_py.exists():
                run["config"] = config_py.read_text()

            per_task_dir = cand_dir / "per_task"
            if per_task_dir.exists():
                for f in sorted(per_task_dir.glob("*.json")):
                    if f.name.endswith("_agent_result.json"):
                        continue
                    try:
                        task = json.loads(f.read_text())
                    except json.JSONDecodeError:
                        continue

                    fb_path = per_task_dir / f"{f.stem}_judge_feedback.md"
                    if fb_path.exists():
                        fb_text = fb_path.read_text().strip()
                        try:
                            task["judge"] = json.loads(fb_text)
                        except json.JSONDecodeError:
                            task["judge_raw"] = fb_text[:2000]

                    run["tasks"].append(task)

            runs.append(run)

        history = None
        history_path = bench_dir / "history.json"
        if history_path.exists():
            try:
                history = json.loads(history_path.read_text())
            except json.JSONDecodeError:
                pass

        benchmarks[bench_name] = {"runs": runs, "history": history}

    return benchmarks


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>meta-agent dashboard</title>
<style>
:root {
  --bg: #0f1117;
  --surface: #1a1d27;
  --surface2: #232734;
  --border: #2e3345;
  --text: #e2e8f0;
  --text2: #94a3b8;
  --accent: #6366f1;
  --accent2: #818cf8;
  --green: #22c55e;
  --red: #ef4444;
  --amber: #f59e0b;
  --radius: 8px;
  --font: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  --mono: "SF Mono", "Fira Code", "JetBrains Mono", monospace;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: var(--font); background: var(--bg); color: var(--text); height: 100vh; display: flex; flex-direction: column; }
a { color: var(--accent2); text-decoration: none; }

.shell { display: flex; flex: 1; overflow: hidden; }
.sidebar { width: 260px; min-width: 260px; background: var(--surface); border-right: 1px solid var(--border); overflow-y: auto; display: flex; flex-direction: column; }
.main { flex: 1; overflow-y: auto; padding: 28px 36px; }
.topbar { background: var(--surface); border-bottom: 1px solid var(--border); padding: 12px 24px; display: flex; align-items: center; gap: 12px; }
.topbar h1 { font-size: 15px; font-weight: 600; letter-spacing: -0.3px; }
.topbar .sep { color: var(--text2); }
.topbar .bench-name { color: var(--text2); font-size: 14px; }

.sidebar-section { padding: 16px 16px 8px; }
.sidebar-section label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text2); font-weight: 600; }
.sidebar-section select { width: 100%; margin-top: 8px; padding: 7px 10px; background: var(--surface2); color: var(--text); border: 1px solid var(--border); border-radius: var(--radius); font-size: 13px; font-family: var(--font); }
.run-list { list-style: none; flex: 1; overflow-y: auto; padding: 4px 8px; }
.run-item { padding: 10px 12px; border-radius: var(--radius); cursor: pointer; font-size: 13px; display: flex; justify-content: space-between; align-items: center; margin-bottom: 2px; transition: background 0.1s; gap: 6px; }
.run-item:hover { background: var(--surface2); }
.run-item.active { background: var(--accent); color: #fff; }
.run-item .run-name { font-family: var(--mono); font-weight: 500; }
.run-meta { display: flex; align-items: center; gap: 6px; }
.run-item .run-score { font-family: var(--mono); font-size: 12px; opacity: 0.7; }
.run-item.active .run-score { opacity: 1; }

/* Mode tabs */
.mode-tabs { display: flex; gap: 2px; padding: 12px 16px 4px; }
.mode-tab { flex: 1; padding: 6px 0; text-align: center; font-size: 12px; font-weight: 600; border-radius: 6px; cursor: pointer; background: transparent; border: 1px solid var(--border); color: var(--text2); transition: all 0.15s; font-family: var(--font); }
.mode-tab:hover { color: var(--text); border-color: var(--text2); }
.mode-tab.active { background: var(--accent); color: #fff; border-color: var(--accent); }

/* Generalization badge */
.gen-pill { font-size: 9px; font-weight: 700; padding: 1px 5px; border-radius: 3px; letter-spacing: 0.3px; white-space: nowrap; }
.gen-pill.ok { background: rgba(34,197,94,0.15); color: var(--green); }
.gen-pill.warn { background: rgba(245,158,11,0.15); color: var(--amber); }
.gen-pill.bad { background: rgba(239,68,68,0.15); color: var(--red); }

.run-header { margin-bottom: 28px; }
.run-header h2 { font-size: 22px; font-weight: 600; letter-spacing: -0.5px; margin-bottom: 16px; font-family: var(--mono); }
.stats-row { display: flex; gap: 16px; flex-wrap: wrap; }
.stat-card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px 20px; min-width: 140px; }
.stat-card .stat-label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text2); margin-bottom: 4px; }
.stat-card .stat-value { font-size: 22px; font-weight: 700; font-family: var(--mono); letter-spacing: -0.5px; }
.stat-card .stat-value.pass { color: var(--green); }
.stat-card .stat-sub { font-size: 11px; color: var(--text2); margin-top: 4px; }

.config-toggle { margin-top: 20px; display: inline-flex; align-items: center; gap: 6px; font-size: 13px; color: var(--accent2); cursor: pointer; border: none; background: none; font-family: var(--font); }
.config-toggle:hover { text-decoration: underline; }
.config-panel { margin-top: 12px; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px; display: none; }
.config-panel.open { display: block; }
.config-panel pre { font-size: 12px; font-family: var(--mono); white-space: pre-wrap; word-break: break-word; color: var(--text2); line-height: 1.6; max-height: 400px; overflow-y: auto; }

.tasks-section { margin-top: 32px; }
.tasks-section h3 { font-size: 15px; font-weight: 600; margin-bottom: 12px; display: flex; align-items: center; gap: 8px; }
.tasks-section h3 .count { font-size: 12px; color: var(--text2); font-weight: 400; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
thead th { text-align: left; padding: 10px 12px; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text2); border-bottom: 1px solid var(--border); font-weight: 600; cursor: pointer; user-select: none; white-space: nowrap; }
thead th:hover { color: var(--text); }
thead th.sorted-asc::after { content: " \25B2"; font-size: 9px; }
thead th.sorted-desc::after { content: " \25BC"; font-size: 9px; }
tbody tr { border-bottom: 1px solid var(--border); cursor: pointer; transition: background 0.1s; }
tbody tr:hover { background: var(--surface2); }
tbody tr.expanded { background: var(--surface); }
td { padding: 10px 12px; font-family: var(--mono); white-space: nowrap; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }
.badge.pass { background: rgba(34,197,94,0.15); color: var(--green); }
.badge.fail { background: rgba(239,68,68,0.15); color: var(--red); }
.score-bar { display: inline-flex; align-items: center; gap: 6px; }
.score-bar .bar { width: 60px; height: 6px; background: var(--surface2); border-radius: 3px; overflow: hidden; }
.score-bar .bar .fill { height: 100%; border-radius: 3px; transition: width 0.3s; }

.detail-row td { padding: 0; }
.detail-row .detail-inner { padding: 16px 20px 20px; background: var(--surface); border-top: 1px solid var(--border); }
.detail-row .detail-inner h4 { font-size: 13px; font-weight: 600; margin-bottom: 12px; }
.dim-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.dim-item { display: flex; align-items: flex-start; gap: 10px; padding: 8px 10px; background: var(--surface2); border-radius: 6px; }
.dim-score { min-width: 28px; text-align: center; font-family: var(--mono); font-weight: 700; font-size: 14px; padding-top: 1px; }
.dim-body { flex: 1; min-width: 0; }
.dim-title { font-size: 12px; font-weight: 600; margin-bottom: 2px; line-height: 1.3; }
.dim-review { font-size: 11px; color: var(--text2); line-height: 1.4; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.dim-review.expanded { -webkit-line-clamp: unset; }
.dim-review-toggle { font-size: 11px; color: var(--accent2); cursor: pointer; margin-top: 2px; border: none; background: none; padding: 0; font-family: var(--font); }

.trajectory { margin-bottom: 28px; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 20px; }
.trajectory h3 { font-size: 14px; font-weight: 600; margin-bottom: 16px; }
.trajectory canvas { width: 100% !important; height: 200px !important; }

/* Epoch timeline */
.timeline { margin-bottom: 28px; }
.timeline h3 { font-size: 14px; font-weight: 600; margin-bottom: 12px; }
.tl-row { display: flex; align-items: stretch; gap: 0; cursor: pointer; transition: background 0.1s; border-radius: var(--radius); margin-bottom: 2px; }
.tl-row:hover { background: var(--surface); }
.tl-row.tl-active { background: var(--surface); }
.tl-gutter { width: 32px; display: flex; flex-direction: column; align-items: center; padding-top: 14px; }
.tl-dot { width: 10px; height: 10px; border-radius: 50%; background: var(--border); flex-shrink: 0; }
.tl-dot.best { background: var(--green); }
.tl-line { width: 2px; flex: 1; background: var(--border); margin-top: 4px; }
.tl-row:last-child .tl-line { display: none; }
.tl-body { flex: 1; padding: 10px 12px; display: flex; align-items: center; gap: 16px; font-size: 13px; }
.tl-name { font-family: var(--mono); font-weight: 600; min-width: 70px; }
.tl-scores { display: flex; gap: 12px; font-family: var(--mono); font-size: 12px; }
.tl-s { color: var(--accent2); }
.tl-h { color: var(--green); }
.tl-delta { font-size: 11px; font-weight: 600; padding: 1px 6px; border-radius: 3px; }
.tl-delta.up { background: rgba(34,197,94,0.15); color: var(--green); }
.tl-delta.down { background: rgba(239,68,68,0.15); color: var(--red); }
.tl-delta.flat { background: var(--surface2); color: var(--text2); }
.tl-diff-stat { font-size: 11px; color: var(--text2); margin-left: auto; }

/* Compare view */
.compare-header { display: flex; gap: 16px; align-items: flex-end; margin-bottom: 24px; flex-wrap: wrap; }
.compare-header .pick { display: flex; flex-direction: column; gap: 4px; }
.compare-header .pick label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text2); font-weight: 600; }
.compare-header .pick select { padding: 7px 10px; background: var(--surface2); color: var(--text); border: 1px solid var(--border); border-radius: var(--radius); font-size: 13px; font-family: var(--mono); min-width: 160px; }
.compare-scores { display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 24px; }
.cmp-card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px 20px; min-width: 160px; }
.cmp-card .cmp-label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text2); margin-bottom: 6px; }
.cmp-card .cmp-vals { display: flex; align-items: baseline; gap: 8px; font-family: var(--mono); }
.cmp-card .cmp-vals .v { font-size: 20px; font-weight: 700; }
.cmp-card .cmp-vals .arrow { color: var(--text2); }
.cmp-card .cmp-vals .delta { font-size: 13px; font-weight: 600; }

/* Diff */
.diff-section { margin-bottom: 28px; }
.diff-section h3 { font-size: 14px; font-weight: 600; margin-bottom: 12px; }
.diff-box { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; max-height: 500px; overflow-y: auto; }
.diff-line { padding: 1px 12px; font-family: var(--mono); font-size: 12px; line-height: 1.7; white-space: pre-wrap; word-break: break-word; }
.diff-line.add { background: rgba(34,197,94,0.08); color: var(--green); }
.diff-line.del { background: rgba(239,68,68,0.08); color: var(--red); }
.diff-line.ctx { color: var(--text2); }

/* Flip table */
.flip-section { margin-bottom: 28px; }
.flip-section h3 { font-size: 14px; font-weight: 600; margin-bottom: 12px; display: flex; align-items: center; gap: 8px; }
.flip-section h3 .count { font-size: 12px; color: var(--text2); font-weight: 400; }
.flip-group { margin-bottom: 16px; }
.flip-group-title { font-size: 12px; font-weight: 600; margin-bottom: 6px; display: flex; align-items: center; gap: 6px; }
.flip-tags { display: flex; flex-wrap: wrap; gap: 4px; }
.flip-tag { font-size: 11px; font-family: var(--mono); padding: 2px 8px; border-radius: 4px; background: var(--surface2); }
.flip-tag.gained { background: rgba(34,197,94,0.1); color: var(--green); }
.flip-tag.lost { background: rgba(239,68,68,0.1); color: var(--red); }

.empty { text-align: center; padding: 80px 20px; color: var(--text2); }
.empty p { font-size: 15px; margin-bottom: 8px; }

::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: var(--text2); }

@media (max-width: 800px) {
  .sidebar { width: 200px; min-width: 200px; }
  .main { padding: 20px; }
  .dim-grid { grid-template-columns: 1fr; }
}
</style>
</head>
<body>

<div class="topbar">
  <h1>meta-agent</h1>
  <span class="sep">/</span>
  <span class="bench-name" id="topbar-bench">dashboard</span>
</div>

<div class="shell">
  <div class="sidebar">
    <div class="sidebar-section">
      <label>Benchmark</label>
      <select id="bench-select"></select>
    </div>
    <div class="mode-tabs">
      <button class="mode-tab active" id="tab-view" onclick="setMode('view')">View</button>
      <button class="mode-tab" id="tab-compare" onclick="setMode('compare')">Compare</button>
    </div>
    <ul class="run-list" id="run-list"></ul>
  </div>
  <div class="main" id="main">
    <div class="empty"><p>Select a run from the sidebar</p></div>
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<script>
const DATA = __DATA_PLACEHOLDER__;

const benchSelect = document.getElementById("bench-select");
const runList = document.getElementById("run-list");
const main = document.getElementById("main");
const topbarBench = document.getElementById("topbar-bench");

let currentBench = null;
let currentRun = null;
let mode = "view"; // "view" | "compare"
let compareA = null;
let compareB = null;
let sortCol = "task_name";
let sortDir = 1;
let expandedTask = null;
let chartInstance = null;

const benchNames = Object.keys(DATA);
benchNames.forEach(name => {
  const opt = document.createElement("option");
  opt.value = name;
  opt.textContent = name;
  benchSelect.appendChild(opt);
});

benchSelect.addEventListener("change", () => {
  currentBench = benchSelect.value;
  topbarBench.textContent = currentBench;
  currentRun = null;
  compareA = null;
  compareB = null;
  expandedTask = null;
  renderSidebar();
  main.innerHTML = '<div class="empty"><p>Select a run from the sidebar</p></div>';
});

window.setMode = function(m) {
  mode = m;
  document.getElementById("tab-view").className = "mode-tab" + (m === "view" ? " active" : "");
  document.getElementById("tab-compare").className = "mode-tab" + (m === "compare" ? " active" : "");
  if (m === "compare") {
    const bench = DATA[currentBench];
    if (bench && bench.runs.length >= 2) {
      compareA = compareA || bench.runs[0].name;
      compareB = compareB || bench.runs[bench.runs.length - 1].name;
    }
    renderSidebar();
    renderCompare();
  } else {
    renderSidebar();
    if (currentRun) renderMain();
    else main.innerHTML = '<div class="empty"><p>Select a run from the sidebar</p></div>';
  }
};

// --- Generalization helpers ---
function getHistory(bench) {
  return (bench.history && bench.history.iterations) || [];
}

function getHistoryEntry(bench, runName) {
  return getHistory(bench).find(it => it.name === runName);
}

function genBadge(bench, runName) {
  const h = getHistoryEntry(bench, runName);
  if (!h || h.holdout_reward == null || h.reward == null) return "";
  const delta = h.reward - h.holdout_reward;
  if (delta > 0.15) return `<span class="gen-pill bad">OVERFIT</span>`;
  if (delta > 0.08) return `<span class="gen-pill warn">RISK</span>`;
  return `<span class="gen-pill ok">GEN</span>`;
}

// --- Sidebar ---
function renderSidebar() {
  const bench = DATA[currentBench];
  if (!bench) { runList.innerHTML = ""; return; }
  runList.innerHTML = "";
  bench.runs.forEach(run => {
    const li = document.createElement("li");
    const isActive = mode === "view" ? currentRun === run.name : (compareA === run.name || compareB === run.name);
    li.className = "run-item" + (isActive ? " active" : "");
    const reward = run.scores.mean_reward;
    const scoreStr = reward != null ? (reward * 100).toFixed(0) + "%" : (run.scores.pass_rate * 100).toFixed(0) + "%";
    const badge = genBadge(bench, run.name);
    li.innerHTML = `<span class="run-name">${esc(run.name)}</span><span class="run-meta">${badge}<span class="run-score">${scoreStr}</span></span>`;
    li.onclick = () => {
      if (mode === "view") selectRun(run.name);
      else selectCompare(run.name);
    };
    runList.appendChild(li);
  });
}

function selectRun(name) {
  currentRun = name;
  expandedTask = null;
  sortCol = "task_name";
  sortDir = 1;
  renderSidebar();
  renderMain();
}

function selectCompare(name) {
  if (compareA === name) { compareA = compareB; compareB = null; }
  else if (compareB === name) { compareB = null; }
  else if (!compareA) compareA = name;
  else compareB = name;
  renderSidebar();
  if (compareA && compareB) renderCompare();
}

// --- Run view ---
function renderMain() {
  const bench = DATA[currentBench];
  const run = bench.runs.find(r => r.name === currentRun);
  if (!run) return;

  const s = run.scores;
  const reward = s.mean_reward != null ? (s.mean_reward * 100).toFixed(1) + "%" : "N/A";
  const passRate = (s.pass_rate * 100).toFixed(0) + "%";
  const cost = s.total_cost_usd != null ? "$" + s.total_cost_usd.toFixed(4) : "N/A";
  const turns = s.median_turns ?? "N/A";
  const model = s.model || "\u2014";

  const h = getHistoryEntry(bench, run.name);
  let genCard = "";
  if (h && h.holdout_reward != null) {
    const ho = (h.holdout_reward * 100).toFixed(1) + "%";
    const delta = h.reward - h.holdout_reward;
    const dStr = (delta >= 0 ? "+" : "") + (delta * 100).toFixed(1) + "pp";
    const cls = delta > 0.15 ? "color:var(--red)" : delta > 0.08 ? "color:var(--amber)" : "color:var(--green)";
    genCard = `<div class="stat-card"><div class="stat-label">Holdout</div><div class="stat-value" style="font-size:20px">${ho}</div><div class="stat-sub" style="${cls}">\u0394 ${dStr} vs search</div></div>`;
  }

  let html = `<div class="run-header">
    <h2>${esc(run.name)}</h2>
    <div class="stats-row">
      <div class="stat-card"><div class="stat-label">Mean Reward</div><div class="stat-value">${reward}</div></div>
      <div class="stat-card"><div class="stat-label">Pass Rate</div><div class="stat-value pass">${s.n_passed}/${s.n_tasks} (${passRate})</div></div>
      ${genCard}
      <div class="stat-card"><div class="stat-label">Cost</div><div class="stat-value">${cost}</div></div>
      <div class="stat-card"><div class="stat-label">Turns</div><div class="stat-value">${turns}</div></div>
      <div class="stat-card"><div class="stat-label">Model</div><div class="stat-value" style="font-size:14px">${esc(model)}</div></div>
    </div>`;

  if (run.config) {
    html += `<button class="config-toggle" onclick="this.nextElementSibling.classList.toggle('open')">\u25B6 View harness config</button>
    <div class="config-panel"><pre>${esc(run.config)}</pre></div>`;
  }
  html += `</div>`;

  // Trajectory chart
  const iters = getHistory(bench);
  if (iters.length > 1) {
    html += `<div class="trajectory"><h3>Optimization Trajectory</h3><canvas id="trajectory-chart"></canvas></div>`;
  }

  // Epoch timeline
  if (iters.length > 1) {
    html += renderTimeline(bench, run.name);
  }

  // Task table
  const tasks = sortTasks(run.tasks);
  html += `<div class="tasks-section">
    <h3>Traces <span class="count">${tasks.length} tasks</span></h3>
    <table><thead><tr>
      <th data-col="task_name" class="${sortClass('task_name')}">Task</th>
      <th data-col="passed" class="${sortClass('passed')}">Status</th>
      <th data-col="reward" class="${sortClass('reward')}">Score</th>
      <th data-col="cost_usd" class="${sortClass('cost_usd')}">Cost</th>
      <th data-col="num_turns" class="${sortClass('num_turns')}">Turns</th>
    </tr></thead><tbody>`;

  tasks.forEach(t => {
    const scoreNum = t.reward != null ? t.reward * 100 : 0;
    const barColor = scoreNum >= 50 ? "var(--green)" : scoreNum >= 25 ? "var(--amber)" : "var(--red)";
    const isExpanded = expandedTask === t.task_name;
    html += `<tr class="${isExpanded ? 'expanded' : ''}" data-task="${esc(t.task_name)}">
      <td>${esc(t.task_name)}</td>
      <td><span class="badge ${t.passed ? 'pass' : 'fail'}">${t.passed ? 'PASS' : 'FAIL'}</span></td>
      <td><div class="score-bar"><div class="bar"><div class="fill" style="width:${scoreNum}%;background:${barColor}"></div></div>${t.reward != null ? scoreNum.toFixed(0) : "\u2014"}</div></td>
      <td>${t.cost_usd != null ? "$" + t.cost_usd.toFixed(4) : "\u2014"}</td>
      <td>${t.num_turns ?? "\u2014"}</td>
    </tr>`;
    if (isExpanded) html += renderDetailRow(t);
  });

  html += `</tbody></table></div>`;
  main.innerHTML = html;
  bindTableEvents();
  if (iters.length > 1) renderChart(iters, run.name);
}

// --- Epoch timeline ---
function renderTimeline(bench, activeName) {
  const iters = getHistory(bench);
  let html = `<div class="timeline"><h3>Epoch Timeline</h3>`;
  let prevReward = null;
  const bestReward = Math.max(...iters.map(it => it.reward || 0));
  const runsByName = {};
  bench.runs.forEach(r => { runsByName[r.name] = r; });

  iters.forEach((it, idx) => {
    const isBest = it.reward === bestReward;
    const isActive = it.name === activeName;
    const reward = it.reward != null ? (it.reward * 100).toFixed(1) : "?";
    const holdout = it.holdout_reward != null ? (it.holdout_reward * 100).toFixed(1) : null;

    let deltaHtml = "";
    if (prevReward != null && it.reward != null) {
      const d = (it.reward - prevReward) * 100;
      const cls = d > 1 ? "up" : d < -1 ? "down" : "flat";
      deltaHtml = `<span class="tl-delta ${cls}">${d >= 0 ? "+" : ""}${d.toFixed(1)}</span>`;
    }
    prevReward = it.reward;

    let diffStat = "";
    if (idx > 0) {
      const prevRun = runsByName[iters[idx - 1].name];
      const curRun = runsByName[it.name];
      if (prevRun && curRun && prevRun.config && curRun.config) {
        const d = quickDiffStat(prevRun.config, curRun.config);
        if (d.add || d.del) diffStat = `<span class="tl-diff-stat">+${d.add} -${d.del} lines</span>`;
      }
    }

    html += `<div class="tl-row${isActive ? ' tl-active' : ''}" onclick="setMode('view');selectRun('${esc(it.name)}')">
      <div class="tl-gutter"><div class="tl-dot${isBest ? ' best' : ''}"></div><div class="tl-line"></div></div>
      <div class="tl-body">
        <span class="tl-name">${esc(it.name)}</span>
        <div class="tl-scores"><span class="tl-s">S:${reward}%</span>${holdout != null ? `<span class="tl-h">H:${holdout}%</span>` : ""}</div>
        ${deltaHtml}
        ${diffStat}
      </div>
    </div>`;
  });

  html += `</div>`;
  return html;
}

function quickDiffStat(a, b) {
  const la = a.split("\n"), lb = b.split("\n");
  const setA = new Set(la), setB = new Set(lb);
  let add = 0, del = 0;
  lb.forEach(l => { if (!setA.has(l)) add++; });
  la.forEach(l => { if (!setB.has(l)) del++; });
  return { add, del };
}

// --- Compare view ---
function renderCompare() {
  const bench = DATA[currentBench];
  if (!bench || !compareA || !compareB) {
    main.innerHTML = '<div class="empty"><p>Select two runs to compare</p></div>';
    return;
  }
  const runA = bench.runs.find(r => r.name === compareA);
  const runB = bench.runs.find(r => r.name === compareB);
  if (!runA || !runB) return;

  const opts = bench.runs.map(r => `<option value="${esc(r.name)}">${esc(r.name)}</option>`).join("");
  let html = `<div class="compare-header">
    <div class="pick"><label>Baseline (A)</label><select id="cmp-a">${opts}</select></div>
    <div class="pick"><label>Candidate (B)</label><select id="cmp-b">${opts}</select></div>
  </div>`;

  // Score comparison cards
  const sa = runA.scores, sb = runB.scores;
  function cmpCard(label, va, vb, fmt) {
    const a = fmt(va), b = fmt(vb);
    const diff = vb != null && va != null ? vb - va : null;
    const dStr = diff != null ? (diff >= 0 ? "+" : "") + fmt(diff) : "";
    const dColor = diff == null ? "" : diff > 0 ? "color:var(--green)" : diff < 0 ? "color:var(--red)" : "";
    return `<div class="cmp-card"><div class="cmp-label">${label}</div>
      <div class="cmp-vals"><span class="v">${a}</span><span class="arrow">\u2192</span><span class="v">${b}</span><span class="delta" style="${dColor}">${dStr}</span></div></div>`;
  }
  const pct = v => v != null ? (v * 100).toFixed(1) + "%" : "N/A";
  const usd = v => v != null ? "$" + v.toFixed(2) : "N/A";

  html += `<div class="compare-scores">
    ${cmpCard("Mean Reward", sa.mean_reward, sb.mean_reward, pct)}
    ${cmpCard("Pass Rate", sa.pass_rate, sb.pass_rate, pct)}
    ${cmpCard("Cost", sa.total_cost_usd, sb.total_cost_usd, usd)}
  </div>`;

  // Config diff
  if (runA.config && runB.config) {
    const diff = computeDiff(runA.config, runB.config);
    html += `<div class="diff-section"><h3>Harness Diff</h3><div class="diff-box">`;
    diff.forEach(d => {
      const cls = d.type === "add" ? "add" : d.type === "del" ? "del" : "ctx";
      const prefix = d.type === "add" ? "+" : d.type === "del" ? "-" : " ";
      html += `<div class="diff-line ${cls}">${prefix} ${esc(d.text)}</div>`;
    });
    html += `</div></div>`;
  }

  // Task flips
  const tasksA = {}, tasksB = {};
  runA.tasks.forEach(t => { tasksA[t.task_name] = t; });
  runB.tasks.forEach(t => { tasksB[t.task_name] = t; });
  const allTasks = [...new Set([...Object.keys(tasksA), ...Object.keys(tasksB)])].sort();
  const gained = [], lost = [], bothPass = [], bothFail = [];
  allTasks.forEach(name => {
    const pa = tasksA[name]?.passed || false;
    const pb = tasksB[name]?.passed || false;
    if (!pa && pb) gained.push(name);
    else if (pa && !pb) lost.push(name);
    else if (pa && pb) bothPass.push(name);
    else bothFail.push(name);
  });

  html += `<div class="flip-section"><h3>Task Flips <span class="count">${gained.length} gained, ${lost.length} lost</span></h3>`;
  if (gained.length) {
    html += `<div class="flip-group"><div class="flip-group-title"><span class="badge pass">GAINED</span> ${gained.length} tasks now pass</div>
      <div class="flip-tags">${gained.map(n => `<span class="flip-tag gained">${esc(n)}</span>`).join("")}</div></div>`;
  }
  if (lost.length) {
    html += `<div class="flip-group"><div class="flip-group-title"><span class="badge fail">LOST</span> ${lost.length} tasks now fail</div>
      <div class="flip-tags">${lost.map(n => `<span class="flip-tag lost">${esc(n)}</span>`).join("")}</div></div>`;
  }
  html += `<div style="font-size:12px;color:var(--text2);margin-top:8px">Both pass: ${bothPass.length} &middot; Both fail: ${bothFail.length}</div>`;
  html += `</div>`;

  main.innerHTML = html;
  document.getElementById("cmp-a").value = compareA;
  document.getElementById("cmp-b").value = compareB;
  document.getElementById("cmp-a").onchange = e => { compareA = e.target.value; renderSidebar(); renderCompare(); };
  document.getElementById("cmp-b").onchange = e => { compareB = e.target.value; renderSidebar(); renderCompare(); };
}

// LCS-based line diff
function computeDiff(textA, textB) {
  const a = textA.split("\n"), b = textB.split("\n");
  const m = a.length, n = b.length;
  const dp = Array(m + 1).fill(null).map(() => new Uint16Array(n + 1));
  for (let i = 1; i <= m; i++)
    for (let j = 1; j <= n; j++)
      dp[i][j] = a[i-1] === b[j-1] ? dp[i-1][j-1] + 1 : Math.max(dp[i-1][j], dp[i][j-1]);
  const result = [];
  let i = m, j = n;
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && a[i-1] === b[j-1]) { result.unshift({type:"ctx", text:a[i-1]}); i--; j--; }
    else if (j > 0 && (i === 0 || dp[i][j-1] >= dp[i-1][j])) { result.unshift({type:"add", text:b[j-1]}); j--; }
    else { result.unshift({type:"del", text:a[i-1]}); i--; }
  }
  return result;
}

// --- Shared rendering ---
function renderDetailRow(task) {
  let inner = "";
  const judge = task.judge;
  if (judge && judge.dimensions) {
    inner += `<h4>Judge Feedback \u2014 Overall: ${judge.overall_score ?? "\u2014"}/100</h4><div class="dim-grid">`;
    judge.dimensions.forEach((d, i) => {
      const scoreColor = d.score >= 7 ? "var(--green)" : d.score >= 4 ? "var(--amber)" : "var(--red)";
      inner += `<div class="dim-item">
        <div class="dim-score" style="color:${scoreColor}">${d.score}</div>
        <div class="dim-body">
          <div class="dim-title">${esc(d.title)}</div>
          <div class="dim-review" id="review-${i}">${esc(d.review || "")}</div>
          ${d.review && d.review.length > 120 ? `<button class="dim-review-toggle" onclick="event.stopPropagation();toggleReview(this,'review-${i}')">show more</button>` : ""}
        </div></div>`;
    });
    inner += `</div>`;
  } else if (task.judge_raw) {
    inner += `<h4>Judge Feedback</h4><pre style="font-size:12px;color:var(--text2);white-space:pre-wrap;max-height:300px;overflow-y:auto">${esc(task.judge_raw)}</pre>`;
  } else {
    inner += `<p style="color:var(--text2);font-size:13px">No judge feedback available for this task.</p>`;
  }
  return `<tr class="detail-row"><td colspan="5"><div class="detail-inner">${inner}</div></td></tr>`;
}

function renderChart(iterations, activeName) {
  const canvas = document.getElementById("trajectory-chart");
  if (!canvas) return;
  if (chartInstance) chartInstance.destroy();
  const labels = iterations.map(it => it.name);
  const searchData = iterations.map(it => it.reward != null ? +(it.reward * 100).toFixed(1) : null);
  const holdoutData = iterations.map(it => it.holdout_reward != null ? +(it.holdout_reward * 100).toFixed(1) : null);
  const datasets = [{
    label: "Search", data: searchData, borderColor: "#6366f1", backgroundColor: "rgba(99,102,241,0.1)",
    pointRadius: labels.map(l => l === activeName ? 6 : 3),
    pointBackgroundColor: labels.map(l => l === activeName ? "#fff" : "#6366f1"),
    pointBorderColor: "#6366f1", pointBorderWidth: labels.map(l => l === activeName ? 3 : 1),
    tension: 0.3, fill: true,
  }];
  if (holdoutData.some(v => v != null)) {
    datasets.push({ label: "Holdout", data: holdoutData, borderColor: "#22c55e", backgroundColor: "rgba(34,197,94,0.1)",
      pointRadius: 3, tension: 0.3, fill: true, borderDash: [4, 4] });
  }
  chartInstance = new Chart(canvas, {
    type: "line", data: { labels, datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      scales: {
        y: { beginAtZero: true, max: 100, ticks: { callback: v => v + "%", color: "#94a3b8", font: { size: 11 } }, grid: { color: "#2e3345" } },
        x: { ticks: { color: "#94a3b8", font: { size: 11, family: "monospace" } }, grid: { display: false } },
      },
      plugins: {
        legend: { labels: { color: "#e2e8f0", font: { size: 12 } } },
        tooltip: { callbacks: { label: ctx => ctx.dataset.label + ": " + ctx.parsed.y + "%" } },
      },
    },
  });
}

function bindTableEvents() {
  main.querySelectorAll("thead th[data-col]").forEach(th => {
    th.onclick = () => {
      const col = th.dataset.col;
      if (sortCol === col) sortDir *= -1;
      else { sortCol = col; sortDir = 1; }
      renderMain();
    };
  });
  main.querySelectorAll("tbody tr[data-task]").forEach(tr => {
    tr.onclick = (e) => {
      if (e.target.closest(".dim-review-toggle")) return;
      expandedTask = expandedTask === tr.dataset.task ? null : tr.dataset.task;
      renderMain();
    };
  });
}

function sortTasks(tasks) {
  return [...tasks].sort((a, b) => {
    let va = a[sortCol], vb = b[sortCol];
    if (va == null) va = sortCol === "reward" ? -1 : "";
    if (vb == null) vb = sortCol === "reward" ? -1 : "";
    if (typeof va === "boolean") { va = va ? 1 : 0; vb = vb ? 1 : 0; }
    if (typeof va === "string") return va.localeCompare(vb) * sortDir;
    return (va - vb) * sortDir;
  });
}
function sortClass(col) { return sortCol !== col ? "" : sortDir === 1 ? "sorted-asc" : "sorted-desc"; }
function esc(s) { return s == null ? "" : String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;"); }

window.toggleReview = function(btn, id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.toggle("expanded");
  btn.textContent = el.classList.contains("expanded") ? "show less" : "show more";
};

// Boot
if (benchNames.length > 0) {
  benchSelect.value = benchNames[0];
  benchSelect.dispatchEvent(new Event("change"));
  const bench = DATA[benchNames[0]];
  if (bench.runs.length > 0) selectRun(bench.runs[bench.runs.length - 1].name);
}
</script>
</body>
</html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate experiment dashboard")
    parser.add_argument("--experience", default=str(get_experience_root()), help="Path to experience root")
    parser.add_argument("--out", default=str(get_workspace_root() / "dashboard.html"), help="Output HTML path")
    parser.add_argument("--open", action="store_true", help="Open in browser after generating")
    args = parser.parse_args()

    data = collect_runs(Path(args.experience))
    n_runs = sum(len(b["runs"]) for b in data.values())
    n_tasks = sum(len(t) for b in data.values() for r in b["runs"] for t in [r["tasks"]])

    html = HTML_TEMPLATE.replace("__DATA_PLACEHOLDER__", json.dumps(data))

    out_path = Path(args.out)
    out_path.write_text(html)
    print(f"Dashboard: {out_path}")
    print(f"  {len(data)} benchmarks, {n_runs} runs, {n_tasks} task traces")

    if args.open:
        webbrowser.open(f"file://{out_path.resolve()}")


if __name__ == "__main__":
    main()
