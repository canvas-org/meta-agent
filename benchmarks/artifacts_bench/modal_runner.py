"""Modal backend for ArtifactsBench — runs each task in an isolated cloud container.

First deploy:  modal deploy benchmarks/artifacts_bench/modal_runner.py

Then set ARTIFACTS_USE_MODAL=1 before running the outer loop or eval_runner.

Requires: modal secret create anthropic-key ANTHROPIC_API_KEY=sk-ant-...
          modal secret create openai-key OPENAI_API_KEY=sk-...
          modal secret create gemini-key GEMINI_API_KEY=AI...
"""
from __future__ import annotations

import re
from pathlib import Path

import modal

app = modal.App("artifacts-bench")
_CODEX_HOOK_EVENTS_UNSUPPORTED = {"PreToolUse", "PostToolUse"}


def get_remote_fn() -> modal.Function:
    """Get a handle to the deployed Modal function for calling from outside."""
    return modal.Function.from_name("artifacts-bench", "run_task")

_dockerfile = str(Path(__file__).parent / "Dockerfile.modal")
_file_path = Path(__file__).resolve()
_repo_root = _file_path.parents[2] if len(_file_path.parents) > 2 else _file_path.parent
image = (
    modal.Image.from_dockerfile(_dockerfile)
    .pip_install(
        "typing_extensions>=4.15",
        "claude-agent-sdk>=0.1.53",
        "pandas", "pyarrow", "google-genai", "pydantic",
    )
    .add_local_dir(
        str(_repo_root / "meta_agent"),
        remote_path="/opt/meta_agent",
        ignore=["**/__pycache__/**", "codex_sdk/node_modules/**"],
    )
)


def _extract_text_from_jsonl_modal(raw: str) -> str | None:
    import json as _json
    parts: list[str] = []
    for line in raw.strip().splitlines():
        try:
            event = _json.loads(line)
        except (ValueError, _json.JSONDecodeError):
            continue
        if event.get("type") == "message" and isinstance(event.get("content"), str):
            parts.append(event["content"])
    return "\n".join(parts) if parts else None


def _resolve_codex_sdk_dir() -> Path:
    candidates = [
        Path("/opt/meta_agent/codex_sdk"),
        _repo_root / "meta_agent" / "codex_sdk",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]


def _build_codex_exec_cmd_modal(prompt: str, model: str) -> list[str]:
    cmd = ["codex", "exec", "--full-auto", "--json", "--skip-git-repo-check"]
    if model:
        cmd.extend(["--model", model])
    cmd.append(prompt)
    return cmd


def _build_codex_sdk_cmd_modal(sdk_runner: Path) -> list[str]:
    return ["node", str(sdk_runner)]


def _ensure_codex_sdk_ready_modal() -> tuple[bool, str, Path]:
    import subprocess

    sdk_dir = _resolve_codex_sdk_dir()
    runner = sdk_dir / "run_codex_sdk.mjs"
    if not sdk_dir.is_dir():
        return False, f"Codex SDK directory missing at {sdk_dir}", runner
    if not runner.is_file():
        return False, f"Codex SDK runner missing at {runner}", runner

    sdk_pkg = sdk_dir / "node_modules" / "@openai" / "codex-sdk"
    if sdk_pkg.is_dir():
        return True, "", runner

    install = subprocess.run(
        ["npm", "install"],
        cwd=str(sdk_dir),
        capture_output=True,
        text=True,
        timeout=180,
    )
    if install.returncode != 0:
        detail = (install.stderr or "").strip() or (install.stdout or "").strip()
        return False, f"Failed to install @openai/codex-sdk: {detail}", runner
    if not sdk_pkg.is_dir():
        return False, f"npm install succeeded but package missing at {sdk_pkg}", runner
    return True, "", runner


def _load_codex_hooks_config_modal(work_dir: Path) -> dict:
    import json

    hooks_path = work_dir / ".codex" / "hooks.json"
    if not hooks_path.is_file():
        return {}
    try:
        payload = json.loads(hooks_path.read_text())
    except json.JSONDecodeError:
        return {}
    hooks = payload.get("hooks")
    return hooks if isinstance(hooks, dict) else {}


def _extract_last_agent_message_from_trace_modal(raw_trace: str) -> str | None:
    import json

    last_message: str | None = None
    for line in raw_trace.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "message":
            content = event.get("content")
            if isinstance(content, str) and content.strip():
                last_message = content
            continue
        if event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        if item.get("type") != "agent_message":
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            last_message = text
    return last_message


