#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
QUEUE_FILE = PROJECT_ROOT / "config" / "task_queue.jsonl"
STATUS_FILE = PROJECT_ROOT / "config" / "task_status.jsonl"


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    items: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            items.append(data)
    return items


def fmt_ts(ts_value) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ts_value)))
    except Exception:
        return "?"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tail", type=int, default=5, help="显示最近多少个任务")
    args = parser.parse_args()

    queue_items = load_jsonl(QUEUE_FILE)
    status_items = load_jsonl(STATUS_FILE)

    if not queue_items:
        print("暂无派单记录。")
        return 0

    latest_tasks = queue_items[-max(1, args.tail) :]
    status_by_task: dict[str, list[dict]] = {}
    for item in status_items:
        task_id = str(item.get("task_id") or "")
        status_by_task.setdefault(task_id, []).append(item)

    for task in latest_tasks:
        task_id = str(task.get("task_id") or "?")
        print("=" * 80)
        print(f"任务ID   : {task_id}")
        print(f"时间     : {fmt_ts(task.get('ts'))}")
        print(f"动作     : {task.get('action')}")
        print(f"载荷     : {json.dumps(task.get('payload', {}), ensure_ascii=False)}")
        print(f"队列状态 : {task.get('status')}")

        task_statuses = status_by_task.get(task_id, [])
        if not task_statuses:
            print("状态流   : <无>")
            continue

        print("状态流   :")
        for status in task_statuses[-10:]:
            detail = str(status.get("detail") or "").strip()
            line = f"  - [{fmt_ts(status.get('ts'))}] {status.get('status')}"
            if detail:
                line += f" | {detail}"
            print(line)

    print("=" * 80)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
