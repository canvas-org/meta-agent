from __future__ import annotations

import os
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent


def get_workspace_root() -> Path:
    """Return the user workspace root for runtime data and configs.

    Priority:
    1) META_AGENT_WORKSPACE_ROOT env var
    2) current working directory
    """
    override = os.environ.get("META_AGENT_WORKSPACE_ROOT", "").strip()
    if override:
        return Path(override).resolve()
    return Path.cwd().resolve()


def get_experience_root() -> Path:
    return get_workspace_root() / "experience"


def get_benchmark_candidates_dir(benchmark_name: str) -> Path:
    return get_experience_root() / benchmark_name / "candidates"


def rel_to_workspace(path: Path) -> str:
    workspace = get_workspace_root()
    try:
        return str(path.resolve().relative_to(workspace))
    except ValueError:
        return str(path.resolve())