def _hook_group_matches_modal(event_name: str, group: dict, payload: dict) -> bool:
    matcher = group.get("matcher")
    if not isinstance(matcher, str) or matcher in {"", "*"}:
        return True

    if event_name == "SessionStart":
        target = str(payload.get("source", ""))
    elif event_name in {"PreToolUse", "PostToolUse"}:
        target = str(payload.get("tool_name", ""))
    else:
        return True

    try:
        return re.search(matcher, target) is not None
    except re.error:
        return False


def _run_codex_hook_event_modal(
    hooks_config: dict,
    event_name: str,
    work_dir: Path,
    model: str,
    payload: dict,
) -> list[str]:
    import json
    import subprocess
    import uuid

    groups = hooks_config.get(event_name)
    if not isinstance(groups, list):
        return []

    failures: list[str] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        if not _hook_group_matches_modal(event_name, group, payload):
            continue
        handlers = group.get("hooks")
        if not isinstance(handlers, list):
            continue
        for handler in handlers:
            if not isinstance(handler, dict):
                continue
            if handler.get("type", "command") != "command":
                continue
            command = handler.get("command")
            if not isinstance(command, str) or not command.strip():
                continue

            timeout_raw = handler.get("timeout", handler.get("timeoutSec", 600))
            try:
                timeout_sec = max(1, int(timeout_raw))
            except (TypeError, ValueError):
                timeout_sec = 600

            hook_input = {
                "session_id": payload.get("session_id", uuid.uuid4().hex),
                "transcript_path": str(work_dir / "trace.jsonl"),
                "cwd": str(work_dir),
                "hook_event_name": event_name,
                "model": model,
                **payload,
            }
            try:
                hook_result = subprocess.run(
                    command,
                    shell=True,
                    cwd=str(work_dir),
                    capture_output=True,
                    text=True,
                    input=json.dumps(hook_input),
                    timeout=timeout_sec,
                )
                if hook_result.returncode != 0:
                    detail = (hook_result.stderr or "").strip() or (hook_result.stdout or "").strip()
                    failures.append(
                        f"{event_name}: command `{command}` failed ({detail or f'exit={hook_result.returncode}'})"
                    )
            except subprocess.TimeoutExpired:
                failures.append(
                    f"{event_name}: command `{command}` timed out after {timeout_sec}s"
                )
    return failures


