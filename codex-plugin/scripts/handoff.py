#!/usr/bin/env python3
"""Resume the original Codex thread after a DSH handoff."""

import argparse
import json
import logging
import time

from appserver import AppServer, AppServerError
from bridge import STATE_ROOT, mark_task, pending_handoffs, rejected_tasks

LOG = logging.getLogger("dsh-codex-bridge")
ENABLED = STATE_ROOT / "auto_resume_enabled"


def last_turn_state(thread):
    turns = thread.get("turns", [])
    return turns[-1].get("status") if turns else None


def delivered_in_thread(thread, marker):
    for turn in thread.get("turns", []):
        for item in turn.get("items", []):
            if marker in json.dumps(item, ensure_ascii=False):
                return True
    return False


def thread_idle(thread):
    status = thread.get("status", {})
    if status.get("type") == "active" or last_turn_state(thread) == "inProgress":
        return False
    return True


def resume_one(client, record, state, result_path, status_path, dry_run=False):
    task_id = record["task_id"]
    thread_id = record["codex_thread_id"]
    marker = f"DSH_HANDOFF:{task_id}:{state}"
    thread = client.request("thread/read", {"threadId": thread_id, "includeTurns": True})["thread"]
    if delivered_in_thread(thread, marker):
        mark_task(task_id, phase="finished" if state != "waiting_for_approval" else "armed", last_signal=state)
        return "already-delivered"
    if not thread_idle(thread):
        return "thread-busy"
    if dry_run:
        return "ready"
    if thread.get("status", {}).get("type") == "notLoaded":
        client.request("thread/resume", {"threadId": thread_id})
    prompt = (
        f"{marker}\nDSH 任务 {task_id} 的状态已变为 {state}。"
        f"请读取 {status_path}"
        + (f" 和 {result_path}" if state != "waiting_for_approval" and result_path.is_file() else "")
        + "；核对工作区中的实际变更并按原用户目标继续。"
        + "若在等待批准，只向用户说明 DSH 界面需要处理的事项，不要替人批准或重新派同一任务。"
        + "若用户中途打断了 DSH，先核实其最新意图。"
    )
    mark_task(task_id, phase="delivering", delivery_marker=marker)
    try:
        client.request("turn/start", {"threadId": thread_id, "input": [{"type": "text", "text": prompt}]})
    except Exception:
        # A timeout may still have started the turn. The marker in thread/read
        # reconciles that case on the next scan.
        raise
    mark_task(task_id, phase="finished" if state != "waiting_for_approval" else "armed", last_signal=state)
    return "delivered"


def scan(dry_run=False):
    outcomes = []
    for record, path in rejected_tasks():
        rejection = json.loads(path.read_text(encoding="utf-8"))
        if not dry_run:
            mark_task(record["task_id"], phase="abandoned", rejection=rejection)
        outcomes.append({"task_id": record["task_id"], "outcome": "rejected", "reason": rejection.get("reason")})
    pending = list(pending_handoffs())
    if not pending:
        return outcomes
    with AppServer() as client:
        for record, state, result_path, status_path in pending:
            try:
                outcome = resume_one(client, record, state, result_path, status_path, dry_run)
            except (AppServerError, OSError, KeyError, ValueError) as exc:
                LOG.error("task %s: %s", record["task_id"], exc)
                outcome = "error"
            outcomes.append({"task_id": record["task_id"], "state": state, "outcome": outcome})
    return outcomes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["probe", "once", "watch"])
    parser.add_argument("--interval", type=float, default=3)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.command == "probe":
        with AppServer():
            pass
        print("app-server connected")
        return 0
    if args.command == "once":
        print(json.dumps(scan(dry_run=not ENABLED.exists()), ensure_ascii=False))
        return 0
    while True:
        if ENABLED.exists():
            try:
                for item in scan():
                    if item["outcome"] not in {"thread-busy"}:
                        LOG.info("%s", item)
            except Exception:
                LOG.exception("handoff scan failed")
        time.sleep(max(1, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
