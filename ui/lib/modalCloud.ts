import { execFile } from "child_process";
import fs from "fs";
import path from "path";
import { promisify } from "util";
import type { DashboardData } from "@/lib/types";

const execFileAsync = promisify(execFile);

const PROJECT_ROOT = path.resolve(process.cwd(), "..");
const MODAL_SOURCES_PATH = path.join(PROJECT_ROOT, "experience", ".modal_sources.json");
const MODAL_VOLUME = "harness-optimizer-runs-v1";
const MODAL_DOWNLOAD_MARKER = "✓ Finished downloading files to local!";

interface ModalBenchmarkSource {
  runId: string;
  updatedAt: string;
}

interface ModalSourceRegistry {
  benchmarks: Record<string, ModalBenchmarkSource>;
}

interface ModalIndexEnvelope {
  run_id: string;
  updated_at: string;
  benchmarks: DashboardData;
}

interface ModalRunState {
  status?: string;
  updated_at?: string;
}

function emptyRegistry(): ModalSourceRegistry {
  return { benchmarks: {} };
}

function trimModalDownloadMarker(raw: string): string {
  const markerIndex = raw.lastIndexOf(MODAL_DOWNLOAD_MARKER);
  if (markerIndex === -1) return raw.trim();
  return raw.slice(0, markerIndex).trim();
}

async function modalVolumeGetText(remotePath: string): Promise<string> {
  const { stdout } = await execFileAsync(
    "modal",
    ["volume", "get", "--force", MODAL_VOLUME, remotePath, "-"],
    {
      cwd: PROJECT_ROOT,
      env: process.env,
      maxBuffer: 50 * 1024 * 1024,
    }
  );
  return trimModalDownloadMarker(stdout);
}

export function readModalSourceRegistry(): ModalSourceRegistry {
  if (!fs.existsSync(MODAL_SOURCES_PATH)) return emptyRegistry();
  try {
    const parsed = JSON.parse(fs.readFileSync(MODAL_SOURCES_PATH, "utf-8")) as Partial<ModalSourceRegistry>;
    const benchmarks = parsed.benchmarks;
    if (!benchmarks || typeof benchmarks !== "object") return emptyRegistry();
    return { benchmarks };
  } catch {
    return emptyRegistry();
  }
}

export function getModalRunIdForBenchmark(benchmark: string): string | null {
  const entry = readModalSourceRegistry().benchmarks[benchmark];
  if (!entry || typeof entry.runId !== "string" || entry.runId.length === 0) return null;
  return entry.runId;
}

export async function fetchModalIndexByRunId(runId: string): Promise<DashboardData | null> {
  try {
    const raw = await modalVolumeGetText(`/runs/${runId}/artifacts/index.json`);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<ModalIndexEnvelope>;
    if (!parsed.benchmarks || typeof parsed.benchmarks !== "object") return null;
    return parsed.benchmarks;
  } catch {
    return null;
  }
}

