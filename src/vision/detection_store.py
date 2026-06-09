"""Thread-safe detection result store shared by VisionPlugin and MCP tools."""
from __future__ import annotations

import threading
import time
from typing import Any, Optional


class DetectionStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: Optional[dict[str, Any]] = None

    def submit(self, result: dict[str, Any]) -> None:
        result["updated_at"] = time.time()
        with self._lock:
            self._latest = dict(result)

    def latest(self) -> Optional[dict[str, Any]]:
        with self._lock:
            return dict(self._latest) if self._latest is not None else None

    def clear(self) -> None:
        with self._lock:
            self._latest = None


_store: Optional[DetectionStore] = None


def get_detection_store() -> DetectionStore:
    global _store
    if _store is None:
        _store = DetectionStore()
    return _store
