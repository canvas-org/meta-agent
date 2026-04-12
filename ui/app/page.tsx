"use client";

import { useState, useEffect, useMemo, useCallback } from "react";
import {
  XAxis, YAxis, Tooltip, Legend, ResponsiveContainer, Area, AreaChart,
} from "recharts";
import type {
  DashboardData, BenchmarkData, TaskResult, HistoryEntry, DimensionReview, HarnessFile, ExperimentConfig,
} from "@/lib/types";

function readUrlParams(): { bench: string; run: string } {
  if (typeof window === "undefined") return { bench: "", run: "" };
  const p = new URLSearchParams(window.location.search);
  return { bench: p.get("bench") ?? "", run: p.get("run") ?? "" };
}

function syncUrl(bench: string, run: string): void {
  if (typeof window === "undefined") return;
  const p = new URLSearchParams();
  if (bench) p.set("bench", bench);
  if (run) p.set("run", run);
  const qs = p.toString();
  const url = qs ? `?${qs}` : window.location.pathname;
  window.history.replaceState(null, "", url);
}

export default function Dashboard() {
  const initial = readUrlParams();
  const [data, setData] = useState<DashboardData | null>(null);
  const [bench, setBench] = useState(initial.bench);
  const [run, setRun] = useState(initial.run);
  const [mode, setMode] = useState<"view" | "compare">("view");
  const [cmpA, setCmpA] = useState("");
  const [cmpB, setCmpB] = useState("");
  const [expanded, setExpanded] = useState("");
  const [sortCol, setSortCol] = useState("task_name");
  const [sortDir, setSortDir] = useState(1);
  const [configOpen, setConfigOpen] = useState(false);
  const [taskFilter, setTaskFilter] = useState<"all" | "pass" | "fail">("all");
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => { syncUrl(bench, run); }, [bench, run]);

  const loadData = useCallback(() => {
    setRefreshing(true);
    fetch("/api/data").then((r) => r.json()).then((d: DashboardData) => {
      setData(d);
      setBench((prev) => {
        if (prev && d[prev]) return prev;
        return Object.keys(d)[0] ?? "";
      });
      setRefreshing(false);
    });
  }, []);

  useEffect(() => { loadData(); }, [loadData]);

  if (!data) return <Loading />;

  const benchData = data[bench];
  const benchNames = Object.keys(data);
  const runNames = new Set(benchData?.runs.map((r) => r.name) ?? []);
  const iters = dedupeIterations((benchData?.history?.iterations ?? []).filter((it) => runNames.has(it.name)));
  const bestHoldout = iters.length ? Math.max(...iters.map((x) => x.holdout_reward ?? 0)) : null;
  const firstReward = iters.length ? iters[0].reward : null;
  const bestSearch = iters.length ? Math.max(...iters.map((x) => x.reward ?? 0)) : null;

  return (
    <div className="flex h-screen flex-col bg-surface-2">
      {/* Top bar */}
      <header className="flex h-12 shrink-0 items-center gap-3 border-b border-border bg-white px-5">
        <span className="text-[15px] font-semibold tracking-tight text-slate-900">meta-agent <span className="font-normal text-slate-400">workbench</span></span>
        <span className="text-slate-300">/</span>
        <select value={bench}
          onChange={(e) => { setBench(e.target.value); setRun(""); setMode("view"); setTaskFilter("all"); }}
          className="rounded-md border border-slate-200 bg-white px-2.5 py-1 text-xs font-mono text-slate-600 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-400"
        >
          {benchNames.map((n) => <option key={n} value={n}>{n}</option>)}
        </select>

        <div className="ml-auto flex items-center gap-3">
          <button onClick={loadData} disabled={refreshing}
            className={`flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium text-slate-500 hover:text-slate-700 hover:bg-slate-50 transition-colors ${refreshing ? "opacity-50" : ""}`}>
            <svg className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
            Refresh
          </button>
          <div className="flex gap-0.5 rounded-lg bg-slate-100 p-0.5">
            {(["view", "compare"] as const).map((m) => (
              <button key={m} onClick={() => setMode(m)}
                className={`rounded-md px-3 py-1 text-xs font-medium transition-all ${
                  mode === m ? "bg-white text-slate-900 shadow-sm" : "text-slate-500 hover:text-slate-700"
                }`}
              >{m === "view" ? "View" : "Compare"}</button>
            ))}
          </div>
        </div>
      </header>

      <div className="flex flex-1 overflow-hidden">
        {/* Sidebar */}
        <aside className="flex w-60 shrink-0 flex-col border-r border-border bg-white overflow-y-auto">
          {/* Sidebar summary — clickable to go to overview */}
          <button onClick={() => { setRun(""); setMode("view"); }}
            className={`w-full border-b border-border px-4 py-4 text-left transition-colors ${!run && mode === "view" ? "bg-indigo-50/50" : "hover:bg-slate-50"}`}>
            {iters.length > 0 ? (
              <>
                <div className="flex items-baseline justify-between">
                  <span className="text-[10px] font-semibold uppercase tracking-widest text-slate-400">Best holdout</span>
                  <span className="font-mono text-lg font-bold text-emerald-600">{bestHoldout != null && bestHoldout > 0 ? `${(bestHoldout * 100).toFixed(1)}%` : "—"}</span>
                </div>
                {firstReward != null && bestSearch != null && (
                  <div className="mt-1.5 flex items-center gap-1.5 text-[11px] text-slate-400">
                    <span className="font-mono">{(firstReward * 100).toFixed(0)}%</span>
                    <span>→</span>
                    <span className="font-mono font-medium text-slate-600">{(bestSearch * 100).toFixed(0)}%</span>
                    <span className="ml-auto">{iters.length} epochs</span>
                  </div>
                )}
              </>
            ) : (
              <div className="flex items-center gap-2">
                <span className="text-[10px] font-semibold uppercase tracking-widest text-slate-400">Experiment Overview</span>
              </div>
            )}
          </button>

          <div className="px-4 pt-3 pb-2">
            <span className="text-[10px] font-semibold uppercase tracking-widest text-slate-400">Candidates</span>
          </div>
          <div className="px-2 pb-2 space-y-0.5">
            {benchData?.runs.map((r) => {
              const active = mode === "view" ? run === r.name : (cmpA === r.name || cmpB === r.name);
              const reward = r.scores.mean_reward;
              const pct = reward != null ? `${(reward * 100).toFixed(0)}%` : `${(r.scores.pass_rate * 100).toFixed(0)}%`;
              const badge = genBadge(benchData, r.name);
              return (
                <button key={r.name}
                  onClick={() => mode === "view" ? (setRun(r.name), setExpanded(""), setConfigOpen(false), setTaskFilter("all")) : toggleCompare(r.name, cmpA, cmpB, setCmpA, setCmpB)}
                  className={`flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-[13px] transition-all ${
                    active ? "bg-indigo-50 text-indigo-700 font-medium" : "text-slate-600 hover:bg-slate-50"
                  }`}
                >
                  <span className="font-mono">{r.name}</span>
                  <span className="flex items-center gap-1.5">
                    {badge && <span className={`rounded px-1.5 py-0.5 text-[9px] font-bold ${badge.cls}`}>{badge.text}</span>}
                    <span className={`font-mono text-[11px] ${active ? "text-indigo-500" : "text-slate-400"}`}>{pct}</span>
                  </span>
                </button>
              );
            })}
          </div>
        </aside>

        {/* Main content */}
        <main className="flex-1 overflow-y-auto p-8">
          {mode === "compare" && benchData
            ? <CompareView data={benchData} a={cmpA} b={cmpB} setA={setCmpA} setB={setCmpB} />
            : mode === "view" && benchData && run
            ? <RunView key={run} data={benchData} bench={bench} runName={run} expanded={expanded} setExpanded={setExpanded}
                sortCol={sortCol} sortDir={sortDir} setSortCol={setSortCol} setSortDir={setSortDir}
                configOpen={configOpen} setConfigOpen={setConfigOpen}
                taskFilter={taskFilter} setTaskFilter={setTaskFilter}
                goBack={() => setRun("")} />
            : benchData
            ? <BenchmarkOverview data={benchData} bench={bench}
                onSelectRun={(n) => { setRun(n); setExpanded(""); setConfigOpen(false); setTaskFilter("all"); }} />
            : null
          }
        </main>
      </div>
    </div>
  );
}

