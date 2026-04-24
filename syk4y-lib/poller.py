#!/usr/bin/env python3
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

_lib = Path(__file__).parent
sys.path.insert(0, str(_lib))

from jobs import Job, JobStatus


_TERMINAL_KAGGLE_STATUSES = {"complete", "error", "cancelAcknowledged", "cancel"}
_RUNNING_KAGGLE_STATUSES = {"running", "queued", "starting"}


def _kaggle_env(account: dict) -> dict:
    return {
        **os.environ,
        "KAGGLE_USERNAME": account["username"],
        "KAGGLE_KEY": account["key"],
    }


def push_job(job: Job, account: dict, kernel_dir: Path) -> None:
    env = _kaggle_env(account)
    result = subprocess.run(
        ["kaggle", "kernels", "push", "-p", str(kernel_dir)],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Push failed")


def poll_status(job: Job, account: dict, max_retries: int = 3) -> str:
    env = _kaggle_env(account)
    last_err = ""
    for attempt in range(max_retries):
        try:
            result = subprocess.run(
                ["kaggle", "kernels", "status", job.kernel_slug],
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                last_err = result.stderr.strip()
                time.sleep(2 ** attempt)
                continue

            output = (result.stdout + result.stderr).lower()
            for status in _TERMINAL_KAGGLE_STATUSES:
                if status in output:
                    if status == "complete":
                        return JobStatus.COMPLETE
                    return JobStatus.FAILED
            for status in _RUNNING_KAGGLE_STATUSES:
                if status in output:
                    return JobStatus.RUNNING
            return JobStatus.RUNNING
        except subprocess.TimeoutExpired:
            last_err = "Timeout"
            time.sleep(2 ** attempt)
        except Exception as e:
            last_err = str(e)
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Failed to poll status after {max_retries} attempts: {last_err}")


def is_terminal_status(kaggle_status: str) -> bool:
    return kaggle_status in (JobStatus.COMPLETE, JobStatus.FAILED)


def extract_kernel_slug(job: Job, account: dict) -> Optional[str]:
    return job.kernel_slug
