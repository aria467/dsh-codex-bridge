"""Arm submitted DSH tasks when their Codex turn finishes."""

import json
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from bridge import arm_thread  # noqa: E402


def main():
    try:
        event = json.load(sys.stdin)
        thread_id = event.get("session_id")
        if isinstance(thread_id, str) and thread_id:
            arm_thread(thread_id)
    except Exception as exc:
        print(f"dsh-codex-bridge Stop hook: {exc}", file=sys.stderr)
    print("{}")


if __name__ == "__main__":
    main()