/* ─── Benchmark Overview ─────────────────────────────────── */

function BenchmarkOverview({ data, bench, onSelectRun }: {
  data: BenchmarkData; bench: string; onSelectRun: (name: string) => void;
}) {
  const names = new Set(data.runs.map((r) => r.name));
  const iters = dedupeIterations((data.history?.iterations ?? []).filter((it) => names.has(it.name)));
  const cfg = data.history?.config;
  const model = data.history?.model ?? data.runs[0]?.scores.model ?? "—";
  const bestHoldout = iters.length ? Math.max(...iters.map((x) => x.holdout_reward ?? 0)) : null;
  const bestSearch = iters.length ? Math.max(...iters.map((x) => x.reward ?? 0)) : null;
  const totalCost = data.runs.reduce((sum, r) => sum + (r.scores.total_cost_usd ?? 0), 0);
  const ranked = data.runs;

  return (
    <div className="mx-auto max-w-4xl space-y-8 animate-in">
      {/* Header */}
      <div>
        <h2 className="font-mono text-2xl font-bold tracking-tight text-slate-900">{bench}</h2>
        {cfg?.description && (
          <p className="mt-1 text-sm text-slate-500">{cfg.description}</p>
        )}
      </div>

      {/* Experiment config + stats — only show fields that have real data */}
      <ExperimentPanel model={model} cfg={cfg} iters={iters}
        bestSearch={bestSearch} bestHoldout={bestHoldout}
        totalCost={totalCost} nCandidates={data.runs.length}
        nTasks={data.runs[0]?.scores.n_tasks ?? null} />

      {/* Trajectory chart */}
      {iters.length > 1 && <TrajectoryChart iterations={iters} activeName="" />}

      {/* Candidates */}
      <div>
        <h3 className="mb-3 text-sm font-semibold text-slate-700">Candidates</h3>
        <div className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-200 bg-slate-50/60">
                {["", "Name", "Reward", "Pass Rate", "Holdout", "Cost", ""].map((h, i) => (
                  <th key={`${h}-${i}`} className={`py-2.5 text-left text-[10px] font-semibold uppercase tracking-widest text-slate-400 ${i === 0 ? "pl-4 pr-0 w-6" : "px-4"}`}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {ranked.map((r, idx) => {
                const h = iters.find((x) => x.name === r.name);
                const badge = genBadge(data, r.name);
                const best = iters.length ? Math.max(...iters.map((x) => x.reward ?? 0)) : 0;
                const isBest = h?.reward === best && best > 0;
                const prevRun = idx > 0 ? ranked[idx - 1] : null;
                const delta = prevRun && r.scores.mean_reward != null && prevRun.scores.mean_reward != null
                  ? (r.scores.mean_reward - prevRun.scores.mean_reward) * 100 : null;
                const prevRunData = prevRun ? data.runs.find((x) => x.name === prevRun.name) : null;
                const diff = prevRunData?.config && r.config ? quickDiffStat(prevRunData.config, r.config) : null;
                return (
                  <tr key={r.name} onClick={() => onSelectRun(r.name)}
                    className="border-b border-slate-100 cursor-pointer hover:bg-indigo-50/40 transition-colors">
                    <td className="pl-4 pr-0 py-3 w-6">
                      <div className={`h-2.5 w-2.5 rounded-full ${isBest ? "bg-emerald-500" : "bg-slate-300"}`} />
                    </td>
                    <td className="px-4 py-3 font-mono text-[13px] font-medium text-slate-700">{r.name}</td>
                    <td className="px-4 py-3">
                      <span className="font-mono text-sm text-slate-700">
                        {r.scores.mean_reward != null ? `${(r.scores.mean_reward * 100).toFixed(1)}%` : "—"}
                      </span>
                      {delta != null && (
                        <span className={`ml-2 rounded-full px-1.5 py-0.5 text-[10px] font-semibold ${
                          delta > 1 ? "bg-emerald-50 text-emerald-700" : delta < -1 ? "bg-red-50 text-red-600" : "bg-slate-100 text-slate-500"
                        }`}>
                          {delta >= 0 ? "+" : ""}{delta.toFixed(1)}
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <span className="font-mono text-sm text-slate-700">{r.scores.n_passed}/{r.scores.n_tasks}</span>
                      <span className="ml-1.5 text-xs text-slate-400">{(r.scores.pass_rate * 100).toFixed(0)}%</span>
                    </td>
                    <td className="px-4 py-3 font-mono text-sm text-slate-500">
                      {h?.holdout_reward != null ? `${(h.holdout_reward * 100).toFixed(1)}%` : "—"}
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-slate-400">
                      {r.scores.total_cost_usd != null ? `$${r.scores.total_cost_usd.toFixed(2)}` : "—"}
                    </td>
                    <td className="px-4 py-3 text-right whitespace-nowrap">
                      {badge && <span className={`rounded px-1.5 py-0.5 text-[9px] font-bold ${badge.cls}`}>{badge.text}</span>}
                      {diff && (diff.add > 0 || diff.del > 0) && (
                        <span className="ml-1.5 text-[10px] text-slate-400">+{diff.add} -{diff.del}</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

/* ─── Run View ────────────────────────────────────────────── */

interface RunViewProps {
  data: BenchmarkData; bench: string; runName: string; expanded: string; setExpanded: (s: string) => void;
  sortCol: string; sortDir: number; setSortCol: (s: string) => void; setSortDir: (n: number) => void;
  configOpen: boolean; setConfigOpen: (b: boolean) => void;
  taskFilter: "all" | "pass" | "fail"; setTaskFilter: (f: "all" | "pass" | "fail") => void;
  goBack: () => void;
}

type RunTab = "traces" | "config";

function RunView({ data, bench, runName, expanded, setExpanded, sortCol, sortDir, setSortCol, setSortDir, configOpen, setConfigOpen, taskFilter, setTaskFilter, goBack }: RunViewProps) {
  const r = data.runs.find((x) => x.name === runName);
  if (!r) return null;
  const s = r.scores;
  const names = new Set(data.runs.map((x) => x.name));
  const iters = dedupeIterations((data.history?.iterations ?? []).filter((it) => names.has(it.name)));
  const h = iters.find((x) => x.name === runName);
  const nPass = r.tasks.filter((t) => t.passed).length;
  const nFail = r.tasks.filter((t) => !t.passed).length;
  const [tab, setTab] = useState<RunTab>("traces");

  return (
    <div className="mx-auto max-w-4xl space-y-8 animate-in">
      {/* Back + heading */}
      <div>
        <button onClick={goBack}
          className="mb-2 flex items-center gap-1 text-xs text-indigo-600 hover:text-indigo-500 font-medium transition-colors">
          <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
          </svg>
          All candidates
        </button>
        <div className="flex items-baseline justify-between">
          <h2 className="font-mono text-2xl font-bold tracking-tight text-slate-900">{r.name}</h2>
          {h && (
            <span className="text-xs text-slate-400">
              Epoch {iters.indexOf(h) + 1} of {iters.length}
              {h.holdout_reward != null && <span className="ml-2 font-mono text-emerald-600">Holdout {(h.holdout_reward * 100).toFixed(1)}%</span>}
            </span>
          )}
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <StatCard label="Mean Reward" value={s.mean_reward != null ? `${(s.mean_reward * 100).toFixed(1)}%` : "N/A"} />
        <StatCard label="Pass Rate" value={`${s.n_passed}/${s.n_tasks}`} sub={`${(s.pass_rate * 100).toFixed(0)}%`} accent />
        {h?.holdout_reward != null && (() => {
          const d = h.reward - h.holdout_reward;
          return <StatCard label="Holdout" value={`${(h.holdout_reward * 100).toFixed(1)}%`}
            sub={`\u0394 ${(d * 100).toFixed(1)}pp`}
            subColor={d > 0.15 ? "text-red-500" : d > 0.08 ? "text-amber-500" : "text-emerald-600"} />;
        })()}
        <StatCard label="Cost" value={s.total_cost_usd != null ? `$${s.total_cost_usd.toFixed(2)}` : "N/A"} />
        <StatCard label="Model" value={s.model || "\u2014"} small />
      </div>

      {/* Tab bar */}
      <div className="flex gap-0.5 rounded-lg bg-slate-100 p-0.5 w-fit">
        <button onClick={() => setTab("traces")}
          className={`rounded-md px-3 py-1 text-xs font-medium transition-all ${
            tab === "traces" ? "bg-white text-slate-900 shadow-sm" : "text-slate-500 hover:text-slate-700"
          }`}>Traces</button>
        <button onClick={() => setTab("config")}
          className={`rounded-md px-3 py-1 text-xs font-medium transition-all ${
            tab === "config" ? "bg-white text-slate-900 shadow-sm" : "text-slate-500 hover:text-slate-700"
          }`}>
          Config {r.harnessFiles.length > 0 && <span className="ml-1 text-slate-400">{r.harnessFiles.length}</span>}
        </button>
      </div>

      {/* Tab content */}
      {tab === "traces" ? (
        <TaskTable tasks={r.tasks} expanded={expanded} setExpanded={setExpanded}
          sortCol={sortCol} sortDir={sortDir} setSortCol={setSortCol} setSortDir={setSortDir}
          filter={taskFilter} setFilter={setTaskFilter} nPass={nPass} nFail={nFail}
          benchName={bench} candidateName={runName} />
      ) : (
        r.harnessFiles.length > 0
          ? <HarnessPanel files={r.harnessFiles} />
          : <div className="text-sm text-slate-400">No harness config files found.</div>
      )}

    </div>
  );
}

function StatCard({ label, value, sub, accent, subColor, small }: {
  label: string; value: string; sub?: string; accent?: boolean; subColor?: string; small?: boolean;
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="text-[10px] font-semibold uppercase tracking-widest text-slate-400 mb-1.5">{label}</div>
      <div className={`font-mono font-bold tracking-tight ${small ? "text-sm text-slate-600" : "text-xl"} ${accent ? "text-emerald-600" : "text-slate-900"}`}>{value}</div>
      {sub && <div className={`mt-1 text-xs font-medium ${subColor ?? "text-slate-400"}`}>{sub}</div>}
    </div>
  );
}

function ExperimentPanel({ model, cfg, iters, bestSearch, bestHoldout, totalCost, nCandidates, nTasks }: {
  model: string; cfg: ExperimentConfig | undefined;
  iters: HistoryEntry[]; bestSearch: number | null; bestHoldout: number | null;
  totalCost: number; nCandidates: number; nTasks: number | null;
}) {
  const items: { label: string; value: string; accent?: boolean; bold?: boolean }[] = [];

  items.push({ label: "Eval Model", value: model });
  if (cfg?.proposer_model) items.push({ label: "Proposer", value: cfg.proposer_model });
  if (cfg?.harness) items.push({ label: "Harness", value: cfg.runtime ? `${cfg.harness} / ${cfg.runtime}` : cfg.harness });
  if (cfg?.concurrency != null) items.push({ label: "Concurrency", value: `${cfg.concurrency}` });

  const taskLabel = cfg?.n_search_tasks != null
    ? `${cfg.n_search_tasks}${cfg.fast ? " (fast)" : ""}`
    : nTasks != null ? `${nTasks}` : null;
  if (taskLabel) items.push({ label: "Search Tasks", value: taskLabel });
  if (cfg?.batch_size != null) items.push({ label: "Batch Size", value: `${cfg.batch_size}` });
  if (cfg?.seed != null) items.push({ label: "Seed", value: `${cfg.seed}` });

  if (cfg?.holdout_benchmark) {
    const name = cfg.holdout_benchmark.split("/").pop()?.replace(".yaml", "") ?? cfg.holdout_benchmark;
    items.push({ label: "Holdout", value: name });
  }

  if (iters.length > 0 || (cfg?.max_iterations != null)) {
    items.push({ label: "Epochs", value: `${iters.length}${cfg?.max_iterations ? ` / ${cfg.max_iterations}` : ""}` });
  }
  if (cfg?.timeout != null) items.push({ label: "Timeout", value: `${cfg.timeout}s` });

  items.push({ label: "Candidates", value: `${nCandidates}` });
  if (totalCost > 0) items.push({ label: "Total Cost", value: `$${totalCost.toFixed(2)}` });
  if (bestSearch != null && bestSearch > 0) items.push({ label: "Best Search", value: `${(bestSearch * 100).toFixed(1)}%`, bold: true });
  if (bestHoldout != null && bestHoldout > 0) items.push({ label: "Best Holdout", value: `${(bestHoldout * 100).toFixed(1)}%`, bold: true, accent: true });

  const colsMap: Record<number, string> = { 3: "sm:grid-cols-3", 4: "sm:grid-cols-4", 5: "sm:grid-cols-5", 6: "sm:grid-cols-6" };
  const cols = colsMap[Math.min(Math.max(items.length, 3), 6)] ?? "sm:grid-cols-4";

  return (
    <div className={`rounded-xl border border-slate-200 bg-white shadow-sm grid grid-cols-2 ${cols}`}>
      {items.map((it) => (
        <div key={it.label} className="px-4 py-3 border-b border-r border-slate-100 last:border-r-0">
          <div className="text-[10px] font-semibold uppercase tracking-widest text-slate-400 mb-1">{it.label}</div>
          <div className={`font-mono text-sm truncate ${
            it.bold ? (it.accent ? "font-bold text-emerald-600" : "font-bold text-slate-900") : "text-slate-700"
          }`}>{it.value}</div>
        </div>
      ))}
    </div>
  );
}

/* ─── Chart ───────────────────────────────────────────────── */

function TrajectoryChart({ iterations, activeName }: { iterations: HistoryEntry[]; activeName: string }) {
  const hasHoldout = iterations.some((it) => it.holdout_reward != null);
  const chartData = iterations.map((it) => ({
    name: it.name,
    search: it.reward != null ? +(it.reward * 100).toFixed(1) : null,
    ...(hasHoldout ? { holdout: it.holdout_reward != null ? +(it.holdout_reward * 100).toFixed(1) : null } : {}),
  }));

  const allValues = chartData.flatMap((d) => {
    const vals: (number | null | undefined)[] = [d.search];
    if ("holdout" in d) vals.push(d.holdout as number | null | undefined);
    return vals.filter((v): v is number => v != null);
  });
  const dataMin = Math.min(...allValues);
  const dataMax = Math.max(...allValues);
  const range = dataMax - dataMin || 10;
  const yMin = Math.max(0, Math.floor((dataMin - range * 0.3) / 5) * 5);
  const yMax = Math.min(100, Math.ceil((dataMax + range * 0.15) / 5) * 5);

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h3 className="mb-4 text-sm font-semibold text-slate-700">Optimization Trajectory</h3>
      <ResponsiveContainer width="100%" height={220}>
        <AreaChart data={chartData}>
          <defs>
            <linearGradient id="gSearch" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#6366f1" stopOpacity={0.12} />
              <stop offset="100%" stopColor="#6366f1" stopOpacity={0} />
            </linearGradient>
            {hasHoldout && (
              <linearGradient id="gHoldout" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#059669" stopOpacity={0.12} />
                <stop offset="100%" stopColor="#059669" stopOpacity={0} />
              </linearGradient>
            )}
          </defs>
          <XAxis dataKey="name" tick={{ fill: "#94a3b8", fontSize: 11, fontFamily: "JetBrains Mono, monospace" }} axisLine={false} tickLine={false} />
          <YAxis domain={[yMin, yMax]} tick={{ fill: "#94a3b8", fontSize: 11 }} axisLine={false} tickLine={false}
            tickFormatter={(v: number) => `${v}%`} width={40} />
          <Tooltip contentStyle={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 10, fontSize: 12, fontFamily: "Inter, sans-serif", boxShadow: "0 4px 12px rgba(0,0,0,0.05)" }}
            labelStyle={{ color: "#64748b", fontFamily: "JetBrains Mono, monospace", marginBottom: 4 }}
            formatter={(v: number, name: string) => [`${v}%`, name === "search" ? "Search" : "Holdout"]} />
          {hasHoldout && <Legend iconType="line" wrapperStyle={{ fontSize: 12, color: "#64748b", fontFamily: "Inter, sans-serif" }} />}
          <Area type="monotone" dataKey="search" stroke="#6366f1" strokeWidth={2} fill="url(#gSearch)" dot={{ r: 3, fill: "#6366f1", stroke: "#fff", strokeWidth: 2 }} activeDot={{ r: 5, fill: "#6366f1", stroke: "#fff", strokeWidth: 2 }} name="Search" />
          {hasHoldout && <Area type="monotone" dataKey="holdout" stroke="#059669" strokeWidth={2} fill="url(#gHoldout)" dot={{ r: 3, fill: "#059669", stroke: "#fff", strokeWidth: 2 }} activeDot={{ r: 5, fill: "#059669", stroke: "#fff", strokeWidth: 2 }} strokeDasharray="4 4" name="Holdout" />}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

/* ─── Epoch Timeline ──────────────────────────────────────── */

function EpochTimeline({ data, activeName, onSelect }: { data: BenchmarkData; activeName: string; onSelect: (n: string) => void }) {
  const names = new Set(data.runs.map((r) => r.name));
  const iters = dedupeIterations((data.history?.iterations ?? []).filter((it) => names.has(it.name)));
  const best = Math.max(...iters.map((x) => x.reward ?? 0));
  const runMap = new Map(data.runs.map((r) => [r.name, r]));

  return (
    <div>
      <h3 className="mb-3 text-sm font-semibold text-slate-700">Epoch Timeline</h3>
      <div className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
        {iters.map((it, idx) => {
          const isBest = it.reward === best;
          const isActive = it.name === activeName;
          const prev = idx > 0 ? iters[idx - 1] : null;
          const delta = prev && it.reward != null && prev.reward != null ? (it.reward - prev.reward) * 100 : null;
          const prevRun = prev ? runMap.get(prev.name) : null;
          const curRun = runMap.get(it.name);
          const diff = prevRun?.config && curRun?.config ? quickDiffStat(prevRun.config, curRun.config) : null;

          return (
            <button key={it.name} onClick={() => onSelect(it.name)}
              className={`flex w-full items-stretch text-left transition-colors border-b border-slate-100 last:border-b-0 ${isActive ? "bg-indigo-50/60" : "hover:bg-slate-50"}`}>
              <div className="flex w-8 flex-col items-center pt-3.5">
                <div className={`h-2.5 w-2.5 rounded-full ring-2 ring-white ${isBest ? "bg-emerald-500" : "bg-slate-300"}`} />
                {idx < iters.length - 1 && <div className="mt-1 w-px flex-1 bg-slate-200" />}
              </div>
              <div className="flex flex-1 items-center gap-4 py-2.5 pr-4 text-sm">
                <span className={`w-20 font-mono font-semibold text-[13px] ${isActive ? "text-indigo-700" : "text-slate-700"}`}>{it.name}</span>
                <span className="font-mono text-xs text-indigo-600">S:{(it.reward * 100).toFixed(1)}%</span>
                {it.holdout_reward != null && (
                  <span className="font-mono text-xs text-emerald-600">H:{(it.holdout_reward * 100).toFixed(1)}%</span>
                )}
                {delta != null && (
                  <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${
                    delta > 1 ? "bg-emerald-50 text-emerald-700" : delta < -1 ? "bg-red-50 text-red-600" : "bg-slate-100 text-slate-500"
                  }`}>
                    {delta >= 0 ? "+" : ""}{delta.toFixed(1)}
                  </span>
                )}
                {diff && (diff.add > 0 || diff.del > 0) && (
                  <span className="ml-auto text-[11px] text-slate-400">+{diff.add} -{diff.del} lines</span>
                )}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

/* ─── Task Table ──────────────────────────────────────────── */

function TaskTable({ tasks, expanded, setExpanded, sortCol, sortDir, setSortCol, setSortDir, filter, setFilter, nPass, nFail, benchName, candidateName }: {
  tasks: TaskResult[]; expanded: string; setExpanded: (s: string) => void;
  sortCol: string; sortDir: number; setSortCol: (s: string) => void; setSortDir: (n: number) => void;
  filter: "all" | "pass" | "fail"; setFilter: (f: "all" | "pass" | "fail") => void;
  nPass: number; nFail: number; benchName: string; candidateName: string;
}) {
  const filtered = useMemo(() => {
    const base = filter === "pass" ? tasks.filter((t) => t.passed) : filter === "fail" ? tasks.filter((t) => !t.passed) : tasks;
    return sortTasks(base, sortCol, sortDir);
  }, [tasks, sortCol, sortDir, filter]);

  const toggleSort = useCallback((col: string) => {
    if (sortCol === col) setSortDir(sortDir * -1);
    else { setSortCol(col); setSortDir(1); }
  }, [sortCol, sortDir, setSortCol, setSortDir]);

  const cols = [
    { key: "task_name", label: "Task" }, { key: "passed", label: "Status" },
    { key: "reward", label: "Score" }, { key: "cost_usd", label: "Cost" }, { key: "num_turns", label: "Turns" },
  ];

  const filters: { key: "all" | "pass" | "fail"; label: string; count: number }[] = [
    { key: "all", label: "All", count: tasks.length },
    { key: "pass", label: "Pass", count: nPass },
    { key: "fail", label: "Fail", count: nFail },
  ];

  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-700">Traces</h3>
        <div className="flex gap-0.5 rounded-lg bg-slate-100 p-0.5">
          {filters.map((f) => (
            <button key={f.key} onClick={() => setFilter(f.key)}
              className={`rounded-md px-2.5 py-1 text-[11px] font-medium transition-all ${
                filter === f.key ? "bg-white text-slate-800 shadow-sm" : "text-slate-500 hover:text-slate-700"
              }`}>
              {f.label} <span className={filter === f.key ? "text-slate-400" : "text-slate-400"}>{f.count}</span>
            </button>
          ))}
        </div>
      </div>
      <div className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-200 bg-slate-50/60">
              {cols.map((c) => (
                <th key={c.key} onClick={() => toggleSort(c.key)}
                  className="px-4 py-2.5 text-left text-[10px] font-semibold uppercase tracking-widest text-slate-400 cursor-pointer hover:text-slate-600 select-none">
                  {c.label}
                  {sortCol === c.key && <span className="ml-1 text-indigo-500">{sortDir === 1 ? "\u25B2" : "\u25BC"}</span>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filtered.map((t) => {
              const scoreNum = t.reward != null ? t.reward * 100 : 0;
              const isExp = expanded === t.task_name;
              return (
                <TaskRow key={t.task_name} task={t} scoreNum={scoreNum} isExpanded={isExp}
                  onClick={() => setExpanded(isExp ? "" : t.task_name)}
                  benchName={benchName} candidateName={candidateName} />
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function TaskRow({ task: t, scoreNum, isExpanded, onClick, benchName, candidateName }: {
  task: TaskResult; scoreNum: number; isExpanded: boolean; onClick: () => void;
  benchName: string; candidateName: string;
}) {
  const barColor = scoreNum >= 50 ? "bg-emerald-500" : scoreNum >= 25 ? "bg-amber-400" : "bg-red-400";
  return (
    <>
      <tr onClick={onClick} className={`border-b border-slate-100 cursor-pointer transition-colors ${isExpanded ? "bg-slate-50" : "hover:bg-slate-50/60"}`}>
        <td className="px-4 py-2.5 font-mono text-[13px] text-slate-700">{t.task_name}</td>
        <td className="px-4 py-2.5">
          <span className={`inline-block rounded-full px-2.5 py-0.5 text-[11px] font-semibold ${
            t.passed ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-600"
          }`}>{t.passed ? "Pass" : "Fail"}</span>
        </td>
        <td className="px-4 py-2.5">
          <div className="flex items-center gap-2.5">
            <div className="h-1.5 w-14 rounded-full bg-slate-100 overflow-hidden">
              <div className={`h-full rounded-full ${barColor} transition-all`} style={{ width: `${scoreNum}%` }} />
            </div>
            <span className="font-mono text-xs text-slate-500">{t.reward != null ? scoreNum.toFixed(0) : "\u2014"}</span>
          </div>
        </td>
        <td className="px-4 py-2.5 font-mono text-xs text-slate-400">{t.cost_usd != null ? `$${t.cost_usd.toFixed(4)}` : "\u2014"}</td>
        <td className="px-4 py-2.5 font-mono text-xs text-slate-400">{t.num_turns ?? "\u2014"}</td>
      </tr>
      {isExpanded && (
        <tr><td colSpan={5} className="border-b border-slate-200 bg-slate-50/80 p-0">
          <JudgePanel task={t} />
          <TraceViewer benchName={benchName} candidateName={candidateName} taskName={t.task_name} />
        </td></tr>
      )}
    </>
  );
}

/* ─── Judge Panel ─────────────────────────────────────────── */

function JudgePanel({ task }: { task: TaskResult }) {
  const [expandedReview, setExpandedReview] = useState<number | null>(null);
  const judge = task.judge;

  if (!judge?.dimensions) {
    return (
      <div className="p-5 text-sm text-slate-500 animate-in">
        {task.judge_raw ? <pre className="max-h-60 overflow-auto whitespace-pre-wrap text-xs">{task.judge_raw}</pre> : "No judge feedback available."}
      </div>
    );
  }

  return (
    <div className="p-5 animate-in">
      <div className="mb-3 text-xs font-semibold text-slate-500">
        Judge Feedback &mdash; Overall: <span className="text-slate-800 text-sm">{judge.overall_score}/100</span>
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        {judge.dimensions.map((d: DimensionReview, i: number) => {
          const color = d.score >= 7 ? "text-emerald-600" : d.score >= 4 ? "text-amber-500" : "text-red-500";
          const bg = d.score >= 7 ? "bg-emerald-50" : d.score >= 4 ? "bg-amber-50" : "bg-red-50";
          const isOpen = expandedReview === i;
          return (
            <div key={i} className="flex gap-3 rounded-lg border border-slate-100 bg-white p-3">
              <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-xs font-bold ${color} ${bg}`}>{d.score}</span>
              <div className="min-w-0 flex-1">
                <div className="text-xs font-semibold text-slate-700 leading-snug">{d.title}</div>
                <div className={`mt-1 text-[11px] leading-relaxed text-slate-400 ${isOpen ? "" : "line-clamp-2"}`}>{d.review}</div>
                {d.review.length > 120 && (
                  <button onClick={(e) => { e.stopPropagation(); setExpandedReview(isOpen ? null : i); }}
                    className="mt-0.5 text-[11px] text-indigo-600 hover:text-indigo-500 font-medium">{isOpen ? "less" : "more"}</button>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ─── Trace Viewer ────────────────────────────────────────── */

interface TraceEvent {
  type: "message" | "tool_call" | "tool_result" | "error" | "meta" | "text";
  content: string;
  tool?: string;
}

interface TraceData {
  events: TraceEvent[];
  format: string;
  size: number;
}

function TraceViewer({ benchName, candidateName, taskName }: { benchName: string; candidateName: string; taskName: string }) {
  const [trace, setTrace] = useState<TraceData | null>(null);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);

  const loadTrace = useCallback(() => {
    if (trace) { setOpen(!open); return; }
    setLoading(true);
    setOpen(true);
    fetch(`/api/trace?benchmark=${encodeURIComponent(benchName)}&candidate=${encodeURIComponent(candidateName)}&task=${encodeURIComponent(taskName)}`)
      .then((r) => r.json())
      .then((d: TraceData) => { setTrace(d); setLoading(false); })
      .catch(() => { setTrace({ events: [], format: "error", size: 0 }); setLoading(false); });
  }, [trace, open, benchName, candidateName, taskName]);

  return (
    <div className="border-t border-slate-100 px-5 py-3">
      <button onClick={loadTrace} className="flex items-center gap-1.5 text-xs text-indigo-600 hover:text-indigo-500 font-medium transition-colors">
        <svg className={`h-3 w-3 transition-transform duration-150 ${open ? "rotate-90" : ""}`} fill="currentColor" viewBox="0 0 20 20">
          <path fillRule="evenodd" d="M7.21 14.77a.75.75 0 01.02-1.06L11.168 10 7.23 6.29a.75.75 0 111.04-1.08l4.5 4.25a.75.75 0 010 1.08l-4.5 4.25a.75.75 0 01-1.06-.02z" clipRule="evenodd" />
        </svg>
        Agent trace
        {trace && trace.size > 0 && <span className="text-slate-400 font-normal ml-1">({(trace.size / 1024).toFixed(1)}KB · {trace.events.length} events)</span>}
      </button>
      {open && (
        <div className="mt-2 animate-in">
          {loading ? (
            <div className="flex items-center gap-2 py-4 text-xs text-slate-400">
              <div className="h-3.5 w-3.5 animate-spin rounded-full border border-indigo-400 border-t-transparent" /> Loading trace...
            </div>
          ) : !trace || trace.events.length === 0 ? (
            <div className="py-3 text-xs text-slate-400">No trace available for this task.</div>
          ) : (
            <div className="max-h-[500px] overflow-auto rounded-lg border border-slate-200 bg-white">
              {trace.events.map((ev, i) => <TraceEventRow key={i} event={ev} />)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function TraceEventRow({ event: ev }: { event: TraceEvent }) {
  const [expanded, setExpanded] = useState(false);
  const isLong = ev.content.length > 200;

  if (ev.type === "message") {
    return (
      <div className="border-b border-slate-100 px-4 py-2.5">
        <div className="flex items-start gap-2.5">
          <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-indigo-50 text-[10px] font-bold text-indigo-600">A</span>
          <div className="min-w-0 flex-1">
            <div className={`text-[12px] leading-relaxed text-slate-700 whitespace-pre-wrap ${!expanded && isLong ? "line-clamp-3" : ""}`}>{ev.content}</div>
            {isLong && (
              <button onClick={() => setExpanded(!expanded)} className="mt-1 text-[11px] text-indigo-600 hover:text-indigo-500 font-medium">
                {expanded ? "less" : "more"}
              </button>
            )}
          </div>
        </div>
      </div>
    );
  }

  if (ev.type === "tool_call") {
    return (
      <div className="border-b border-slate-100 px-4 py-2 bg-slate-50/50">
        <div className="flex items-start gap-2.5">
          <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-amber-50 text-[10px] font-bold text-amber-600">T</span>
          <div className="min-w-0 flex-1">
            {ev.tool && <span className="text-[10px] font-semibold uppercase tracking-wide text-amber-600">{ev.tool}</span>}
            {ev.content && (
              <pre className={`mt-0.5 text-[11px] leading-relaxed text-slate-500 font-mono whitespace-pre-wrap ${!expanded && isLong ? "line-clamp-3" : ""}`}>{ev.content}</pre>
            )}
            {isLong && (
              <button onClick={() => setExpanded(!expanded)} className="mt-1 text-[11px] text-indigo-600 hover:text-indigo-500 font-medium">
                {expanded ? "less" : "more"}
              </button>
            )}
          </div>
        </div>
      </div>
    );
  }

  if (ev.type === "tool_result") {
    return (
      <div className="border-b border-slate-100 px-4 py-2 bg-slate-50/30">
        <div className="flex items-start gap-2.5">
          <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-slate-100 text-[10px] font-bold text-slate-500">R</span>
          <pre className={`min-w-0 flex-1 text-[11px] leading-relaxed text-slate-400 font-mono whitespace-pre-wrap ${!expanded && isLong ? "line-clamp-2" : ""}`}>{ev.content}</pre>
          {isLong && (
            <button onClick={() => setExpanded(!expanded)} className="shrink-0 text-[11px] text-indigo-600 hover:text-indigo-500 font-medium">
              {expanded ? "less" : "more"}
            </button>
          )}
        </div>
      </div>
    );
  }

  if (ev.type === "error") {
    return (
      <div className="border-b border-slate-100 px-4 py-2 bg-red-50/30">
        <div className="flex items-start gap-2.5">
          <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-red-50 text-[10px] font-bold text-red-500">!</span>
          <pre className="min-w-0 flex-1 text-[11px] leading-relaxed text-red-600 font-mono whitespace-pre-wrap">{ev.content}</pre>
        </div>
      </div>
    );
  }

  if (ev.type === "text") {
    return (
      <div className="border-b border-slate-100 px-4 py-2">
        <pre className={`text-[11px] leading-relaxed text-slate-500 font-mono whitespace-pre-wrap ${!expanded && isLong ? "line-clamp-4" : ""}`}>{ev.content}</pre>
        {isLong && (
          <button onClick={() => setExpanded(!expanded)} className="mt-1 text-[11px] text-indigo-600 hover:text-indigo-500 font-medium">
            {expanded ? "less" : "more"}
          </button>
        )}
      </div>
    );
  }

  // meta
  return (
    <div className="border-b border-slate-100 px-4 py-1.5">
      <span className="text-[10px] text-slate-400 font-mono">{ev.content}</span>
    </div>
  );
}

/* ─── Compare View ────────────────────────────────────────── */

function CompareView({ data, a, b, setA, setB }: {
  data: BenchmarkData; a: string; b: string; setA: (s: string) => void; setB: (s: string) => void;
}) {
  const opts = data.runs.map((r) => r.name);
  useEffect(() => {
    if (!a && opts.length >= 1) setA(opts[0]);
    if (!b && opts.length >= 2) setB(opts[1]);
  }, [a, b, opts, setA, setB]);
  const runA = data.runs.find((r) => r.name === a);
  const runB = data.runs.find((r) => r.name === b);

  if (!runA || !runB) {
    return (
      <div className="mx-auto max-w-3xl space-y-6 animate-in">
        <h2 className="font-mono text-2xl font-bold text-slate-900">Compare Runs</h2>
        <div className="flex gap-4">
          <PickSelect label="Baseline (A)" value={a} options={opts} onChange={setA} />
          <PickSelect label="Candidate (B)" value={b} options={opts} onChange={setB} />
        </div>
        <div className="text-sm text-slate-400">Select two runs to compare.</div>
      </div>
    );
  }

  const sa = runA.scores, sb = runB.scores;
  const diff = runA.config && runB.config ? computeDiff(runA.config, runB.config) : [];

  const tasksA = new Map(runA.tasks.map((t) => [t.task_name, t]));
  const tasksB = new Map(runB.tasks.map((t) => [t.task_name, t]));
  const allNames = [...new Set([...tasksA.keys(), ...tasksB.keys()])].sort();
  const gained: string[] = [], lost: string[] = [], bothPass: string[] = [], bothFail: string[] = [];
  for (const name of allNames) {
    const pa = tasksA.get(name)?.passed ?? false;
    const pb = tasksB.get(name)?.passed ?? false;
    if (!pa && pb) gained.push(name);
    else if (pa && !pb) lost.push(name);
    else if (pa && pb) bothPass.push(name);
    else bothFail.push(name);
  }

  return (
    <div className="mx-auto max-w-4xl space-y-8 animate-in">
      <div className="flex items-end gap-4 flex-wrap">
        <h2 className="font-mono text-2xl font-bold text-slate-900">Compare</h2>
        <PickSelect label="Baseline (A)" value={a} options={opts} onChange={setA} />
        <PickSelect label="Candidate (B)" value={b} options={opts} onChange={setB} />
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        <CmpCard label="Mean Reward" va={sa.mean_reward} vb={sb.mean_reward} fmt={pctFmt} />
        <CmpCard label="Pass Rate" va={sa.pass_rate} vb={sb.pass_rate} fmt={pctFmt} />
        <CmpCard label="Cost" va={sa.total_cost_usd} vb={sb.total_cost_usd} fmt={usdFmt} />
      </div>

      <HarnessDiffPanel filesA={runA.harnessFiles} filesB={runB.harnessFiles} />

      <div>
        <h3 className="mb-3 text-sm font-semibold text-slate-700">
          Task Flips <span className="ml-1 font-normal text-slate-400">{gained.length} gained, {lost.length} lost</span>
        </h3>
        {gained.length > 0 && (
          <div className="mb-4">
            <div className="mb-2 flex items-center gap-2">
              <span className="rounded-full bg-emerald-50 px-2.5 py-0.5 text-[11px] font-semibold text-emerald-700">GAINED</span>
              <span className="text-xs text-slate-400">{gained.length} tasks now pass</span>
            </div>
            <div className="flex flex-wrap gap-1.5">{gained.map((n) => <span key={n} className="rounded-md bg-emerald-50 px-2 py-0.5 font-mono text-[11px] text-emerald-700">{n}</span>)}</div>
          </div>
        )}
        {lost.length > 0 && (
          <div className="mb-4">
            <div className="mb-2 flex items-center gap-2">
              <span className="rounded-full bg-red-50 px-2.5 py-0.5 text-[11px] font-semibold text-red-600">LOST</span>
              <span className="text-xs text-slate-400">{lost.length} tasks now fail</span>
            </div>
            <div className="flex flex-wrap gap-1.5">{lost.map((n) => <span key={n} className="rounded-md bg-red-50 px-2 py-0.5 font-mono text-[11px] text-red-600">{n}</span>)}</div>
          </div>
        )}
        <div className="text-xs text-slate-400">Both pass: {bothPass.length} &middot; Both fail: {bothFail.length}</div>
      </div>
    </div>
  );
}

function PickSelect({ label, value, options, onChange }: {
  label: string; value: string; options: string[]; onChange: (v: string) => void;
}) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-[10px] font-semibold uppercase tracking-widest text-slate-400">{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)}
        className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 font-mono text-sm text-slate-700 shadow-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-400">
        {options.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    </div>
  );
}

function CmpCard({ label, va, vb, fmt }: { label: string; va: number | null; vb: number | null; fmt: (n: number | null) => string }) {
  const diff = va != null && vb != null ? vb - va : null;
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="text-[10px] font-semibold uppercase tracking-widest text-slate-400 mb-2">{label}</div>
      <div className="flex items-baseline gap-2 font-mono">
        <span className="text-lg font-bold text-slate-800">{fmt(va)}</span>
        <span className="text-slate-300">&rarr;</span>
        <span className="text-lg font-bold text-slate-800">{fmt(vb)}</span>
        {diff != null && (
          <span className={`text-sm font-semibold ${diff > 0 ? "text-emerald-600" : diff < 0 ? "text-red-500" : "text-slate-400"}`}>
            {diff >= 0 ? "+" : ""}{fmt(diff)}
          </span>
        )}
      </div>
    </div>
  );
}

/* ─── Loading ─────────────────────────────────────────────── */

function Loading() {
  return (
    <div className="flex h-screen items-center justify-center bg-surface-2">
      <div className="flex flex-col items-center gap-3">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-indigo-500 border-t-transparent" />
        <span className="text-sm text-slate-400">Loading&hellip;</span>
      </div>
    </div>
  );
}

/* ─── Harness Diff Panel (Compare view) ──────────────────── */

function HarnessDiffPanel({ filesA, filesB }: { filesA: HarnessFile[]; filesB: HarnessFile[] }) {
  const [openPath, setOpenPath] = useState<string | null>(null);

  const allPaths = useMemo(() => {
    const set = new Set<string>();
    filesA.forEach((f) => set.add(f.path));
    filesB.forEach((f) => set.add(f.path));
    return [...set].sort();
  }, [filesA, filesB]);

  const mapA = useMemo(() => new Map(filesA.map((f) => [f.path, f])), [filesA]);
  const mapB = useMemo(() => new Map(filesB.map((f) => [f.path, f])), [filesB]);

  if (allPaths.length === 0) return null;

  return (
    <div>
      <h3 className="mb-3 text-sm font-semibold text-slate-700">Harness Diff by Component</h3>
      <div className="space-y-2">
        {allPaths.map((p) => {
          const fA = mapA.get(p);
          const fB = mapB.get(p);
          const cat = (fB || fA)!.category;
          const meta = CATEGORY_META[cat];
          const isNew = !fA && fB;
          const isRemoved = fA && !fB;
          const isChanged = fA && fB && fA.content !== fB.content;
          const isUnchanged = fA && fB && fA.content === fB.content;
          const isOpen = openPath === p;

          const diff = isChanged ? computeDiff(fA!.content, fB!.content) : [];

          return (
            <div key={p} className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
              <button onClick={() => setOpenPath(isOpen ? null : p)}
                className="flex w-full items-center gap-3 px-4 py-2.5 text-left hover:bg-slate-50/60 transition-colors">
                <span className={`flex h-5 w-5 shrink-0 items-center justify-center rounded text-[9px] font-bold ${meta.color} ${meta.bg}`}>{meta.icon}</span>
                <span className="font-mono text-[12px] text-slate-700">{p}</span>
                {isNew && <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-semibold text-emerald-700">NEW</span>}
                {isRemoved && <span className="rounded-full bg-red-50 px-2 py-0.5 text-[10px] font-semibold text-red-600">REMOVED</span>}
                {isChanged && <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[10px] font-semibold text-amber-600">CHANGED</span>}
                {isUnchanged && <span className="text-[10px] text-slate-400">unchanged</span>}
                <svg className={`ml-auto h-3 w-3 text-slate-400 transition-transform duration-150 ${isOpen ? "rotate-90" : ""}`} fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M7.21 14.77a.75.75 0 01.02-1.06L11.168 10 7.23 6.29a.75.75 0 111.04-1.08l4.5 4.25a.75.75 0 010 1.08l-4.5 4.25a.75.75 0 01-1.06-.02z" clipRule="evenodd" />
                </svg>
              </button>
              {isOpen && (
                <div className="border-t border-slate-100 max-h-[400px] overflow-auto">
                  {isNew && <pre className="px-4 py-3 text-[12px] leading-relaxed text-emerald-700 bg-emerald-50/30 font-mono whitespace-pre-wrap">{fB!.content}</pre>}
                  {isRemoved && <pre className="px-4 py-3 text-[12px] leading-relaxed text-red-600 bg-red-50/30 font-mono whitespace-pre-wrap">{fA!.content}</pre>}
                  {isChanged && diff.map((d, i) => (
                    <div key={i} className={`px-4 py-0.5 font-mono text-[12px] leading-relaxed whitespace-pre-wrap ${
                      d.type === "add" ? "bg-emerald-50/60 text-emerald-700" : d.type === "del" ? "bg-red-50/60 text-red-600" : "text-slate-400"
                    }`}>
                      {d.type === "add" ? "+" : d.type === "del" ? "-" : " "} {d.text}
                    </div>
                  ))}
                  {isUnchanged && <pre className="px-4 py-3 text-[12px] leading-relaxed text-slate-500 font-mono whitespace-pre-wrap">{fA!.content}</pre>}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ─── Harness Panel ──────────────────────────────────────── */

const CATEGORY_META: Record<HarnessFile["category"], { label: string; icon: string; color: string; bg: string }> = {
  system_prompt: { label: "System Prompt", icon: "S", color: "text-indigo-600", bg: "bg-indigo-50" },
  hooks:         { label: "Hooks",         icon: "H", color: "text-amber-600",  bg: "bg-amber-50" },
  config:        { label: "Config",        icon: "C", color: "text-slate-600",  bg: "bg-slate-100" },
  skills:        { label: "Skills",        icon: "K", color: "text-violet-600", bg: "bg-violet-50" },
  agents:        { label: "Agents",        icon: "A", color: "text-cyan-600",   bg: "bg-cyan-50" },
  rules:         { label: "Rules",         icon: "R", color: "text-emerald-600",bg: "bg-emerald-50" },
  other:         { label: "Other",         icon: "?", color: "text-slate-500",  bg: "bg-slate-50" },
};

function HarnessPanel({ files }: { files: HarnessFile[] }) {
  const [openCat, setOpenCat] = useState<string | null>("system_prompt");

  const grouped = useMemo(() => {
    const map = new Map<HarnessFile["category"], HarnessFile[]>();
    for (const f of files) {
      const list = map.get(f.category) || [];
      list.push(f);
      map.set(f.category, list);
    }
    return map;
  }, [files]);

  const order: HarnessFile["category"][] = ["system_prompt", "hooks", "skills", "agents", "config", "rules", "other"];

  return (
    <div className="mt-3 space-y-2 animate-in">
      {order.filter((cat) => grouped.has(cat)).map((cat) => {
        const meta = CATEGORY_META[cat];
        const catFiles = grouped.get(cat)!;
        const isOpen = openCat === cat;
        return (
          <div key={cat} className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
            <button onClick={() => setOpenCat(isOpen ? null : cat)}
              className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-slate-50/60 transition-colors">
              <span className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-[10px] font-bold ${meta.color} ${meta.bg}`}>{meta.icon}</span>
              <span className="text-sm font-semibold text-slate-700">{meta.label}</span>
              <span className="text-xs text-slate-400">{catFiles.length} {catFiles.length === 1 ? "file" : "files"}</span>
              <svg className={`ml-auto h-3 w-3 text-slate-400 transition-transform duration-150 ${isOpen ? "rotate-90" : ""}`} fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M7.21 14.77a.75.75 0 01.02-1.06L11.168 10 7.23 6.29a.75.75 0 111.04-1.08l4.5 4.25a.75.75 0 010 1.08l-4.5 4.25a.75.75 0 01-1.06-.02z" clipRule="evenodd" />
              </svg>
            </button>
            {isOpen && (
              <div className="border-t border-slate-100">
                {catFiles.map((f) => (
                  <div key={f.path} className="border-b border-slate-50 last:border-b-0">
                    <div className="flex items-center gap-2 bg-slate-50/60 px-4 py-1.5">
                      <span className="font-mono text-[11px] text-slate-500">{f.path}</span>
                      <span className="text-[10px] text-slate-400">{f.content.split("\n").length} lines</span>
                    </div>
                    <pre className="max-h-72 overflow-auto px-4 py-3 text-[12px] leading-relaxed text-slate-600 font-mono whitespace-pre-wrap">{f.content}</pre>
                  </div>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

/* ─── Utilities ───────────────────────────────────────────── */

function genBadge(bench: BenchmarkData, name: string): { text: string; cls: string } | null {
  const h = bench.history?.iterations?.find((x) => x.name === name);
  if (!h || h.holdout_reward == null || h.reward == null) return null;
  const delta = h.reward - h.holdout_reward;
  if (delta > 0.15) return { text: "OVERFIT", cls: "bg-red-50 text-red-600" };
  if (delta > 0.08) return { text: "RISK", cls: "bg-amber-50 text-amber-600" };
  return { text: "GEN", cls: "bg-emerald-50 text-emerald-700" };
}

function toggleCompare(name: string, a: string, b: string, setA: (s: string) => void, setB: (s: string) => void): void {
  if (a === name) { setA(b); setB(""); }
  else if (b === name) { setB(""); }
  else if (!a) setA(name);
  else setB(name);
}

type TaskSortValue = string | number | boolean | null;

function getTaskSortValue(task: TaskResult, col: string): TaskSortValue {
  switch (col) {
    case "task_name":
      return task.task_name;
    case "passed":
      return task.passed;
    case "reward":
      return task.reward;
    case "cost_usd":
      return task.cost_usd;
    case "num_turns":
      return task.num_turns;
    default:
      return null;
  }
}

function sortTasks(tasks: TaskResult[], col: string, dir: number): TaskResult[] {
  return [...tasks].sort((a, b) => {
    const va = getTaskSortValue(a, col);
    const vb = getTaskSortValue(b, col);
    if (va == null && vb == null) return 0;
    if (va == null) return 1;
    if (vb == null) return -1;
    if (typeof va === "boolean" && typeof vb === "boolean") {
      return ((va ? 1 : 0) - (vb ? 1 : 0)) * dir;
    }
    if (typeof va === "string" && typeof vb === "string") {
      return va.localeCompare(vb) * dir;
    }
    if (typeof va === "number" && typeof vb === "number") {
      return (va - vb) * dir;
    }
    return 0;
  });
}

function quickDiffStat(a: string, b: string): { add: number; del: number } {
  const la = new Set(a.split("\n")), lb = new Set(b.split("\n"));
  let add = 0, del = 0;
  lb.forEach((l) => { if (!la.has(l)) add++; });
  la.forEach((l) => { if (!lb.has(l)) del++; });
  return { add, del };
}

interface DiffLine { type: "ctx" | "add" | "del"; text: string }

function computeDiff(textA: string, textB: string): DiffLine[] {
  const a = textA.split("\n"), b = textB.split("\n");
  const m = a.length, n = b.length;
  const dp: number[][] = Array(m + 1).fill(null).map(() => Array(n + 1).fill(0));
  for (let i = 1; i <= m; i++)
    for (let j = 1; j <= n; j++)
      dp[i][j] = a[i - 1] === b[j - 1] ? dp[i - 1][j - 1] + 1 : Math.max(dp[i - 1][j], dp[i][j - 1]);
  const result: DiffLine[] = [];
  let i = m, j = n;
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && a[i - 1] === b[j - 1]) { result.unshift({ type: "ctx", text: a[i - 1] }); i--; j--; }
    else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) { result.unshift({ type: "add", text: b[j - 1] }); j--; }
    else { result.unshift({ type: "del", text: a[i - 1] }); i--; }
  }
  return result;
}

function dedupeIterations(iters: HistoryEntry[]): HistoryEntry[] {
  const seen = new Map<string, number>();
  for (let i = iters.length - 1; i >= 0; i--) {
    if (!seen.has(iters[i].name)) seen.set(iters[i].name, i);
  }
  return iters.filter((_, i) => [...seen.values()].includes(i));
}

function pctFmt(v: number | null): string { return v != null ? `${(v * 100).toFixed(1)}%` : "N/A"; }
function usdFmt(v: number | null): string { return v != null ? `$${v.toFixed(2)}` : "N/A"; }
