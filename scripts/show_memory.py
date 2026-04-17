#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
except Exception:
    pass

from src.utils.config_manager import ConfigManager


def main() -> int:
    config = ConfigManager.get_instance()
    memory_file = Path(config.config_dir) / "user_memory.json"
    if not memory_file.exists():
        print(f"记忆文件不存在: {memory_file}")
        return 0

    try:
        payload = json.loads(memory_file.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"读取记忆文件失败: {exc}")
        return 1

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
