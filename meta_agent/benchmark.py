from __future__ import annotations
from pathlib import Path
from typing import List, Optional, Union
from pydantic import BaseModel, Field
import yaml


class Task(BaseModel):
    name: str
    instruction: str
    workspace: str
    verify: Union[str, List[str]]
    setup: Optional[Union[str, List[str]]] = None
    timeout: int = 300


class HarborBackend(BaseModel):
    dataset: str
    task_prefix: str = ""
    agent_import_path: str = ""
    modal_env_import_path: str = ""
    default_tasks: Optional[List[str]] = None
    judge_model: Optional[str] = None


class TauBackend(BaseModel):
    tau_repo: str = ""
    domains: List[str] = Field(default_factory=lambda: ["airline", "retail"])
    user_model: str = "gpt-4o"
    user_model_provider: str = "openai"
    task_ids: Optional[List[str]] = None
    judge_model: Optional[str] = None
    judge_strategy: str = "binary"
    sample_size: Optional[int] = None


class SWEBenchMBackend(BaseModel):
    dataset_path: str = "data/swebench_multimodal_dev.parquet"
    task_ids: Optional[List[str]] = None
    timeout: int = 600


class Benchmark(BaseModel):
    name: str
    tasks: List[Task] = []
    description: str = ""
    fast_tasks: List[str] = Field(default_factory=list)
    type: str = "local"
    agent: str = "claude_sdk"
    backend: Optional[HarborBackend] = None
    tau_backend: Optional[TauBackend] = None
    swebench_backend: Optional[SWEBenchMBackend] = None


def load_benchmark(path: str) -> Benchmark:
    raw = Path(path).read_text()
    data = yaml.safe_load(raw)

    bench_type = data.get("type", "local")
    backend_data = None
    if bench_type not in ("local", "harbor") and "backend" in data:
        backend_data = data.pop("backend")

    bench = Benchmark.model_validate(data)

    base_dir = Path(path).parent
    for task in bench.tasks:
        task.workspace = str((base_dir / task.workspace).resolve())

    if bench.type == "local":
        if not bench.tasks:
            raise ValueError(f"Benchmark '{bench.name}' has no tasks")
        names = [t.name for t in bench.tasks]
        if len(names) != len(set(names)):
            raise ValueError(f"Benchmark '{bench.name}' has duplicate task names")
        for task in bench.tasks:
            if not Path(task.workspace).is_dir():
                raise ValueError(f"Workspace not found: {task.workspace}")

    if not bench.fast_tasks:
        bench.fast_tasks = [t.name for t in bench.tasks]

    if bench.type == "harbor":
        if bench.backend is None:
            raise ValueError(f"Benchmark '{bench.name}' requires a 'backend' section")
        if not bench.backend.dataset:
            raise ValueError(f"Benchmark '{bench.name}' backend.dataset is empty")

    if bench.type in ("tau", "tau3"):
        if backend_data:
            bench.tau_backend = TauBackend.model_validate(backend_data)
        if bench.tau_backend is None:
            bench.tau_backend = TauBackend()

    if bench.type == "swebench_m":
        if backend_data:
            bench.swebench_backend = SWEBenchMBackend.model_validate(backend_data)
        if bench.swebench_backend is None:
            bench.swebench_backend = SWEBenchMBackend()

    return bench
