#!/usr/bin/env python3
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

ACCOUNTS_DIR = Path.home() / ".syk4y"
ACCOUNTS_FILE = ACCOUNTS_DIR / "accounts.json"


def _load_raw() -> dict:
    if not ACCOUNTS_FILE.exists():
        return {"accounts": []}
    try:
        return json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"accounts": []}


def _save_raw(data: dict) -> None:
    ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)
    ACCOUNTS_FILE.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    ACCOUNTS_FILE.chmod(0o600)


def load_accounts() -> list[dict]:
    return _load_raw().get("accounts", [])


def save_accounts(accounts: list[dict]) -> None:
    _save_raw({"accounts": accounts})


def get_account(account_id: str) -> Optional[dict]:
    for acc in load_accounts():
        if acc["id"] == account_id:
            return acc
    return None


def add_account(
    account_id: str,
    username: str,
    key: str,
    max_concurrent_gpu: int = 2,
    weekly_gpu_limit_hours: int = 30,
    enabled: bool = True,
) -> dict:
    accounts = load_accounts()
    for acc in accounts:
        if acc["id"] == account_id:
            raise ValueError(f"Account '{account_id}' already exists. Use --force to overwrite.")
    entry = {
        "id": account_id,
        "username": username,
        "key": key,
        "max_concurrent_gpu": max_concurrent_gpu,
        "weekly_gpu_limit_hours": weekly_gpu_limit_hours,
        "enabled": enabled,
    }
    accounts.append(entry)
    save_accounts(accounts)
    return entry


def update_account(account_id: str, **kwargs) -> dict:
    accounts = load_accounts()
    for i, acc in enumerate(accounts):
        if acc["id"] == account_id:
            acc.update(kwargs)
            accounts[i] = acc
            save_accounts(accounts)
            return acc
    raise ValueError(f"Account '{account_id}' not found.")


def remove_account(account_id: str) -> None:
    accounts = load_accounts()
    new = [a for a in accounts if a["id"] != account_id]
    if len(new) == len(accounts):
        raise ValueError(f"Account '{account_id}' not found.")
    save_accounts(new)


def validate_account(acc: dict) -> tuple[bool, str]:
    env = {**os.environ, "KAGGLE_USERNAME": acc["username"], "KAGGLE_KEY": acc["key"]}
    try:
        result = subprocess.run(
            ["kaggle", "datasets", "list", "-s", "test", "-p", "1"],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return True, "OK"
        return False, result.stderr.strip() or "Unknown error"
    except FileNotFoundError:
        return False, "kaggle CLI not found"
    except subprocess.TimeoutExpired:
        return False, "Timeout"
    except Exception as e:
        return False, str(e)


def get_enabled_accounts() -> list[dict]:
    return [a for a in load_accounts() if a.get("enabled", True)]