def _run_codex_cli_with_hooks_modal(
    prompt: str,
    model: str,
    work_dir: Path,
    timeout: int,
) -> tuple:
    import subprocess
    import uuid

    hooks_config = _load_codex_hooks_config_modal(work_dir)
    hook_failures: list[str] = []
    hook_warnings: list[str] = []

    unsupported = sorted(event for event in _CODEX_HOOK_EVENTS_UNSUPPORTED if event in hooks_config)
    if unsupported:
        hook_warnings.append(
            "Codex hook emulation does not support events: " + ", ".join(unsupported)
        )

    pre_payload = {"source": "startup", "prompt": prompt, "turn_id": uuid.uuid4().hex}
    hook_failures.extend(
        _run_codex_hook_event_modal(hooks_config, "SessionStart", work_dir, model, pre_payload)
    )
    hook_failures.extend(
        _run_codex_hook_event_modal(hooks_config, "UserPromptSubmit", work_dir, model, pre_payload)
    )
    if hook_failures:
        return (
            subprocess.CompletedProcess(
                args=_build_codex_exec_cmd_modal(prompt, model),
                returncode=1,
                stdout="",
                stderr="\n".join(hook_failures),
            ),
            hook_failures,
            hook_warnings,
        )

    result = subprocess.run(
        _build_codex_exec_cmd_modal(prompt, model),
        cwd=str(work_dir),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    stop_payload = {
        "turn_id": uuid.uuid4().hex,
        "stop_hook_active": False,
        "last_assistant_message": _extract_last_agent_message_from_trace_modal(result.stdout or ""),
    }
    hook_failures.extend(
        _run_codex_hook_event_modal(hooks_config, "Stop", work_dir, model, stop_payload)
    )
    return result, hook_failures, hook_warnings


def _run_codex_sdk_turn_modal(
    prompt: str,
    model: str,
    work_dir: Path,
    timeout: int,
) -> tuple:
    import json
    import subprocess

    ready, reason, runner = _ensure_codex_sdk_ready_modal()
    if not ready:
        return subprocess.CompletedProcess(
            args=_build_codex_sdk_cmd_modal(runner),
            returncode=1,
            stdout="",
            stderr=reason,
        )

    payload = {
        "prompt": prompt,
        "workingDirectory": str(work_dir),
        "timeoutSec": timeout,
        "skipGitRepoCheck": True,
    }
    if model:
        payload["model"] = model

    sdk_result = subprocess.run(
        _build_codex_sdk_cmd_modal(runner),
        cwd=str(work_dir),
        capture_output=True,
        text=True,
        input=json.dumps(payload),
        timeout=timeout + 30,
    )
    stdout_raw = (sdk_result.stdout or "").strip()
    try:
        sdk_payload = json.loads(stdout_raw) if stdout_raw else {}
    except json.JSONDecodeError:
        sdk_payload = {}

    sdk_ok = isinstance(sdk_payload, dict) and bool(sdk_payload.get("ok"))
    if sdk_result.returncode != 0 or not sdk_ok:
        err_detail = ""
        if isinstance(sdk_payload, dict):
            raw_err = sdk_payload.get("error")
            if isinstance(raw_err, str):
                err_detail = raw_err.strip()
        stderr = "\n".join(part for part in [err_detail, (sdk_result.stderr or "").strip()] if part).strip()
        return subprocess.CompletedProcess(
            args=sdk_result.args,
            returncode=sdk_result.returncode or 1,
            stdout="",
            stderr=stderr,
        )

    final_response = sdk_payload.get("finalResponse", "")
    if not isinstance(final_response, str):
        final_response = ""
    items = sdk_payload.get("items")
    if not isinstance(items, list):
        items = []

    trace_events: list[dict] = []
    if final_response.strip():
        trace_events.append({"type": "message", "content": final_response})
    for item in items:
        if isinstance(item, dict):
            trace_events.append({"type": "item.completed", "item": item})
    trace_stdout = "\n".join(json.dumps(e) for e in trace_events)
    if trace_stdout:
        trace_stdout += "\n"
    return subprocess.CompletedProcess(
        args=sdk_result.args,
        returncode=0,
        stdout=trace_stdout,
        stderr=(sdk_result.stderr or "").strip(),
    )


def _run_codex_sdk_with_hooks_modal(
    prompt: str,
    model: str,
    work_dir: Path,
    timeout: int,
) -> tuple:
    import subprocess
    import uuid

    hooks_config = _load_codex_hooks_config_modal(work_dir)
    hook_failures: list[str] = []
    hook_warnings: list[str] = []

    unsupported = sorted(event for event in _CODEX_HOOK_EVENTS_UNSUPPORTED if event in hooks_config)
    if unsupported:
        hook_warnings.append(
            "Codex hook emulation does not support events: " + ", ".join(unsupported)
        )

    pre_payload = {"source": "startup", "prompt": prompt, "turn_id": uuid.uuid4().hex}
    hook_failures.extend(
        _run_codex_hook_event_modal(hooks_config, "SessionStart", work_dir, model, pre_payload)
    )
    hook_failures.extend(
        _run_codex_hook_event_modal(hooks_config, "UserPromptSubmit", work_dir, model, pre_payload)
    )
    if hook_failures:
        return (
            subprocess.CompletedProcess(
                args=["node", "run_codex_sdk.mjs"],
                returncode=1,
                stdout="",
                stderr="\n".join(hook_failures),
            ),
            hook_failures,
            hook_warnings,
        )

    result = _run_codex_sdk_turn_modal(prompt=prompt, model=model, work_dir=work_dir, timeout=timeout)
    stop_payload = {
        "turn_id": uuid.uuid4().hex,
        "stop_hook_active": False,
        "last_assistant_message": _extract_last_agent_message_from_trace_modal(result.stdout or ""),
    }
    hook_failures.extend(
        _run_codex_hook_event_modal(hooks_config, "Stop", work_dir, model, stop_payload)
    )
    return result, hook_failures, hook_warnings


def _run_single_task(
    task_data: dict,
    config_agents_md: str,
    config_claude_md: str,
    config_codex_dir_files: dict[str, bytes] | None,
    model: str,
    runtime: str,
    timeout: int,
) -> dict:
    """Execute one ArtifactsBench task inside a Modal container.

    Returns a serializable dict matching TaskResult fields.
    """
    import json
    import os
    import shutil
    import subprocess
    import tempfile
    import time
    from pathlib import Path

    work_dir = Path(tempfile.mkdtemp(prefix=f"artifacts_{task_data['index']}_"))

    try:
        if config_agents_md:
            (work_dir / "AGENTS.md").write_text(config_agents_md)
        if config_claude_md:
            (work_dir / "CLAUDE.md").write_text(config_claude_md)
        elif runtime == "claude_code_cli" and config_agents_md:
            (work_dir / "CLAUDE.md").write_text("@AGENTS.md\n")
        if config_codex_dir_files:
            codex_dir = work_dir / ".codex"
            codex_dir.mkdir(parents=True, exist_ok=True)
            for fname, content in config_codex_dir_files.items():
                target = codex_dir / fname
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)

        subprocess.run(["git", "init"], cwd=str(work_dir), capture_output=True)
        subprocess.run(["git", "add", "."], cwd=str(work_dir), capture_output=True)
        subprocess.run(["git", "commit", "-m", "init", "--allow-empty"],
                       cwd=str(work_dir), capture_output=True)

        debug_stderr = ""

        if runtime == "claude_sdk":
            import asyncio
            from claude_agent_sdk import (
                query, ClaudeAgentOptions,
                AssistantMessage, ResultMessage, TextBlock,
            )

            options = ClaudeAgentOptions(
                model=model,
                cwd=str(work_dir),
                system_prompt=config_agents_md,
                permission_mode="acceptEdits",
                allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
                max_turns=30,
            )

            stderr_lines: list[str] = []
            options.stderr = lambda line: stderr_lines.append(line)

            raw_parts: list[str] = []
            debug_parts: list[str] = []
            try:
                async def _run_sdk() -> None:
                    async for message in query(prompt=task_data["question"], options=options):
                        if isinstance(message, AssistantMessage):
                            for block in message.content:
                                if isinstance(block, TextBlock):
                                    raw_parts.append(block.text)
                        elif isinstance(message, ResultMessage):
                            if getattr(message, "is_error", False):
                                debug_parts.append(f"SDK result error: {getattr(message, 'result', '')}")

                asyncio.run(_run_sdk())
            except Exception as e:
                import traceback
                debug_stderr = f"SDK error: {e}\n{traceback.format_exc()}"

            raw_output = "\n".join(raw_parts)
            if debug_parts:
                debug_stderr = "\n".join(debug_parts)
            if stderr_lines:
                debug_stderr += "\nCLI stderr: " + "\n".join(stderr_lines[-20:])
        elif runtime == "claude_code_cli":
            cmd = [
                "claude", "--print", "--verbose",
                "--output-format", "stream-json",
                "--permission-mode", "bypassPermissions",
                "--max-turns", "30",
                "-p", task_data["question"],
            ]
            if model:
                cmd.extend(["--model", model])

            raw_output = ""
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    result = subprocess.run(
                        cmd, cwd=str(work_dir), capture_output=True,
                        text=True, timeout=timeout,
                    )
                    raw_output = result.stdout
                    debug_stderr = (result.stderr or "")[:2000]
                    if raw_output.strip():
                        break
                    if attempt < max_retries - 1:
                        time.sleep(5 * (attempt + 1))
                except subprocess.TimeoutExpired:
                    debug_stderr = f"TIMEOUT after {timeout}s"
                    if attempt < max_retries - 1:
                        time.sleep(5)
        elif runtime == "codex_cli":
            raw_output = ""
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    result, hook_failures, hook_warnings = _run_codex_cli_with_hooks_modal(
                        prompt=task_data["question"],
                        model=model,
                        work_dir=work_dir,
                        timeout=timeout,
                    )

                    raw_output = result.stdout or ""
                    debug_stderr = (result.stderr or "")[:2000]
                    if hook_warnings or hook_failures:
                        hook_diag = "\n".join(
                            [f"warning: {w}" for w in hook_warnings]
                            + [f"failure: {f}" for f in hook_failures]
                        )
                        debug_stderr = (debug_stderr + "\n[codex_hooks]\n" + hook_diag)[:2000]
                    if raw_output.strip():
                        break
                    if attempt < max_retries - 1:
                        time.sleep(5 * (attempt + 1))
                except subprocess.TimeoutExpired:
                    debug_stderr = f"TIMEOUT after {timeout}s"
                    if attempt < max_retries - 1:
                        time.sleep(5)
        elif runtime == "codex_sdk":
            raw_output = ""
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    result, hook_failures, hook_warnings = _run_codex_sdk_with_hooks_modal(
                        prompt=task_data["question"],
                        model=model,
                        work_dir=work_dir,
                        timeout=timeout,
                    )

                    raw_output = result.stdout or ""
                    debug_stderr = (result.stderr or "")[:2000]
                    if hook_warnings or hook_failures:
                        hook_diag = "\n".join(
                            [f"warning: {w}" for w in hook_warnings]
                            + [f"failure: {f}" for f in hook_failures]
                        )
                        debug_stderr = (debug_stderr + "\n[codex_hooks]\n" + hook_diag)[:2000]
                    if raw_output.strip():
                        break
                    if attempt < max_retries - 1:
                        time.sleep(5 * (attempt + 1))
                except subprocess.TimeoutExpired:
                    debug_stderr = f"TIMEOUT after {timeout}s"
                    if attempt < max_retries - 1:
                        time.sleep(5)
        else:
            raw_output = ""
            debug_stderr = f"Unsupported runtime: {runtime}"

        # --- Extract answer ---
        text_output = _extract_text_from_jsonl_modal(raw_output)
        answer = text_output or raw_output
        if "<html" not in answer.lower() and "```html" not in answer:
            _skip = {"node_modules", ".codex", ".git", "__pycache__", "venv", ".venv"}
            html_files = [
                f for f in work_dir.glob("**/*.html")
                if f.is_file() and not any(s in f.parts for s in _skip) and f.name != "AGENTS.md"
            ]
            if html_files:
                html_content = html_files[0].read_text()
                answer = f"```html\n{html_content}\n```"
                for css in work_dir.glob("**/*.css"):
                    if css.is_file() and not any(s in css.parts for s in _skip):
                        answer += f"\n```css\n{css.read_text()}\n```"
                for js in work_dir.glob("**/*.js"):
                    if js.is_file() and not any(s in js.parts for s in _skip):
                        answer += f"\n```javascript\n{js.read_text()}\n```"

        # --- Screenshot ---
        import re
        screenshot_bytes_list: list[bytes] = []

        html_match = re.search(r"(<html[^>]*>.*?</html>)", answer, re.DOTALL | re.IGNORECASE)
        svg_match = re.search(r"(<svg[^>]*>.*?</svg>)", answer, re.DOTALL | re.IGNORECASE)
        renderable = html_match.group(1) if html_match else (svg_match.group(1) if svg_match else None)

        if renderable:
            html_path = work_dir / "render.html"
            html_path.write_text(renderable, encoding="utf-8")
            try:
                from playwright.sync_api import sync_playwright
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(headless=True)
                    context = browser.new_context()
                    page = context.new_page()
                    page.set_default_timeout(60000)
                    page.goto(f"file://{html_path.resolve()}", timeout=60000)
                    page.wait_for_load_state("networkidle", timeout=60000)
                    for i in range(3):
                        img_path = work_dir / f"screenshot_{i}.png"
                        page.screenshot(path=str(img_path), full_page=True, timeout=60000)
                        if img_path.exists():
                            screenshot_bytes_list.append(img_path.read_bytes())
                        if i < 2:
                            time.sleep(1)
                    context.close()
                    browser.close()
            except Exception as e:
                print(f"  [MODAL] Screenshot error task={task_data['index']}: {e}")

        # --- Judge ---
        from google import genai
        from google.genai import types
        from pydantic import BaseModel, Field
        from typing import List

        class DimensionReview(BaseModel):
            score: int = Field(description="Score 0-10 for this dimension")
            title: str = Field(description="Title of the evaluation dimension")
            review: str = Field(description="Detailed review for this dimension")

        class JudgeResult(BaseModel):
            dimensions: List[DimensionReview] = Field(description="Per-dimension scores and reviews")
            overall_score: int = Field(description="Overall score 0-100 aggregating all dimensions")

        checklist_str = json.dumps(task_data["checklist"])
        truncated_answer = answer[:30000]

        prompt_text = _build_judge_prompt(checklist_str, task_data["question"], truncated_answer)

        content_parts = [types.Part.from_text(text=prompt_text)]
        for img_bytes in screenshot_bytes_list:
            content_parts.append(types.Part.from_bytes(data=img_bytes, mime_type="image/png"))

        api_key = os.environ.get("GEMINI_API_KEY")
        client = genai.Client(api_key=api_key)

        score = None
        judge_response = ""
        for attempt in range(8):
            try:
                response = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=content_parts,
                    config={
                        "response_mime_type": "application/json",
                        "response_schema": JudgeResult,
                    },
                )
                if not response.text:
                    if attempt < 7:
                        time.sleep(3 * (attempt + 1))
                        continue
                    break
                parsed = json.loads(response.text)
                score = float(parsed["overall_score"])
                judge_response = response.text
                break
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                if attempt < 7:
                    time.sleep(2)
                    continue
                break
            except Exception:
                if attempt < 7:
                    time.sleep(3 * (attempt + 1))
                    continue
                break

        reward = score / 100.0 if score is not None else 0.0

        return {
            "task_name": str(task_data["index"]),
            "passed": score is not None and score >= 50,
            "reward": reward,
            "verify_output": f"score={score}" if score is not None else "no_score",
            "verify_exit_code": 0 if score is not None else 1,
            "judge_feedback": judge_response,
            "trace": raw_output,
            "debug_stderr": debug_stderr,
        }

    except Exception as e:
        return {
            "task_name": str(task_data["index"]),
            "passed": False,
            "reward": 0.0,
            "verify_output": f"Error: {e}",
            "verify_exit_code": 1,
            "judge_feedback": "",
            "trace": "",
            "debug_stderr": f"Exception: {e}",
        }
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _build_judge_prompt(checklist: str, question: str, answer: str) -> str:
    """Inline the judge prompt template to avoid shipping lib/ to Modal."""
    return (
        "You are a seasoned and meticulous code review expert, proficient in multiple "
        "programming languages, front-end technologies, and interaction design. Your task "
        "is to conduct an in-depth analysis and scoring of the received [question] and "
        "[answer]. The [answer] may include source code (in various programming languages), "
        "algorithm implementations, data structure designs, system architecture diagrams, "
        "front-end visualization code (such as HTML/SVG/JavaScript), interaction logic "
        "descriptions, and related technical explanations. Please leverage your coding "
        "expertise and aesthetic experience to thoroughly examine the [answer] content from "
        "the following dimensions and provide scores along with detailed review comments. "
        "You should be very strict and cautious when giving full marks for each dimension.\n\n"
        "Role Definition\n\n"
        "Responsibilities: Act as an authoritative technical review committee member, ensuring "
        "objectivity, comprehensiveness, and impartiality.\n"
        "Attitude: Rigorous, professional, and unsparing, adept at identifying details "
        "and potential risks.\n"
        "Additional Traits: Possess exceptional aesthetic talent, with high standards for "
        "visual appeal and user experience.\n\n"
        "I have only extracted the last segment of HTML or SVG code from the provided answer "
        "for visualization. The content is adaptively scrolled to capture the entire page.\n\n"
        "**Scoring Criteria:**\n\n"
        f"{checklist}\n\n"
        "- The final output should be a JSON object containing the dimensions above, "
        "following this example:\n"
        '```json\n{\n "Overall Score": "35"\n}\n``` Reason:...\n\n'
        "Please score the following question according to the standards above:\n\n"
        f"--------Problem starts--------\n{question}\n--------Problem ends--------\n\n"
        f"--------Answer starts--------\n{answer}\n--------Answer ends--------\n"
    )


@app.function(
    image=image,
    include_source=True,
    secrets=[
        modal.Secret.from_name("anthropic-key", required_keys=["ANTHROPIC_API_KEY"]),
        modal.Secret.from_name("openai-key", required_keys=["OPENAI_API_KEY"]),
        modal.Secret.from_name("openai-key-added", required_keys=["OPENAI_API_KEY"]),
        modal.Secret.from_name("codex-key-added", required_keys=["CODEX_API_KEY"]),
        modal.Secret.from_name("gemini-key", required_keys=["GEMINI_API_KEY"]),
    ],
    timeout=900,
    retries=1,
)
def run_task(
    task_data: dict,
    config_agents_md: str,
    config_claude_md: str,
    config_codex_dir_files: dict[str, bytes] | None,
    model: str,
    runtime: str,
    task_timeout: int,
) -> dict:
    return _run_single_task(
        task_data, config_agents_md, config_claude_md, config_codex_dir_files,
        model, runtime, task_timeout,
    )
