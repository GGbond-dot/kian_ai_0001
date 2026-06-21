"""Thread-safe detection result store shared by VisionPlugin and MCP tools.

多机改造:检测结果按 drone_key 区分。未带 key 的提交存为全局结果(单机)。
latest(key) 优先返回该机结果,无则回退最近一次全局结果 → 保持单机旧行为。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Optional


class DetectionStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest_by_key: dict[str, dict[str, Any]] = {}
        self._latest_global: Optional[dict[str, Any]] = None

    def submit(self, result: dict[str, Any], drone_key: Optional[str] = None) -> None:
        result = dict(result)
        result["updated_at"] = time.time()
        if drone_key:
            result["drone_key"] = drone_key
        with self._lock:
            if drone_key:
                self._latest_by_key[drone_key] = result
            else:
                self._latest_global = result

    def latest(self, drone_key: Optional[str] = None) -> Optional[dict[str, Any]]:
        with self._lock:
            if drone_key and drone_key in self._latest_by_key:
                return dict(self._latest_by_key[drone_key])
            return dict(self._latest_global) if self._latest_global is not None else None

    def clear(self, drone_key: Optional[str] = None) -> None:
        with self._lock:
            if drone_key:
                self._latest_by_key.pop(drone_key, None)
            else:
                self._latest_global = None


_store: Optional[DetectionStore] = None


def get_detection_store() -> DetectionStore:
    global _store
    if _store is None:
        _store = DetectionStore()
    return _store
