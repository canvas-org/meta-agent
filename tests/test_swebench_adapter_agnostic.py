"""Tests that the SWEBench-M adapter uses the unified dispatch."""
from __future__ import annotations

import inspect
import unittest


class TestSwebenchAdapterImports(unittest.TestCase):
    def test_run_single_task_uses_run_agent(self) -> None:
        from benchmarks.swebench_m.adapter import run_single_task
        source = inspect.getsource(run_single_task)
        self.assertIn("run_agent", source)

    def test_run_single_task_no_direct_runner_imports(self) -> None:
        from benchmarks.swebench_m.adapter import run_single_task
        source = inspect.getsource(run_single_task)
        self.assertNotIn("run_codex_cli_with_hooks", source)
        self.assertNotIn("run_codex_sdk_with_hooks", source)

    def test_module_does_not_import_runtime_specific_runners(self) -> None:
        import benchmarks.swebench_m.adapter as mod
        source = inspect.getsource(mod)
        self.assertNotIn("run_codex_cli_with_hooks", source)
        self.assertNotIn("run_codex_sdk_with_hooks", source)
