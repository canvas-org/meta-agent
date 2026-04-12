from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class TestNoLegacyCodexSdkRefs(unittest.TestCase):
    def test_legacy_codex_sdk_dir_removed(self) -> None:
        self.assertFalse((REPO_ROOT / "meta_agent" / "codex_sdk").exists())

    def test_task_runner_has_no_node_wrapper_symbols(self) -> None:
        task_runner = (REPO_ROOT / "meta_agent" / "task_runner.py").read_text()
        self.assertNotIn("_CODEX_SDK_DIR", task_runner)
        self.assertNotIn("_CODEX_SDK_RUNNER", task_runner)
        self.assertNotIn("_build_codex_sdk_cmd", task_runner)
        self.assertNotIn("_ensure_codex_sdk_ready", task_runner)
        self.assertNotIn("_run_codex_sdk_turn(", task_runner)
        self.assertNotIn("\ndef run_codex_sdk_with_hooks(", task_runner)

    def test_readme_has_no_deleted_wrapper_install_instructions(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text()
        self.assertNotIn("cd meta_agent/codex_sdk && npm install", readme)
        self.assertNotIn("Node.js 18+ (only if using `runtime: codex_sdk`)", readme)
