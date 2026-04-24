#!/usr/bin/env python3
import curses
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_lib = Path(__file__).parent
sys.path.insert(0, str(_lib))

from jobs import RunState, Job, JobStatus


_STATUS_ICONS = {
    JobStatus.PENDING: "⏳",
    JobStatus.QUEUED: "📋",
    JobStatus.PUSHING: "📤",
    JobStatus.RUNNING: "🔄",
    JobStatus.COMPLETE: "✅",
    JobStatus.PULLING: "📥",
    JobStatus.DONE: "✅",
    JobStatus.FAILED: "❌",
    JobStatus.RETRYING: "🔁",
    JobStatus.SKIPPED: "⏭",
}

_STATUS_COLORS = {
    JobStatus.PENDING: 7,
    JobStatus.QUEUED: 6,
    JobStatus.PUSHING: 6,
    JobStatus.RUNNING: 3,
    JobStatus.COMPLETE: 2,
    JobStatus.PULLING: 6,
    JobStatus.DONE: 2,
    JobStatus.FAILED: 1,
    JobStatus.RETRYING: 5,
    JobStatus.SKIPPED: 7,
}


def _fmt_duration(seconds: Optional[float]) -> str:
    if seconds is None or seconds < 0:
        return "—"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h}h{m:02d}m"
    if m > 0:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def _elapsed_str(job: Job) -> str:
    secs = job.elapsed_seconds()
    return _fmt_duration(secs)


def _progress_bar(done: int, total: int, width: int = 30) -> str:
    if total == 0:
        return " " * width
    filled = int(width * done / total)
    return "█" * filled + "░" * (width - filled)


def run_dashboard(
    state_loader,
    stop_flag: list,
    run_id: str,
    refresh_interval: float = 3.0,
) -> None:
    def _safe_curses(stdscr):
        curses.curs_set(0)
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_RED, -1)
        curses.init_pair(2, curses.COLOR_GREEN, -1)
        curses.init_pair(3, curses.COLOR_YELLOW, -1)
        curses.init_pair(4, curses.COLOR_BLUE, -1)
        curses.init_pair(5, curses.COLOR_MAGENTA, -1)
        curses.init_pair(6, curses.COLOR_CYAN, -1)
        curses.init_pair(7, curses.COLOR_WHITE, -1)
        stdscr.nodelay(True)
        stdscr.timeout(int(refresh_interval * 1000))

        start_time = time.time()
        scroll_offset = 0

        while not stop_flag[0]:
            key = stdscr.getch()
            if key in (ord("q"), ord("Q"), 27):
                stop_flag[0] = True
                break
            if key == curses.KEY_DOWN:
                scroll_offset += 1
            if key == curses.KEY_UP:
                scroll_offset = max(0, scroll_offset - 1)

            try:
                state = state_loader()
            except Exception:
                continue

            stdscr.erase()
            h, w = stdscr.getmaxyx()

            elapsed = time.time() - start_time
            elapsed_str = _fmt_duration(elapsed)

            row = 0
            def _addstr(r, c, text, attr=0):
                if r < 0 or r >= h or c < 0:
                    return
                try:
                    stdscr.addstr(r, c, text[:max(0, w - c)], attr)
                except curses.error:
                    pass

            header = f" syk4y run — {run_id}"
            elapsed_label = f"Elapsed: {elapsed_str} "
            _addstr(row, 0, "─" * w, curses.color_pair(4))
            _addstr(row, 1, header, curses.A_BOLD | curses.color_pair(4))
            _addstr(row, max(0, w - len(elapsed_label) - 1), elapsed_label, curses.color_pair(7))
            row += 1

            all_accs = {}
            for job in state.jobs:
                if job.account_id:
                    all_accs[job.account_id] = all_accs.get(job.account_id, 0)
                    if job.is_active():
                        all_accs[job.account_id] += 1

            _addstr(row, 1, "Accounts:", curses.A_BOLD)
            row += 1
            for acc_id, active in all_accs.items():
                bar = "█" * active + "░" * max(0, 2 - active)
                line = f"  {acc_id:<16} {bar}  {active}/2 GPU slots"
                _addstr(row, 0, line, curses.color_pair(6))
                row += 1

            row += 1
            total = len(state.jobs)
            done = state.done_count()
            failed = state.failed_count()
            running = len(state.running_jobs())
            pending = len(state.pending_jobs())

            summary = f"  Jobs: {done}/{total} done  {running} running  {pending} pending  {failed} failed"
            _addstr(row, 0, summary, curses.A_BOLD)
            row += 1

            pct = int(100 * done / total) if total > 0 else 0
            bar = _progress_bar(done, total, min(50, w - 10))
            _addstr(row, 2, f"{bar} {pct}%", curses.color_pair(2))
            row += 1

            row += 1
            col_header = f"  {'#':<4}  {'Job ID':<22}  {'Account':<16}  {'Status':<12}  {'Duration':<10}  Output"
            _addstr(row, 0, col_header, curses.A_BOLD | curses.A_UNDERLINE)
            row += 1

            visible_jobs = state.jobs[scroll_offset:]
            for i, job in enumerate(visible_jobs):
                if row >= h - 2:
                    break
                idx = i + scroll_offset + 1
                icon = _STATUS_ICONS.get(job.status, "?")
                color = curses.color_pair(_STATUS_COLORS.get(job.status, 7))
                dur = _elapsed_str(job)
                out = "✔" if job.output_dir else "—"
                acc_label = job.account_id or "—"
                line = f"  {idx:<4}  {job.job_id:<22}  {acc_label:<16}  {icon} {job.status:<10}  {dur:<10}  {out}"
                _addstr(row, 0, line, color)
                row += 1

            footer = f" ↑↓ scroll  q quit/detach  │  Next poll: auto "
            _addstr(h - 1, 0, "─" * w, curses.color_pair(4))
            _addstr(h - 1, 1, footer, curses.color_pair(7))

            stdscr.refresh()

            if state.all_terminal():
                time.sleep(1)
                break

    try:
        curses.wrapper(_safe_curses)
    except Exception:
        pass


def print_status(state: RunState) -> None:
    total = len(state.jobs)
    done = state.done_count()
    failed = state.failed_count()
    running = len(state.running_jobs())
    pending = len(state.pending_jobs())

    print(f"\nRun: {state.run_id}")
    print(f"  Created:  {state.created_at}")
    print(f"  Jobs:     {total} total | {done} done | {running} running | {pending} pending | {failed} failed")
    if state.finished_at:
        print(f"  Finished: {state.finished_at}")
    print()

    print(f"  {'#':<4}  {'Job ID':<22}  {'Account':<16}  {'Status':<12}  {'Duration':<10}  Output")
    print("  " + "─" * 80)
    for i, job in enumerate(state.jobs):
        icon = _STATUS_ICONS.get(job.status, "?")
        dur_secs = job.elapsed_seconds()
        dur = _fmt_duration(dur_secs)
        out = "✔ " + job.output_dir if job.output_dir else "—"
        acc = job.account_id or "—"
        print(f"  {i+1:<4}  {job.job_id:<22}  {acc:<16}  {icon} {job.status:<10}  {dur:<10}  {out}")
    print()
