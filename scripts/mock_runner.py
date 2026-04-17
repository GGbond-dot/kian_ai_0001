import json, time, sys
from pathlib import Path

STATUS = Path("config/task_status.jsonl")

def append_status(task_id, status, detail=""):
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": time.time(), "task_id": task_id, "status": status, "detail": detail}
    with STATUS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

if __name__ == "__main__":
    task_id = sys.argv[1] if len(sys.argv) > 1 else "unknown"
    append_status(task_id, "running", "开始执行（mock）")
    time.sleep(2)
    append_status(task_id, "running", "执行中（mock）")
    time.sleep(2)
    append_status(task_id, "success", "执行完成（mock）")