export async function fetchModalRunStateByRunId(runId: string): Promise<ModalRunState | null> {
  try {
    const raw = await modalVolumeGetText(`/runs/${runId}/state.json`);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as ModalRunState;
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch {
    return null;
  }
}

async function modalVolumeGetToFile(
  remotePath: string,
  localPath: string,
): Promise<boolean> {
  try {
    const raw = await modalVolumeGetText(remotePath);
    if (!raw) return false;
    fs.mkdirSync(path.dirname(localPath), { recursive: true });
    fs.writeFileSync(localPath, raw, "utf-8");
    return true;
  } catch {
    return false;
  }
}

async function modalVolumeLs(remotePath: string): Promise<string[]> {
  try {
    const { stdout } = await execFileAsync(
      "modal",
      ["volume", "ls", MODAL_VOLUME, remotePath],
      { cwd: PROJECT_ROOT, env: process.env, maxBuffer: 5 * 1024 * 1024 },
    );
    return stdout
      .trim()
      .split("\n")
      .map((l) => l.trim())
      .filter(Boolean);
  } catch {
    return [];
  }
}

async function syncRemoteDir(
  remoteDirPath: string,
  localDirPath: string,
): Promise<void> {
  const entries = await modalVolumeLs(`${remoteDirPath}/`);
  if (entries.length === 0) return;

  for (const entry of entries) {
    const name = entry.split("/").filter(Boolean).pop()!;
    if (name.includes(".")) {
      await modalVolumeGetToFile(
        `${remoteDirPath}/${name}`,
        path.join(localDirPath, name),
      );
    } else {
      await syncRemoteDir(
        `${remoteDirPath}/${name}`,
        path.join(localDirPath, name),
      );
    }
  }
}

export async function syncModalToLocal(
  runId: string,
  benchmarkName: string,
): Promise<void> {
  const EXPERIENCE_ROOT = path.join(PROJECT_ROOT, "experience");
  const remoteBase = `/runs/${runId}/experience/${benchmarkName}`;
  const localBase = path.join(EXPERIENCE_ROOT, benchmarkName);

  await modalVolumeGetToFile(
    `${remoteBase}/history.json`,
    path.join(localBase, "history.json"),
  );

  const candidateLines = await modalVolumeLs(`${remoteBase}/candidates/`);
  const candidates = candidateLines.map((l) => l.split("/").filter(Boolean).pop()!);

  const syncs = candidates.map(async (cand) => {
    const localCandDir = path.join(localBase, "candidates", cand);
    const remoteCandidate = `${remoteBase}/candidates/${cand}`;

    const hasScores = fs.existsSync(path.join(localCandDir, "scores.json"));
    if (!hasScores) {
      await modalVolumeGetToFile(
        `${remoteCandidate}/scores.json`,
        path.join(localCandDir, "scores.json"),
      );
    }

    const hasAgentsMd = fs.existsSync(path.join(localCandDir, "AGENTS.md"));
    if (!hasAgentsMd) {
      await modalVolumeGetToFile(
        `${remoteCandidate}/AGENTS.md`,
        path.join(localCandDir, "AGENTS.md"),
      );
    }

    const codexDir = path.join(localCandDir, ".codex");
    const hasHarnessFiles =
      fs.existsSync(codexDir) &&
      fs.statSync(codexDir).isDirectory() &&
      fs.readdirSync(codexDir).length > 0;
    if (!hasHarnessFiles) {
      await syncRemoteDir(
        `${remoteCandidate}/.codex`,
        path.join(localCandDir, ".codex"),
      );
    }

    const localPerTask = path.join(localCandDir, "per_task");
    const hasPerTask =
      fs.existsSync(localPerTask) &&
      fs.readdirSync(localPerTask).some((f) => f.endsWith(".json"));
    if (!hasPerTask) {
      const remotePerTaskFiles = await modalVolumeLs(
        `${remoteCandidate}/per_task/`,
      );
      const jsonFiles = remotePerTaskFiles
        .map((line) => line.split("/").pop()!)
        .filter((f) => f.endsWith(".json") && !f.endsWith("_agent_result.json"));

      const taskSyncs = jsonFiles.map((f) =>
        modalVolumeGetToFile(
          `${remoteCandidate}/per_task/${f}`,
          path.join(localPerTask, f),
        ),
      );
      await Promise.all(taskSyncs);
    }
  });

  await Promise.all(syncs);
}

export async function fetchModalTraceTextByRunId(
  runId: string,
  benchmark: string,
  candidate: string,
  task: string
): Promise<string | null> {
  try {
    const remotePath =
      `/runs/${runId}/experience/${benchmark}/candidates/${candidate}/per_task/${task}_trace.jsonl`;
    const raw = await modalVolumeGetText(remotePath);
    return raw.trim() ? raw : null;
  } catch {
    return null;
  }
}
