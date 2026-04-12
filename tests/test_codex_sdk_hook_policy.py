"""Tests for Codex SDK hook emulation policy in task_runner.py."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from meta_agent.task_runner import (
    _CODEX_HOOK_EVENTS_UNSUPPORTED,
    _hook_group_matches,
    _load_codex_hooks_config,
    _run_codex_hook_event,
)


class TestLoadCodexHooksConfig(unittest.TestCase):
    def test_loads_valid_hooks_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            codex_dir = work / ".codex"
            codex_dir.mkdir()
            hooks = {
                "hooks": {
                    "SessionStart": [{"matcher": "", "hooks": [{"type": "command", "command": "echo start"}]}],
                    "Stop": [{"matcher": "", "hooks": [{"type": "command", "command": "echo stop"}]}],
                }
            }
            (codex_dir / "hooks.json").write_text(json.dumps(hooks))
            config = _load_codex_hooks_config(work)
            self.assertIn("SessionStart", config)
            self.assertIn("Stop", config)

    def test_returns_empty_when_no_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _load_codex_hooks_config(Path(tmp))
            self.assertEqual(config, {})

    def test_returns_empty_on_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            codex_dir = work / ".codex"
            codex_dir.mkdir()
            (codex_dir / "hooks.json").write_text("not json")
            config = _load_codex_hooks_config(work)
            self.assertEqual(config, {})


class TestHookGroupMatches(unittest.TestCase):
    def test_empty_matcher_matches(self) -> None:
        self.assertTrue(_hook_group_matches("SessionStart", {"matcher": ""}, {}))

    def test_wildcard_matcher_matches(self) -> None:
        self.assertTrue(_hook_group_matches("SessionStart", {"matcher": "*"}, {}))

    def test_no_matcher_key_matches(self) -> None:
        self.assertTrue(_hook_group_matches("SessionStart", {}, {}))

    def test_regex_matcher_on_session_start(self) -> None:
        self.assertTrue(
            _hook_group_matches("SessionStart", {"matcher": "start"}, {"source": "startup"})
        )
        self.assertFalse(
            _hook_group_matches("SessionStart", {"matcher": "^nope$"}, {"source": "startup"})
        )


class TestRunCodexHookEvent(unittest.TestCase):
    def test_runs_echo_command(self) -> None:
        hooks_config = {
            "SessionStart": [{
                "matcher": "",
                "hooks": [{"type": "command", "command": "echo ok"}],
            }],
        }
        with tempfile.TemporaryDirectory() as tmp:
            failures = _run_codex_hook_event(
                hooks_config, "SessionStart", Path(tmp), "gpt-5.4", {},
            )
        self.assertEqual(failures, [])

    def test_failing_command_reports_failure(self) -> None:
        hooks_config = {
            "SessionStart": [{
                "matcher": "",
                "hooks": [{"type": "command", "command": "exit 1"}],
            }],
        }
        with tempfile.TemporaryDirectory() as tmp:
            failures = _run_codex_hook_event(
                hooks_config, "SessionStart", Path(tmp), "gpt-5.4", {},
            )
        self.assertEqual(len(failures), 1)
        self.assertIn("failed", failures[0])

    def test_no_handlers_returns_empty(self) -> None:
        failures = _run_codex_hook_event({}, "SessionStart", Path("/tmp"), "m", {})
        self.assertEqual(failures, [])


class TestUnsupportedHookWarning(unittest.TestCase):
    """Verify that PreToolUse/PostToolUse generate warnings."""

    @patch("meta_agent.task_runner._codex_native_hooks_supported", return_value=False)
    @patch("meta_agent.codex_sdk_runner.run_codex_sdk_turn")
    def test_unsupported_events_produce_warnings(
        self, mock_turn: object, _mock_native: object,
    ) -> None:
        from meta_agent.codex_sdk_runner import CodexSdkRunResult
        from meta_agent.task_runner import _run_codex_sdk_with_hooks_native

        mock_turn.return_value = CodexSdkRunResult(  # type: ignore[union-attr]
            final_response="done", exit_code=0,
        )

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            codex_dir = work / ".codex"
            codex_dir.mkdir()
            hooks = {
                "hooks": {
                    "PreToolUse": [{"matcher": "", "hooks": []}],
                    "PostToolUse": [{"matcher": "", "hooks": []}],
                    "SessionStart": [{"matcher": "", "hooks": []}],
                }
            }
            (codex_dir / "hooks.json").write_text(json.dumps(hooks))

            result = _run_codex_sdk_with_hooks_native(
                prompt="test", model="m", work_dir=work, timeout=30,
            )

        self.assertEqual(len(result.hook_warnings), 1)
        warning = result.hook_warnings[0]
        self.assertIn("PostToolUse", warning)
        self.assertIn("PreToolUse", warning)

    def test_unsupported_event_names_are_defined(self) -> None:
        self.assertIn("PreToolUse", _CODEX_HOOK_EVENTS_UNSUPPORTED)
        self.assertIn("PostToolUse", _CODEX_HOOK_EVENTS_UNSUPPORTED)
