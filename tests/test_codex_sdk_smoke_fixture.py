from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from meta_agent.benchmark import load_benchmark
from meta_agent.task_runner import _copy_harness_files

REPO_ROOT = Path(__file__).resolve().parents[1]
SMOKE_BENCHMARK = REPO_ROOT / "benchmarks" / "example" / "benchmark_codex_sdk_smoke.yaml"
CODEX_CONFIG_DIR = REPO_ROOT / "configs" / "codex_vanilla"


class CodexSdkSmokeFixtureTest(unittest.TestCase):
    def test_smoke_benchmark_loads_with_codex_sdk_runtime(self) -> None:
        benchmark = load_benchmark(str(SMOKE_BENCHMARK))

        self.assertEqual(benchmark.harness, "codex")
        self.assertEqual(benchmark.runtime, "codex_sdk")
        self.assertEqual(
            [task.name for task in benchmark.tasks],
            ["fix-fibonacci", "add-tests"],
        )

    def test_codex_vanilla_config_contains_agents_file(self) -> None:
        self.assertTrue((CODEX_CONFIG_DIR / "AGENTS.md").is_file())

    def test_codex_vanilla_config_can_be_copied_as_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            work_dir = Path(tmp_dir)
            _copy_harness_files(str(CODEX_CONFIG_DIR), work_dir)

            self.assertTrue((work_dir / "AGENTS.md").is_file())
            self.assertTrue((work_dir / ".codex" / "config.toml").is_file())
