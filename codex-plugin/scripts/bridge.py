#!/usr/bin/env python3
"""Codex-side file bridge. No DSH API, credentials, or project writes."""

import argparse
import contextlib
import fcntl
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path


BRIDGE_ROOT = Path(os.environ.get("DSH_BRIDGE_ROOT", "~/.dsh-bridge")).expanduser()
STATE_ROOT = Path(os.environ.get("DSH_CODEX_STATE_ROOT", "~/.local/state/dsh-codex-bridge")).expanduser()
TASK_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
TERMINAL = {"completed", "failed", "cancelled", "interrupted_by_user", "orphaned"}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def read_json(path):
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


@contextlib.contextmanager
def state_lock():
    STATE_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(STATE_ROOT / "lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def task_path(task_id):
    if not TASK_RE.fullmatch(task_id) or task_id.startswith("."):
        raise ValueError("invalid task_id")
    return STATE_ROOT / "tasks" / f"{task_id}.json"


def task_records():
    return [read_json(path) for path in sorted((STATE_ROOT / "tasks").glob("*.json"))]


def arm_thread(thread_id):
    with state_lock():
        for record in task_records():
            if record["codex_thread_id"] == thread_id and record["phase"] == "submitted":
                record["phase"] = "armed"
                write_json(task_path(record["task_id"]), record)


def submit(args):
    thread_id = os.environ.get("CODEX_THREAD_ID") or os.environ.get("CODEX_SESSION_ID")
    if not thread_id:
        raise ValueError("CODEX_THREAD_ID is unavailable; run from a Codex tool call")
    workspace = Path(args.workspace).expanduser().resolve(strict=True)
    if not workspace.is_dir():
        raise ValueError("workspace must be an existing directory")
    capabilities = read_json(BRIDGE_ROOT / "capabilities.json")
    allowed = {item["value"] for item in capabilities["permissionPresets"]}
    if args.permission_preset not in allowed:
        raise ValueError(f"unknown permission preset; choose from {sorted(allowed)}")
    if args.agent_preset:
        agent_presets = {item["id"] for item in capabilities["agentPresets"]}
        if args.agent_preset not in agent_presets:
            raise ValueError(f"unknown agent preset; choose from {sorted(agent_presets)}")
        if args.dsh_session_id:
            raise ValueError("agent preset can only be selected when creating a new DSH session")
    prompt = sys.stdin.read() if args.prompt_file == "-" else Path(args.prompt_file).read_text(encoding="utf-8")
    if not prompt.strip():
        raise ValueError("prompt is empty")
    task_id = args.task_id or f"codex-{uuid.uuid4().hex}"
    state_file = task_path(task_id)
    envelope_file = BRIDGE_ROOT / "inbox" / f"{task_id}.json"
    envelope = {
        "task_id": task_id,
        "workspace": str(workspace),
        "prompt": prompt,
        "permission_preset": args.permission_preset,
    }
    if args.dsh_session_id:
        envelope["session_id"] = args.dsh_session_id
    if args.agent_preset:
        envelope["agent_preset"] = args.agent_preset
    with state_lock():
        if state_file.exists() or envelope_file.exists() or (BRIDGE_ROOT / "handoffs" / f"{task_id}.json").exists():
            raise ValueError("task_id is already in use")
        for existing in task_records():
            if existing["workspace"] == str(workspace) and existing["phase"] not in {"finished", "abandoned"}:
                raise ValueError(f"workspace already has task {existing['task_id']}")
        record = {
            "task_id": task_id,
            "codex_thread_id": thread_id,
            "workspace": str(workspace),
            "phase": "prepared",
            "last_signal": None,
            "created_at": time.time(),
        }
        write_json(state_file, record)
        # The final rename is the point at which DSH can see a complete envelope.
        write_json(envelope_file, envelope)
        record["phase"] = "submitted"
        write_json(state_file, record)
    print(json.dumps({"task_id": task_id, "codex_thread_id": thread_id, "inbox": str(envelope_file)}, ensure_ascii=False))


def pending_handoffs():
    with state_lock():
        records = task_records()
    for record in records:
        if record["phase"] not in {"submitted", "armed", "delivering"}:
            continue
        handoff_file = BRIDGE_ROOT / "handoffs" / f"{record['task_id']}.json"
        if not handoff_file.is_file():
            continue
        handoff = read_json(handoff_file)
        if handoff.get("task_id") != record["task_id"]:
            continue
        status_file = BRIDGE_ROOT / "outbox" / f"{record['task_id']}.status.json"
        if Path(handoff.get("status_path", "")) != status_file:
            continue
        status = read_json(status_file)
        state = status.get("state")
        if state != handoff.get("terminal_state"):
            continue
        if state == record.get("last_signal"):
            continue
        result_file = BRIDGE_ROOT / "outbox" / f"{record['task_id']}.result.md"
        if handoff.get("signal") == "done" and Path(handoff.get("result_path", "")) != result_file:
            continue
        yield record, state, result_file, status_file


def rejected_tasks():
    with state_lock():
        records = task_records()
    for record in records:
        if record["phase"] not in {"submitted", "armed"}:
            continue
        path = BRIDGE_ROOT / "inbox" / f"{record['task_id']}.rejected"
        if path.is_file():
            yield record, path


def mark_task(task_id, **updates):
    with state_lock():
        path = task_path(task_id)
        record = read_json(path)
        record.update(updates)
        write_json(path, record)


def status(_args):
    for record in task_records():
        print(json.dumps(record, ensure_ascii=False))


def check(_args):
    if not (BRIDGE_ROOT / "capabilities.json").is_file():
        raise RuntimeError("DSH bridge capabilities.json is missing")
    caps = read_json(BRIDGE_ROOT / "capabilities.json")
    print(json.dumps({"bridge_root": str(BRIDGE_ROOT), "revision": caps.get("revision"), "pending_handoffs": [r["task_id"] for r, _, _, _ in pending_handoffs()]}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("submit")
    command.add_argument("--workspace", required=True)
    command.add_argument("--permission-preset", required=True)
    command.add_argument("--prompt-file", required=True)
    command.add_argument("--task-id")
    command.add_argument("--dsh-session-id")
    command.add_argument("--agent-preset")
    command.set_defaults(func=submit)
    sub.add_parser("status").set_defaults(func=status)
    sub.add_parser("check").set_defaults(func=check)
    args = parser.parse_args()
    try:
        args.func(args)
    except (OSError, ValueError, RuntimeError, KeyError, json.JSONDecodeError) as exc:
        print(f"dsh-codex-bridge: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
