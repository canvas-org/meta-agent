export interface DimensionReview {
  score: number;
  title: string;
  review: string;
}

export interface JudgeFeedback {
  dimensions: DimensionReview[];
  overall_score: number;
}

export interface TaskResult {
  task_name: string;
  short_name: string;
  reward: number | null;
  passed: boolean;
  cost_usd: number | null;
  num_turns: number | null;
  duration_ms: number | null;
  wall_time_s: number;
  input_tokens: number | null;
  output_tokens: number | null;
  judge?: JudgeFeedback;
  judge_raw?: string;
}

export interface RunScores {
  name: string;
  model: string;
  n_tasks: number;
  n_passed: number;
  pass_rate: number;
  mean_reward: number | null;
  total_cost_usd: number | null;
  median_turns: number | null;
  tasks_passed: string[];
  tasks_failed: string[];
}

export interface HarnessFile {
  path: string;
  content: string;
  category: "system_prompt" | "hooks" | "config" | "skills" | "agents" | "rules" | "other";
}

export interface Run {
  name: string;
  scores: RunScores;
  config: string | null;
  harnessFiles: HarnessFile[];
  tasks: TaskResult[];
}

export interface HistoryEntry {
  name: string;
  reward: number;
  pass_rate: number;
  n_passed: number;
  n_tasks: number;
  cost_usd: number;
  timestamp: string;
  holdout_reward?: number;
  holdout_cost?: number;
}

export interface ExperimentConfig {
  description?: string | null;
  harness?: string;
  runtime?: string;
  bench_type?: string;
  n_search_tasks?: number;
  n_total_tasks?: number;
  batch_size?: number | null;
  seed?: number | null;
  holdout_benchmark?: string | null;
  proposer_model?: string;
  proposer_cli?: string;
  max_iterations?: number;
  concurrency?: number;
  fast?: boolean;
  timeout?: number | null;
}

export interface BenchmarkData {
  runs: Run[];
  history: {
    benchmark: string;
    model: string;
    config?: ExperimentConfig;
    iterations: HistoryEntry[];
  } | null;
}

export type DashboardData = Record<string, BenchmarkData>;
