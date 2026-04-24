#!/usr/bin/env python3
import argparse
import os
import signal
import sys
import threading
from pathlib import Path

_lib = Path(__file__).parent
sys.path.insert(0, str(_lib))

import accounts as acc_mod
import dashboard as dash
import notebook_gen as nb_gen
import puller as pul
from jobs import (
    RUNS_DIR,
    Job,
    JobStatus,
    RunState,
    create_run,
    list_runs,
    load_state,
    now_iso,
    output_dir,
    save_state,
)
from runner import run_orchestrator


def _die(msg: str, code: int = 1) -> None:
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(code)


def cmd_start(args: list[str]) -> None:
    p = argparse.ArgumentParser(prog="syk4y kaggle run start")
    p.add_argument("--notebook", required=True, help="Path to .ipynb notebook")
    p.add_argument(
        "--dataset-sources",
        nargs="*",
        default=[],
        dest="dataset_sources",
        metavar="OWNER/DATASET",
        help="Kaggle dataset sources (e.g. taiduc1001/my-datasets)",
    )
    p.add_argument(
        "--strategy",
        default="round-robin",
        choices=["round-robin", "least-active"],
    )
    p.add_argument("--poll-interval", type=int, default=120, dest="poll_interval", help="Seconds between polls")
    p.add_argument("--output-dir", default="./results", dest="output_dir")
    p.add_argument("--max-retries", type=int, default=2, dest="max_retries")
    p.add_argument("--dry-run", action="store_true", dest="dry_run", help="Generate kernel dirs only, do not push")
    p.add_argument("--no-dashboard", action="store_true", dest="no_dashboard")
    ns = p.parse_args(args)

    notebook_path = Path(ns.notebook).resolve()
    if not notebook_path.exists():
        _die(f"Notebook not found: {notebook_path}")
    if not notebook_path.suffix == ".ipynb":
        _die("Notebook must be a .ipynb file")

    accounts = acc_mod.get_enabled_accounts()
    if not accounts:
        _die("No enabled accounts. Add one with: syk4y kaggle account add")

    print(f"Parsing notebook: {notebook_path}")
    jobs = nb_gen.parse_notebook_jobs(notebook_path)
    if not jobs:
        _die("No bash script lines found in notebook. Ensure cells contain lines like:\n  !bash /path/to/script.sh\n  # !bash /path/to/script.sh")

    print(f"Found {len(jobs)} job(s):")
    for j in jobs:
        print(f"  [{j.job_id}]  {j.script_line}")

    print(f"\nAccounts ({len(accounts)}):")
    for a in accounts:
        print(f"  {a['id']} → {a['username']} (max {a.get('max_concurrent_gpu', 2)} GPU)")

    output_base = str(Path(ns.output_dir).resolve())
    state = create_run(
        notebook_source=str(notebook_path),
        jobs=jobs,
        dataset_sources=ns.dataset_sources,
        strategy=ns.strategy,
        poll_interval=ns.poll_interval,
        output_dir_base=output_base,
        max_retries=ns.max_retries,
    )

    print(f"\nRun ID: {state.run_id}")
    print(f"State:  {RUNS_DIR / state.run_id / 'state.json'}")

    if ns.dry_run:
        print("\nDry-run mode: generating kernel dirs only...")
        template = nb_gen.load_template_notebook(notebook_path)
        for job in jobs:
            acc = accounts[0]
            kdir = RUNS_DIR / state.run_id / "kernels" / job.job_id / acc["id"]
            nb_gen.generate_kernel_dir(
                job=job,
                account=acc,
                run_id=state.run_id,
                template_notebook=template,
                dataset_sources=ns.dataset_sources,
                kernel_dir_path=kdir,
            )
            print(f"  Generated: {kdir}")
        print("Dry-run complete. No kernels pushed.")
        return

    stop_flag = [False]

    def _sigint(sig, frame):
        print("\nInterrupt received. Detaching (Kaggle jobs continue)...")
        stop_flag[0] = True

    signal.signal(signal.SIGINT, _sigint)

    if ns.no_dashboard:
        run_orchestrator(state.run_id, log=print, stop_flag=stop_flag)
    else:
        def _state_loader():
            return load_state(state.run_id)

        runner_done = [False]
        def _run_thread():
            run_orchestrator(state.run_id, stop_flag=stop_flag)
            runner_done[0] = True
            stop_flag[0] = True

        t = threading.Thread(target=_run_thread, daemon=True)
        t.start()

        dash.run_dashboard(
            state_loader=_state_loader,
            stop_flag=stop_flag,
            run_id=state.run_id,
        )

        if not runner_done[0]:
            print(f"\nDetached. Run continues in background.")
            print(f"Check status: syk4y kaggle run status --run-id {state.run_id}")
        else:
            final = load_state(state.run_id)
            dash.print_status(final)


def cmd_status(args: list[str]) -> None:
    p = argparse.ArgumentParser(prog="syk4y kaggle run status")
    p.add_argument("--run-id", dest="run_id", help="Run ID (default: latest)")
    ns = p.parse_args(args)

    if ns.run_id:
        try:
            state = load_state(ns.run_id)
        except FileNotFoundError:
            _die(f"Run '{ns.run_id}' not found.")
        dash.print_status(state)
    else:
        runs = list_runs()
        if not runs:
            print("No runs found. Use: syk4y kaggle run start")
            return
        latest = runs[0]
        state = load_state(latest["run_id"])
        dash.print_status(state)


