#!/usr/bin/env python3
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

_lib = Path(__file__).parent
sys.path.insert(0, str(_lib))

import accounts as acc_mod
import notebook_gen as nb_gen
import poller as pol
import puller as pul
import scheduler as sched
from jobs import (
    Job,
    JobStatus,
    RunState,
    RUNS_DIR,
    kernel_dir,
    load_state,
    now_iso,
    output_dir,
    save_state,
)


class StopRequested(Exception):
    pass


def _get_account(accounts: list[dict], account_id: str) -> Optional[dict]:
    for a in accounts:
        if a["id"] == account_id:
            return a
    return None


def _handle_push(
    job: Job,
    account: dict,
    state: RunState,
    template_notebook: dict,
    log: Callable,
) -> None:
    kdir = kernel_dir(state.run_id, job.job_id, account["id"])
    slug_without_user = nb_gen._make_kernel_slug(state.run_id, job.job_id)
    job.kernel_slug = f"{account['username']}/{slug_without_user}"

    try:
        log(f"[{job.job_id}] Generating kernel dir → {kdir}")
        nb_gen.generate_kernel_dir(
            job=job,
            account=account,
            run_id=state.run_id,
            template_notebook=template_notebook,
            dataset_sources=state.dataset_sources,
            kernel_dir_path=kdir,
        )

        log(f"[{job.job_id}] Pushing to {account['id']} ({account['username']})...")
        job.status = JobStatus.PUSHING
        save_state(state)

        pol.push_job(job, account, kdir)

        job.status = JobStatus.RUNNING
        job.pushed_at = now_iso()
        job.started_polling_at = now_iso()
        log(f"[{job.job_id}] Pushed → {job.kernel_slug}")
    except Exception as e:
        job.status = JobStatus.FAILED
        job.error = f"Push error: {e}"
        log(f"[{job.job_id}] Push FAILED: {e}")
    save_state(state)


def _handle_poll(
    job: Job,
    account: dict,
    state: RunState,
    log: Callable,
) -> None:
    try:
        new_status = pol.poll_status(job, account)
        if new_status == JobStatus.COMPLETE and job.status != JobStatus.COMPLETE:
            log(f"[{job.job_id}] Complete! Pulling output...")
            job.status = JobStatus.COMPLETE
            job.completed_at = now_iso()
            save_state(state)
            _handle_pull(job, account, state, log)
        elif new_status == JobStatus.FAILED and job.status != JobStatus.FAILED:
            if job.retries < job.max_retries:
                job.retries += 1
                job.status = JobStatus.RETRYING
                job.error = "Kernel reported error, retrying..."
                job.account_id = None
                job.kernel_slug = None
                log(f"[{job.job_id}] Failed, retry {job.retries}/{job.max_retries}")
            else:
                job.status = JobStatus.FAILED
                job.error = "Kernel error (max retries exceeded)"
                log(f"[{job.job_id}] FAILED after {job.retries} retries")
        save_state(state)
    except Exception as e:
        log(f"[{job.job_id}] Poll error (non-fatal): {e}")


def _handle_pull(
    job: Job,
    account: dict,
    state: RunState,
    log: Callable,
) -> None:
    dest = output_dir(state.run_id, job.job_id, account["id"])
    job.status = JobStatus.PULLING
    save_state(state)
    try:
        actual_out = pul.pull_output(job, account, dest)
        job.status = JobStatus.DONE
        job.pulled_at = now_iso()
        job.output_dir = str(actual_out)
        secs = job.elapsed_seconds()
        job.duration_seconds = secs
        log(f"[{job.job_id}] Done. Output → {actual_out}")
    except Exception as e:
        job.status = JobStatus.FAILED
        job.error = f"Pull error: {e}"
        log(f"[{job.job_id}] Pull FAILED: {e}")
    save_state(state)


def _log_noop(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def run_orchestrator(
    run_id: str,
    tick_callback: Optional[Callable[[RunState], None]] = None,
    log: Callable = _log_noop,
    stop_flag: Optional[list] = None,
) -> RunState:
    state = load_state(run_id)
    enabled_accounts = acc_mod.get_enabled_accounts()

    if not enabled_accounts:
        log("ERROR: No enabled accounts. Use: syk4y kaggle account add")
        sys.exit(1)

    template_notebook = nb_gen.load_template_notebook(Path(state.notebook_source))

    def _should_stop():
        return stop_flag is not None and stop_flag[0]

    log(f"Starting orchestrator for run {run_id}")
    log(f"  Jobs: {len(state.jobs)} | Accounts: {len(enabled_accounts)} | Strategy: {state.strategy}")

    while not state.all_terminal():
        if _should_stop():
            log("Stop requested. Saving state. Kaggle jobs continue running.")
            save_state(state)
            return state

        assignments = sched.assign_pending_jobs(state, enabled_accounts, state.strategy)
        for job, account in assignments:
            _handle_push(job, account, state, template_notebook, log)

        for job in list(state.running_jobs()):
            account = _get_account(enabled_accounts, job.account_id)
            if account is None:
                log(f"[{job.job_id}] Account '{job.account_id}' not found, skipping poll")
                continue
            _handle_poll(job, account, state, log)

        if tick_callback:
            tick_callback(state)

        if state.all_terminal():
            break

        if _should_stop():
            log("Stop requested. Saving state.")
            save_state(state)
            return state

        next_poll = state.poll_interval
        log(f"Sleeping {next_poll}s before next poll...")
        for _ in range(next_poll * 2):
            if _should_stop():
                break
            time.sleep(0.5)

    state.finished_at = now_iso()
    save_state(state)

    done = state.done_count()
    failed = state.failed_count()
    log(f"Run {run_id} complete: {done} done, {failed} failed out of {len(state.jobs)} jobs.")
    return state
