#!/usr/bin/env python3
"""syk4y update CLI: Check for and install updates to syk4y across environments."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _run_git(args: list[str], cwd: Path, capture: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a git command in the target directory."""
    return subprocess.run(
        ["git", "-C", str(cwd)] + args,
        capture_output=capture,
        text=True,
        check=False,
    )


def detect_install_type(install_dir: Path) -> str:
    """Detect whether syk4y is installed via git, APT, Homebrew, macOS pkg, or standalone."""
    # 1. Git checkout (has .git or git worktree)
    try:
        p = _run_git(["rev-parse", "--is-inside-work-tree"], cwd=install_dir)
        if p.returncode == 0 and p.stdout.strip() == "true":
            return "git"
    except OSError:
        pass

    install_str = str(install_dir.resolve())

    # 2. APT package
    if install_str.startswith("/usr/lib/syk4y"):
        return "apt"
    if shutil.which("dpkg-query"):
        try:
            p = subprocess.run(
                ["dpkg-query", "-W", "-f=${Status}", "syk4y"],
                capture_output=True,
                text=True,
            )
            if "install ok installed" in p.stdout:
                return "apt"
        except OSError:
            pass

    # 3. macOS PKG
    if install_str.startswith("/usr/local/lib/syk4y"):
        return "macos_pkg"

    # 4. Homebrew
    if any(k in install_str for k in ("/Cellar/syk4y", "/opt/syk4y", "/linuxbrew")):
        return "homebrew"
    if shutil.which("brew"):
        try:
            p_prefix = subprocess.run(["brew", "--prefix", "syk4y"], capture_output=True, text=True)
            if p_prefix.returncode == 0 and p_prefix.stdout.strip():
                brew_pkg_dir = str(Path(p_prefix.stdout.strip()).resolve())
                if install_str.startswith(brew_pkg_dir):
                    return "homebrew"
        except OSError:
            pass

    return "standalone"


def get_git_info(install_dir: Path) -> dict[str, Any]:
    """Retrieve current Git metadata from the installation directory."""
    p_sha = _run_git(["rev-parse", "HEAD"], cwd=install_dir)
    sha = p_sha.stdout.strip() if p_sha.returncode == 0 else ""

    p_short = _run_git(["rev-parse", "--short", "HEAD"], cwd=install_dir)
    short_sha = p_short.stdout.strip() if p_short.returncode == 0 else ""

    p_branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=install_dir)
    branch = p_branch.stdout.strip() if p_branch.returncode == 0 else ""

    p_date = _run_git(["log", "-1", "--format=%cd", "--date=short"], cwd=install_dir)
    commit_date = p_date.stdout.strip() if p_date.returncode == 0 else ""

    p_tag = _run_git(["describe", "--tags", "--exact-match"], cwd=install_dir)
    tag = p_tag.stdout.strip() if p_tag.returncode == 0 else ""

    p_status = _run_git(["status", "--porcelain"], cwd=install_dir)
    dirty_lines = [line.strip() for line in p_status.stdout.splitlines() if line.strip()]

    p_remote = _run_git(["remote"], cwd=install_dir)
    remotes = [r.strip() for r in p_remote.stdout.splitlines() if r.strip()]

    return {
        "commit": sha,
        "short_commit": short_sha,
        "branch": branch,
        "commit_date": commit_date,
        "tag": tag,
        "is_dirty": len(dirty_lines) > 0,
        "dirty_files": dirty_lines,
        "remotes": remotes,
    }


