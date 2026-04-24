#!/usr/bin/env python3
import sys
from pathlib import Path
from typing import Optional

_lib = Path(__file__).parent
sys.path.insert(0, str(_lib))

from jobs import RunState, Job, JobStatus


def pick_account(
    accounts: list[dict],
    state: RunState,
    strategy: str = "round-robin",
) -> Optional[dict]:
    available = []
    for acc in accounts:
        if not acc.get("enabled", True):
            continue
        active = len(state.active_for_account(acc["id"]))
        max_gpu = acc.get("max_concurrent_gpu", 2)
        if active < max_gpu:
            available.append((acc, active))

    if not available:
        return None

    if strategy == "round-robin":
        return _round_robin(available, state)
    elif strategy == "least-active":
        return min(available, key=lambda x: x[1])[0]
    else:
        return _round_robin(available, state)


def _round_robin(
    available: list[tuple[dict, int]],
    state: RunState,
) -> Optional[dict]:
    if not available:
        return None
    available_ids = [a[0]["id"] for a in available]
    idx = state.round_robin_index % len(available_ids)
    chosen_id = available_ids[idx]
    state.round_robin_index = (idx + 1) % len(available_ids)
    for acc, _ in available:
        if acc["id"] == chosen_id:
            return acc
    return available[0][0]


def assign_pending_jobs(
    state: RunState,
    accounts: list[dict],
    strategy: str,
) -> list[tuple[Job, dict]]:
    assignments = []
    for job in state.pending_jobs():
        acc = pick_account(accounts, state, strategy)
        if acc is None:
            break
        job.status = JobStatus.QUEUED
        job.account_id = acc["id"]
        assignments.append((job, acc))
    return assignments
