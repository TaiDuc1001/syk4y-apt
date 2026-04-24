#!/usr/bin/env python3
import os
import subprocess
import sys
import time
from pathlib import Path

_lib = Path(__file__).parent
sys.path.insert(0, str(_lib))

from jobs import Job, JobStatus


def _kaggle_env(account: dict) -> dict:
    return {
        **os.environ,
        "KAGGLE_USERNAME": account["username"],
        "KAGGLE_KEY": account["key"],
    }


def pull_output(job: Job, account: dict, dest_dir: Path, max_retries: int = 3) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    env = _kaggle_env(account)
    last_err = ""
    for attempt in range(max_retries):
        try:
            result = subprocess.run(
                ["kaggle", "kernels", "output", job.kernel_slug, "-p", str(dest_dir)],
                env=env,
                capture_output=True,
                text=True,
                timeout=300,
            )
            if result.returncode == 0:
                return dest_dir
            last_err = result.stderr.strip()
            time.sleep(2 ** attempt * 5)
        except subprocess.TimeoutExpired:
            last_err = "Timeout"
            time.sleep(2 ** attempt * 5)
        except Exception as e:
            last_err = str(e)
            time.sleep(2 ** attempt * 5)
    raise RuntimeError(f"Failed to pull output for {job.kernel_slug}: {last_err}")