def handle_git_update(
    install_dir: Path,
    check_only: bool = False,
    force: bool = False,
    branch: str = "",
    tag: str = "",
    remote: str = "origin",
    dry_run: bool = False,
    json_mode: bool = False,
) -> int:
    """Handle checking or applying updates for a git-based syk4y installation."""
    if not shutil.which("git"):
        err = "Error: git command is required for updating git-based syk4y installations."
        if json_mode:
            print(json.dumps({"status": "error", "message": err}))
        else:
            print(err, file=sys.stderr)
        return 1

    info = get_git_info(install_dir)
    current_sha = info["commit"]
    current_short = info["short_commit"]
    current_branch = info["branch"]

    if not current_sha:
        err = f"Error: Unable to inspect git repository at {install_dir}."
        if json_mode:
            print(json.dumps({"status": "error", "message": err}))
        else:
            print(err, file=sys.stderr)
        return 1

    effective_remote = remote
    if effective_remote not in info["remotes"]:
        effective_remote = info["remotes"][0] if info["remotes"] else "origin"

    target_ref = ""
    fetch_arg = ""
    if tag:
        target_ref = f"refs/tags/{tag}"
        fetch_arg = f"refs/tags/{tag}:refs/tags/{tag}"
    elif branch:
        target_ref = f"{effective_remote}/{branch}"
        fetch_arg = branch
    else:
        target_branch = current_branch if current_branch and current_branch != "HEAD" else "main"
        target_ref = f"{effective_remote}/{target_branch}"
        fetch_arg = target_branch

    # Fetch from remote
    fetch_cmd = ["fetch", "--quiet", effective_remote]
    if fetch_arg:
        fetch_cmd.append(fetch_arg)

    p_fetch = _run_git(fetch_cmd, cwd=install_dir)
    if p_fetch.returncode != 0:
        err_msg = (p_fetch.stderr or p_fetch.stdout or "Connection failed").strip()
        err = f"Error: Failed to fetch updates from remote '{effective_remote}': {err_msg}"
        if json_mode:
            print(json.dumps({"status": "error", "message": err, "install_type": "git"}))
        else:
            print(err, file=sys.stderr)
        return 1

    # Resolve target commit SHA
    resolve_ref = target_ref if not tag else f"refs/tags/{tag}"
    p_target = _run_git(["rev-parse", resolve_ref], cwd=install_dir)
    if p_target.returncode != 0:
        p_target = _run_git(["rev-parse", "FETCH_HEAD"], cwd=install_dir)

    target_sha = p_target.stdout.strip() if p_target.returncode == 0 else ""
    if not target_sha:
        err = f"Error: Could not resolve target commit for '{target_ref}'."
        if json_mode:
            print(json.dumps({"status": "error", "message": err, "install_type": "git"}))
        else:
            print(err, file=sys.stderr)
        return 1

    target_short = target_sha[:8]
    is_up_to_date = (current_sha == target_sha)

    # Compute commits behind / ahead
    commits_behind = 0
    recent_commits = []
    if not is_up_to_date:
        p_behind = _run_git(["rev-list", "--count", f"HEAD..{target_sha}"], cwd=install_dir)
        if p_behind.returncode == 0:
            try:
                commits_behind = int(p_behind.stdout.strip())
            except ValueError:
                commits_behind = 0

        p_log = _run_git(["log", "--oneline", "-n", "5", f"HEAD..{target_sha}"], cwd=install_dir)
        if p_log.returncode == 0:
            recent_commits = [line.strip() for line in p_log.stdout.splitlines() if line.strip()]

    result_data = {
        "status": "up_to_date" if is_up_to_date else "update_available",
        "install_type": "git",
        "install_dir": str(install_dir),
        "current_commit": current_sha,
        "current_short": current_short,
        "current_branch": current_branch,
        "target_commit": target_sha,
        "target_short": target_short,
        "update_available": not is_up_to_date,
        "commits_behind": commits_behind,
        "recent_commits": recent_commits,
        "is_dirty": info["is_dirty"],
    }

    if check_only:
        if json_mode:
            print(json.dumps(result_data, indent=2))
        else:
            if is_up_to_date:
                print(f"syk4y is up to date (commit {current_short}, branch {current_branch}).")
            else:
                print(f"Update available for syk4y!")
                print(f"  Current: {current_short} ({info['commit_date']})")
                print(f"  Latest:  {target_short} ({commits_behind} commit{'s' if commits_behind != 1 else ''} ahead)")
                if recent_commits:
                    print("  Recent commits:")
                    for c in recent_commits:
                        print(f"    - {c}")
                print("\nRun 'syk4y update' to apply the update.")
        return 0

    # Applying update
    if is_up_to_date:
        if json_mode:
            print(json.dumps(result_data, indent=2))
        else:
            print(f"syk4y is already up to date (commit {current_short}).")
        return 0

    # Check for dirty working tree
    if info["is_dirty"] and not force:
        err_lines = [
            "Error: Local modifications detected in syk4y installation directory:",
        ]
        for f in info["dirty_files"][:5]:
            err_lines.append(f"  {f}")
        if len(info["dirty_files"]) > 5:
            err_lines.append(f"  ... ({len(info['dirty_files']) - 5} more)")
        err_lines.append("\nTo apply updates, either stash/commit your changes or run:")
        err_lines.append("  syk4y update --force")

        err = "\n".join(err_lines)
        if json_mode:
            result_data["status"] = "dirty_error"
            result_data["message"] = err
            print(json.dumps(result_data, indent=2))
        else:
            print(err, file=sys.stderr)
        return 1

    if dry_run:
        msg = f"[DRY-RUN] Would update syk4y from {current_short} to {target_short} ({commits_behind} commits)."
        if json_mode:
            result_data["status"] = "dry_run"
            result_data["message"] = msg
            print(json.dumps(result_data, indent=2))
        else:
            print(msg)
        return 0

    # Perform the update
    if force:
        p_up = _run_git(["reset", "--hard", target_sha], cwd=install_dir)
    else:
        p_up = _run_git(["merge", "--ff-only", target_sha], cwd=install_dir)
        if p_up.returncode != 0:
            err = (
                f"Error: Fast-forward update failed (local branch may have diverged):\n"
                f"{p_up.stderr or p_up.stdout}\n"
                f"Run 'syk4y update --force' to overwrite local changes and reset to remote."
            )
            if json_mode:
                result_data["status"] = "merge_error"
                result_data["message"] = err
                print(json.dumps(result_data, indent=2))
            else:
                print(err, file=sys.stderr)
            return 1

    p_new = _run_git(["rev-parse", "--short", "HEAD"], cwd=install_dir)
    new_short = p_new.stdout.strip() if p_new.returncode == 0 else target_short

    result_data["status"] = "updated"
    result_data["new_commit"] = new_short
    result_data["previous_commit"] = current_short

    if json_mode:
        print(json.dumps(result_data, indent=2))
    else:
        print(f"Successfully updated syk4y from {current_short} to {new_short}!")
        if recent_commits:
            print("Applied commits:")
            for c in recent_commits:
                print(f"  - {c}")

    return 0


