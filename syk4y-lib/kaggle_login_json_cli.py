import json
import sys
from pathlib import Path


def cmd_status(path: Path) -> int:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        print("INVALID\t")
        return 0

    username = data.get("username")
    key = data.get("key")
    if (
        isinstance(username, str)
        and username.strip()
        and isinstance(key, str)
        and key.strip()
    ):
        print(f"OK\t{username.strip()}")
    else:
        print("INVALID\t")
    return 0


def cmd_write(path: Path, username: str, key: str) -> int:
    path.write_text(
        json.dumps({"username": username, "key": key}, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


def main() -> int:
    if len(sys.argv) < 3:
        print(
            "Usage: kaggle_login_json_cli.py <status|write> <path> [username] [key]",
            file=sys.stderr,
        )
        return 2

    command = sys.argv[1]
    path = Path(sys.argv[2])

    if command == "status":
        return cmd_status(path)
    if command == "write":
        if len(sys.argv) != 5:
            print(
                "Usage: kaggle_login_json_cli.py write <path> <username> <key>",
                file=sys.stderr,
            )
            return 2
        return cmd_write(path, sys.argv[3], sys.argv[4])

    print(f"Unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