def cmd_list(_args: list[str]) -> None:
    runs = list_runs()
    if not runs:
        print("No runs found.")
        return
    print(f"{'Run ID':<30}  {'Created':<26}  {'Done':<6}  {'Failed':<8}  {'Total':<7}  Finished")
    print("─" * 90)
    for r in runs:
        fin = r.get("finished_at") or "—"
        if fin and fin != "—":
            fin = fin[:19].replace("T", " ")
        created = r.get("created_at", "")[:19].replace("T", " ")
        print(
            f"{r['run_id']:<30}  {created:<26}  {r['done']:<6}  {r['failed']:<8}  {r['total']:<7}  {fin}"
        )


def cmd_pull(args: list[str]) -> None:
    p = argparse.ArgumentParser(prog="syk4y kaggle run pull")
    p.add_argument("--run-id", dest="run_id", help="Run ID (default: latest)")
    p.add_argument("--job-id", dest="job_id", help="Pull only this job")
    ns = p.parse_args(args)

    if ns.run_id:
        try:
            state = load_state(ns.run_id)
        except FileNotFoundError:
            _die(f"Run '{ns.run_id}' not found.")
    else:
        runs = list_runs()
        if not runs:
            _die("No runs found.")
        state = load_state(runs[0]["run_id"])

    accounts_map = {a["id"]: a for a in acc_mod.load_accounts()}

    targets = state.jobs
    if ns.job_id:
        targets = [j for j in state.jobs if j.job_id == ns.job_id]
        if not targets:
            _die(f"Job '{ns.job_id}' not found.")

    pulled = 0
    for job in targets:
        if job.status not in (JobStatus.COMPLETE, JobStatus.DONE):
            print(f"  [{job.job_id}] Skipping — status: {job.status}")
            continue
        if job.account_id not in accounts_map:
            print(f"  [{job.job_id}] Account '{job.account_id}' not found, skipping")
            continue
        account = accounts_map[job.account_id]
        dest = Path(output_dir(state.run_id, job.job_id, job.account_id))
        print(f"  [{job.job_id}] Pulling from {job.kernel_slug}...", end=" ", flush=True)
        try:
            pul.pull_output(job, account, dest)
            job.status = JobStatus.DONE
            job.pulled_at = now_iso()
            job.output_dir = str(dest)
            save_state(state)
            print(f"→ {dest}")
            pulled += 1
        except Exception as e:
            print(f"FAILED: {e}")

    print(f"\nPulled {pulled} output(s).")


def cmd_stop(args: list[str]) -> None:
    p = argparse.ArgumentParser(prog="syk4y kaggle run stop")
    p.add_argument("--run-id", dest="run_id")
    ns = p.parse_args(args)
    print("Stop is handled via Ctrl+C / q in the dashboard.")
    print("Kaggle jobs continue running. Use 'syk4y kaggle run resume' to reconnect.")
    if ns.run_id:
        print(f"State file: {RUNS_DIR / ns.run_id / 'state.json'}")


def cmd_resume(args: list[str]) -> None:
    p = argparse.ArgumentParser(prog="syk4y kaggle run resume")
    p.add_argument("--run-id", dest="run_id", required=True)
    p.add_argument("--no-dashboard", action="store_true", dest="no_dashboard")
    ns = p.parse_args(args)

    try:
        state = load_state(ns.run_id)
    except FileNotFoundError:
        _die(f"Run '{ns.run_id}' not found.")

    if state.all_terminal():
        print(f"Run '{ns.run_id}' is already finished.")
        dash.print_status(state)
        return

    print(f"Resuming run: {ns.run_id}")
    stop_flag = [False]

    def _sigint(sig, frame):
        print("\nDetaching...")
        stop_flag[0] = True

    signal.signal(signal.SIGINT, _sigint)

    if ns.no_dashboard:
        run_orchestrator(ns.run_id, log=print, stop_flag=stop_flag)
    else:
        def _state_loader():
            return load_state(ns.run_id)

        stop_flag = [False]
        runner_done = [False]

        def _run_thread():
            run_orchestrator(ns.run_id, stop_flag=stop_flag)
            runner_done[0] = True
            stop_flag[0] = True

        t = threading.Thread(target=_run_thread, daemon=True)
        t.start()

        dash.run_dashboard(
            state_loader=_state_loader,
            stop_flag=stop_flag,
            run_id=ns.run_id,
        )


USAGE = """\
Usage: syk4y kaggle run <subcommand> [options]

Subcommands:
  start     Start a new multi-account run
  status    Show status of a run
  list      List all runs
  pull      Pull completed outputs
  resume    Resume monitoring an existing run
  stop      Detach from run (Kaggle jobs keep running)

Examples:
  syk4y kaggle run start --notebook experiment.ipynb \\
    --dataset-sources "user/my-datasets" "user/my-checkpoints" \\
    --strategy round-robin --poll-interval 120

  syk4y kaggle run status
  syk4y kaggle run status --run-id run-20260423-161500
  syk4y kaggle run list
  syk4y kaggle run pull --run-id run-20260423-161500
  syk4y kaggle run resume --run-id run-20260423-161500
"""


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help", "help"):
        print(USAGE)
        return

    subcmd = args[0]
    rest = args[1:]

    if subcmd == "start":
        cmd_start(rest)
    elif subcmd == "status":
        cmd_status(rest)
    elif subcmd == "list":
        cmd_list(rest)
    elif subcmd == "pull":
        cmd_pull(rest)
    elif subcmd == "stop":
        cmd_stop(rest)
    elif subcmd == "resume":
        cmd_resume(rest)
    else:
        print(f"Unknown subcommand: {subcmd}", file=sys.stderr)
        print(USAGE, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
