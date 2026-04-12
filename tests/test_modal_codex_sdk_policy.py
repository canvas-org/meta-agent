"""Tests for the Modal codex_sdk fail-fast policy (Option B)."""
from __future__ import annotations

import unittest


class TestModalCodexSdkFailsFast(unittest.TestCase):
    def test_codex_sdk_fails_with_clear_message(self) -> None:
        from benchmarks.artifacts_bench.modal_runner import _run_single_task

        task_data = {
            "index": 999,
            "question": "test",
            "checklist": [],
            "task_class": "test",
            "difficulty": "easy",
        }
        result = _run_single_task(
            task_data=task_data,
            config_agents_md="",
            config_claude_md="",
            config_codex_dir_files=None,
            model="gpt-5.4",
            runtime="codex_sdk",
            timeout=30,
        )
        self.assertFalse(result["passed"])
        self.assertIn("Python SDK runner", result["verify_output"])

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
