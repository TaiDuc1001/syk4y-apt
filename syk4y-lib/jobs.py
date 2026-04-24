#!/usr/bin/env python3
import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

RUNS_DIR = Path.home() / ".syk4y" / "runs"


class JobStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    PUSHING = "pushing"
    RUNNING = "running"
    COMPLETE = "complete"
    PULLING = "pulling"
    DONE = "done"
    FAILED = "failed"
    RETRYING = "retrying"
    SKIPPED = "skipped"


TERMINAL_STATUSES = {JobStatus.DONE, JobStatus.FAILED, JobStatus.SKIPPED}
ACTIVE_STATUSES = {JobStatus.PUSHING, JobStatus.RUNNING, JobStatus.PULLING}


@dataclass
class Job:
    job_id: str
    script_line: str
    status: str = JobStatus.PENDING
    account_id: Optional[str] = None
    kernel_slug: Optional[str] = None
    pushed_at: Optional[str] = None
    started_polling_at: Optional[str] = None
    completed_at: Optional[str] = None
    pulled_at: Optional[str] = None
    output_dir: Optional[str] = None
    retries: int = 0
    max_retries: int = 2
    error: Optional[str] = None
    version_number: Optional[int] = None
    duration_seconds: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Job":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in known})

    def elapsed_seconds(self) -> Optional[float]:
        if self.pushed_at is None:
            return None
        start = datetime.fromisoformat(self.pushed_at)
        if self.completed_at:
            end = datetime.fromisoformat(self.completed_at)
        else:
            end = datetime.now(timezone.utc)
        return (end - start).total_seconds()

    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES


@dataclass
class RunState:
    run_id: str
    notebook_source: str
    dataset_sources: list[str]
    created_at: str
    strategy: str
    poll_interval: int
    output_dir: str
    jobs: list[Job] = field(default_factory=list)
    round_robin_index: int = 0
    finished_at: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["jobs"] = [j.to_dict() for j in self.jobs]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "RunState":
        jobs = [Job.from_dict(j) for j in d.pop("jobs", [])]
        known = {f.name for f in cls.__dataclass_fields__.values() if f.name != "jobs"}
        state = cls(**{k: v for k, v in d.items() if k in known})
        state.jobs = jobs
        return state

    def get_job(self, job_id: str) -> Optional[Job]:
        for j in self.jobs:
            if j.job_id == job_id:
                return j
        return None

    def pending_jobs(self) -> list[Job]:
        return [j for j in self.jobs if j.status in (JobStatus.PENDING, JobStatus.RETRYING)]

    def active_jobs(self) -> list[Job]:
        return [j for j in self.jobs if j.is_active()]

    def active_for_account(self, account_id: str) -> list[Job]:
        return [j for j in self.jobs if j.account_id == account_id and j.is_active()]

    def running_jobs(self) -> list[Job]:
        return [j for j in self.jobs if j.status == JobStatus.RUNNING]

    def done_count(self) -> int:
        return sum(1 for j in self.jobs if j.status == JobStatus.DONE)

    def failed_count(self) -> int:
        return sum(1 for j in self.jobs if j.status == JobStatus.FAILED)

    def all_terminal(self) -> bool:
        return all(j.is_terminal() for j in self.jobs)


def state_file(run_id: str) -> Path:
    return RUNS_DIR / run_id / "state.json"


def kernel_dir(run_id: str, job_id: str, account_id: str) -> Path:
    return RUNS_DIR / run_id / "kernels" / job_id / account_id


def output_dir(run_id: str, job_id: str, account_id: str) -> Path:
    return RUNS_DIR / run_id / "outputs" / job_id / account_id


def load_state(run_id: str) -> RunState:
    f = state_file(run_id)
    if not f.exists():
        raise FileNotFoundError(f"Run state not found: {f}")
    data = json.loads(f.read_text(encoding="utf-8"))
    return RunState.from_dict(data)


def save_state(state: RunState) -> None:
    f = state_file(state.run_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state.to_dict(), indent=2) + "\n", encoding="utf-8")
    tmp.replace(f)


def create_run(
    notebook_source: str,
    jobs: list[Job],
    dataset_sources: list[str],
    strategy: str,
    poll_interval: int,
    output_dir_base: str,
    max_retries: int = 2,
) -> RunState:
    run_id = "run-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    for j in jobs:
        j.max_retries = max_retries
    state = RunState(
        run_id=run_id,
        notebook_source=notebook_source,
        dataset_sources=dataset_sources,
        created_at=datetime.now(timezone.utc).isoformat(),
        strategy=strategy,
        poll_interval=poll_interval,
        output_dir=output_dir_base,
        jobs=jobs,
    )
    save_state(state)
    return state


def list_runs() -> list[dict]:
    if not RUNS_DIR.exists():
        return []
    result = []
    for p in sorted(RUNS_DIR.iterdir(), reverse=True):
        sf = p / "state.json"
        if sf.exists():
            try:
                data = json.loads(sf.read_text(encoding="utf-8"))
                total = len(data.get("jobs", []))
                done = sum(1 for j in data.get("jobs", []) if j.get("status") == "done")
                failed = sum(1 for j in data.get("jobs", []) if j.get("status") == "failed")
                result.append({
                    "run_id": data.get("run_id", p.name),
                    "created_at": data.get("created_at", ""),
                    "total": total,
                    "done": done,
                    "failed": failed,
                    "finished_at": data.get("finished_at"),
                })
            except Exception:
                pass
    return result


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