def handle_package_update(
    install_type: str,
    install_dir: Path,
    check_only: bool = False,
    json_mode: bool = False,
) -> int:
    """Handle checking or instructions for package-manager based installations."""
    current_version = "unknown"
    if install_type == "apt":
        try:
            p = subprocess.run(
                ["dpkg-query", "-W", "-f=${Version}", "syk4y"],
                capture_output=True,
                text=True,
            )
            if p.returncode == 0 and p.stdout.strip():
                current_version = p.stdout.strip()
        except OSError:
            pass

    data = {
        "install_type": install_type,
        "install_dir": str(install_dir),
        "current_version": current_version,
    }

    if install_type == "apt":
        data["update_command"] = "sudo apt-get update && sudo apt-get install --only-upgrade syk4y"
        if check_only:
            data["message"] = f"syk4y is installed via APT (version {current_version}). Check for updates with: sudo apt-get update && apt-cache policy syk4y"
            if json_mode:
                print(json.dumps(data, indent=2))
            else:
                print(data["message"])
            return 0
        else:
            if os.geteuid() == 0:
                print("Updating syk4y via APT...")
                cmd = ["apt-get", "update"]
                subprocess.run(cmd, check=False)
                cmd_install = ["apt-get", "install", "--only-upgrade", "-y", "syk4y"]
                ret = subprocess.run(cmd_install, check=False).returncode
                return ret
            else:
                msg = (
                    f"syk4y is installed via APT (version {current_version}).\n"
                    f"To update syk4y, run with sudo or contact your system administrator:\n"
                    f"  sudo apt-get update && sudo apt-get install --only-upgrade syk4y"
                )
                if json_mode:
                    data["message"] = msg
                    print(json.dumps(data, indent=2))
                else:
                    print(msg)
                return 0

    elif install_type == "homebrew":
        data["update_command"] = "brew update && brew upgrade syk4y"
        if check_only:
            data["message"] = "syk4y is installed via Homebrew. Check for updates with: brew outdated syk4y"
            if json_mode:
                print(json.dumps(data, indent=2))
            else:
                print(data["message"])
            return 0
        else:
            print("Updating syk4y via Homebrew...")
            subprocess.run(["brew", "update"], check=False)
            ret = subprocess.run(["brew", "upgrade", "syk4y"], check=False).returncode
            return ret

    elif install_type in ("macos_pkg", "standalone"):
        url = "https://github.com/TaiDuc1001/syk4y-apt/releases/latest"
        data["release_url"] = url
        msg = (
            f"syk4y is installed as a {install_type} installation at {install_dir}.\n"
            f"To check or download the latest release, visit:\n"
            f"  {url}"
        )
        data["message"] = msg
        if json_mode:
            print(json.dumps(data, indent=2))
        else:
            print(msg)
        return 0

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="syk4y update",
        description="Check for and install updates to syk4y.",
        add_help=False,
    )
    parser.add_argument("-c", "--check", action="store_true", help="Check for available updates without applying them")
    parser.add_argument("-y", "--yes", action="store_true", help="Apply updates without interactive confirmation")
    parser.add_argument("-f", "--force", action="store_true", help="Force update even if local files are modified")
    parser.add_argument("--branch", default="", help="Track and update to a specific git branch")
    parser.add_argument("--tag", default="", help="Update to a specific release tag")
    parser.add_argument("--remote", default="origin", help="Git remote to fetch from (default: origin)")
    parser.add_argument("--dry-run", action="store_true", help="Simulate update actions without modifying files")
    parser.add_argument("--json", action="store_true", help="Output update status as JSON")
    parser.add_argument("-v", "--version", action="store_true", help="Show current version/commit of syk4y")
    parser.add_argument("--install-dir", default="", help="Path to syk4y installation directory")
    parser.add_argument("-h", "--help", action="store_true", help="Show this help")

    args = parser.parse_args(argv)

    if args.help:
        parser.print_help()
        return 0

    install_dir = Path(args.install_dir).resolve() if args.install_dir else Path(__file__).resolve().parents[1]

    install_type = detect_install_type(install_dir)

    if args.version:
        if install_type == "git":
            info = get_git_info(install_dir)
            tag_part = f", tag: {info['tag']}" if info['tag'] else ""
            out = f"syk4y (git commit {info['short_commit']}{tag_part}, date {info['commit_date']}, branch {info['branch']})"
            if args.json:
                print(json.dumps({
                    "version": info["short_commit"],
                    "install_type": "git",
                    "commit": info["commit"],
                    "tag": info["tag"],
                    "date": info["commit_date"],
                    "branch": info["branch"],
                }, indent=2))
            else:
                print(out)
        else:
            out = f"syk4y ({install_type} installation at {install_dir})"
            if args.json:
                print(json.dumps({
                    "version": "unknown",
                    "install_type": install_type,
                    "install_dir": str(install_dir),
                }, indent=2))
            else:
                print(out)
        return 0

    if install_type == "git":
        return handle_git_update(
            install_dir=install_dir,
            check_only=args.check,
            force=args.force,
            branch=args.branch,
            tag=args.tag,
            remote=args.remote,
            dry_run=args.dry_run,
            json_mode=args.json,
        )
    else:
        return handle_package_update(
            install_type=install_type,
            install_dir=install_dir,
            check_only=args.check,
            json_mode=args.json,
        )


if __name__ == "__main__":
    sys.exit(main())
