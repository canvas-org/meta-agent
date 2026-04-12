"""Tests for the Modal codex_sdk fail-fast policy (Option B)."""
from __future__ import annotations

import unittest


class TestModalCodexSdkFailsFast(unittest.TestCase):
    def test_codex_sdk_single_task_raises_with_clear_message(self) -> None:
        from benchmarks.artifacts_bench.modal_runner import _run_single_task

        task_data = {
            "index": 999,
            "question": "test",
            "checklist": [],
            "task_class": "test",
            "difficulty": "easy",
        }
        with self.assertRaises(NotImplementedError) as ctx:
            _run_single_task(
                task_data=task_data,
                config_agents_md="",
                config_claude_md="",
                config_codex_dir_files=None,
                model="gpt-5.4",
                runtime="codex_sdk",
                timeout=30,
            )
        self.assertIn("Python SDK runner", str(ctx.exception))

    def test_modal_sync_preflight_raises_before_fanout(self) -> None:
        from benchmarks.artifacts_bench.adapter import _run_artifacts_tasks_modal_sync
        from meta_agent.benchmark import ArtifactsBenchBackend, Benchmark

        benchmark = Benchmark(
            name="artifacts-modal-test",
            type="artifacts_bench",
            harness="codex",
            runtime="codex_sdk",
            artifacts_backend=ArtifactsBenchBackend(dataset_path="missing.parquet"),
        )

        with self.assertRaises(NotImplementedError) as ctx:
            _run_artifacts_tasks_modal_sync(
                benchmark=benchmark,
                config_path="configs/codex_vanilla",
                model="gpt-5.4",
                concurrency=1,
                runtime="codex_sdk",
            )
        self.assertIn("Python SDK runner", str(ctx.exception))

    def test_codex_cli_branch_not_affected(self) -> None:
        """The codex_cli branch should not raise NotImplementedError."""
        import inspect
        from benchmarks.artifacts_bench.modal_runner import _run_single_task
        source = inspect.getsource(_run_single_task)
        self.assertIn('runtime == "codex_cli"', source)


class TestModalSdkFunctionsRemoved(unittest.TestCase):
    def test_no_sdk_turn_function(self) -> None:
        import benchmarks.artifacts_bench.modal_runner as mod
        self.assertFalse(hasattr(mod, "_run_codex_sdk_turn_modal"))

    def test_no_sdk_with_hooks_function(self) -> None:
        import benchmarks.artifacts_bench.modal_runner as mod
        self.assertFalse(hasattr(mod, "_run_codex_sdk_with_hooks_modal"))

    def test_no_ensure_sdk_ready_function(self) -> None:
        import benchmarks.artifacts_bench.modal_runner as mod
        self.assertFalse(hasattr(mod, "_ensure_codex_sdk_ready_modal"))

    def test_no_resolve_sdk_dir_function(self) -> None:
        import benchmarks.artifacts_bench.modal_runner as mod
        self.assertFalse(hasattr(mod, "_resolve_codex_sdk_dir"))
