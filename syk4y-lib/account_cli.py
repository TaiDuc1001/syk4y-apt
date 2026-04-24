#!/usr/bin/env python3
import json
import sys
from pathlib import Path

_lib = Path(__file__).parent
sys.path.insert(0, str(_lib))

import accounts as acc_mod


def _die(msg: str) -> None:
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(1)


def cmd_add(args: list[str]) -> None:
    import argparse

    p = argparse.ArgumentParser(prog="syk4y kaggle account add")
    p.add_argument("--id", required=True, dest="account_id", help="Account ID (alias)")
    p.add_argument("--username", help="Kaggle username")
    p.add_argument("--key", help="Kaggle API key")
    p.add_argument("--from-json", dest="from_json", help="Path to kaggle.json")
    p.add_argument("--max-concurrent-gpu", type=int, default=2, dest="max_concurrent_gpu")
    p.add_argument("--weekly-gpu-hours", type=int, default=30, dest="weekly_gpu_limit_hours")
    p.add_argument("--force", action="store_true", help="Overwrite existing account")
    p.add_argument("--no-validate", action="store_true", dest="no_validate")
    ns = p.parse_args(args)

    username = ns.username
    key = ns.key

    if ns.from_json:
        try:
            data = json.loads(Path(ns.from_json).read_text(encoding="utf-8"))
            username = username or data.get("username", "")
            key = key or data.get("key", "")
        except Exception as e:
            _die(f"Failed to read {ns.from_json}: {e}")

    if not username:
        _die("--username is required (or provide --from-json with username).")
    if not key:
        _die("--key is required (or provide --from-json with key).")

    if ns.force:
        try:
            acc_mod.remove_account(ns.account_id)
        except ValueError:
            pass

    try:
        entry = acc_mod.add_account(
            account_id=ns.account_id,
            username=username,
            key=key,
            max_concurrent_gpu=ns.max_concurrent_gpu,
            weekly_gpu_limit_hours=ns.weekly_gpu_limit_hours,
        )
    except ValueError as e:
        _die(str(e))
        return

    if not ns.no_validate:
        print(f"Validating credentials for '{ns.account_id}'...", end=" ", flush=True)
        ok, msg = acc_mod.validate_account(entry)
        if ok:
            print("OK")
        else:
            print(f"FAILED ({msg})")
            print("Account saved but credentials could not be verified.", file=sys.stderr)
    else:
        print(f"Account '{ns.account_id}' added.")


def cmd_list(_args: list[str]) -> None:
    accounts = acc_mod.load_accounts()
    if not accounts:
        print("No accounts configured. Use: syk4y kaggle account add")
        return
    print(f"{'ID':<16}  {'Username':<24}  {'GPU/week':<10}  {'Max GPU':<9}  {'Enabled'}")
    print("─" * 75)
    for a in accounts:
        status = "✓" if a.get("enabled", True) else "✗"
        print(
            f"{a['id']:<16}  {a['username']:<24}  "
            f"{a.get('weekly_gpu_limit_hours', 30):<10}  "
            f"{a.get('max_concurrent_gpu', 2):<9}  {status}"
        )


def cmd_remove(args: list[str]) -> None:
    if not args:
        _die("Usage: syk4y kaggle account remove <id>")
    account_id = args[0]
    try:
        acc_mod.remove_account(account_id)
        print(f"Account '{account_id}' removed.")
    except ValueError as e:
        _die(str(e))


def cmd_enable(args: list[str], enabled: bool) -> None:
    if not args:
        action = "enable" if enabled else "disable"
        _die(f"Usage: syk4y kaggle account {action} <id>")
    account_id = args[0]
    try:
        acc_mod.update_account(account_id, enabled=enabled)
        action = "enabled" if enabled else "disabled"
        print(f"Account '{account_id}' {action}.")
    except ValueError as e:
        _die(str(e))


def cmd_test(args: list[str]) -> None:
    import argparse

    p = argparse.ArgumentParser(prog="syk4y kaggle account test")
    p.add_argument("ids", nargs="*", help="Account IDs to test (default: all)")
    ns = p.parse_args(args)

    accounts = acc_mod.load_accounts()
    if not accounts:
        print("No accounts configured.")
        return

    targets = [a for a in accounts if (not ns.ids or a["id"] in ns.ids)]
    if not targets:
        _die("No matching accounts found.")

    all_ok = True
    for a in targets:
        print(f"Testing '{a['id']}' ({a['username']})...", end=" ", flush=True)
        ok, msg = acc_mod.validate_account(a)
        if ok:
            print("OK ✓")
        else:
            print(f"FAILED ✗  ({msg})")
            all_ok = False

    sys.exit(0 if all_ok else 1)


def cmd_set(args: list[str]) -> None:
    import argparse

    p = argparse.ArgumentParser(prog="syk4y kaggle account set")
    p.add_argument("account_id")
    p.add_argument("--max-concurrent-gpu", type=int, dest="max_concurrent_gpu")
    p.add_argument("--weekly-gpu-hours", type=int, dest="weekly_gpu_limit_hours")
    p.add_argument("--username")
    p.add_argument("--key")
    ns = p.parse_args(args)

    kwargs = {k: v for k, v in vars(ns).items() if k != "account_id" and v is not None}
    if not kwargs:
        _die("Provide at least one field to update.")
    try:
        acc_mod.update_account(ns.account_id, **kwargs)
        print(f"Account '{ns.account_id}' updated.")
    except ValueError as e:
        _die(str(e))


USAGE = """\
Usage: syk4y kaggle account <subcommand> [options]

Subcommands:
  add       Add a Kaggle account to the pool
  list      List all configured accounts
  remove    Remove an account
  enable    Enable an account
  disable   Disable an account (skip during scheduling)
  test      Validate credentials for accounts
  set       Update account settings

Examples:
  syk4y kaggle account add --id main --username user1 --key xxxxx
  syk4y kaggle account add --id alt1 --from-json ~/alt1.json
  syk4y kaggle account list
  syk4y kaggle account test
  syk4y kaggle account disable alt1
  syk4y kaggle account set main --max-concurrent-gpu 2
"""


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help", "help"):
        print(USAGE)
        return

    subcmd = args[0]
    rest = args[1:]

    if subcmd == "add":
        cmd_add(rest)
    elif subcmd == "list":
        cmd_list(rest)
    elif subcmd == "remove":
        cmd_remove(rest)
    elif subcmd == "enable":
        cmd_enable(rest, enabled=True)
    elif subcmd == "disable":
        cmd_enable(rest, enabled=False)
    elif subcmd == "test":
        cmd_test(rest)
    elif subcmd == "set":
        cmd_set(rest)
    else:
        print(f"Unknown subcommand: {subcmd}", file=sys.stderr)
        print(USAGE, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
