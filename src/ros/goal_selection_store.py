"""Thread-safe selected-goal storage shared by Web UI and MCP tools.

多机改造:框选目标可按 drone_key 区分。未带 key 的提交存为全局点(单机/未点名)。
latest(key) 优先返回该机专属点,无则回退到最近一次全局点 → 保持单机旧行为。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Optional


class GoalSelectionStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._selected_by_key: dict[str, dict[str, Any]] = {}
        self._latest_global: Optional[dict[str, Any]] = None

    async def submit(self, payload: dict[str, Any], drone_key: Optional[str] = None) -> dict[str, Any]:
        selected = dict(payload)
        selected["interrupt_mode"] = 0
        selected["dwell_time"] = 0.0
        selected["yaw_deg"] = -1.0
        selected["updated_at"] = float(selected.get("updated_at") or time.time())
        if drone_key:
            selected["drone_key"] = drone_key
        with self._lock:
            if drone_key:
                self._selected_by_key[drone_key] = selected
            else:
                self._latest_global = selected
        return {"configured": True, "published": False, "reason": "awaiting_dispatch"}

    def latest(self, drone_key: Optional[str] = None) -> Optional[dict[str, Any]]:
        with self._lock:
            if drone_key and drone_key in self._selected_by_key:
                return dict(self._selected_by_key[drone_key])
            return dict(self._latest_global) if self._latest_global is not None else None

    def clear(self, drone_key: Optional[str] = None) -> None:
        with self._lock:
            if drone_key:
                self._selected_by_key.pop(drone_key, None)
            else:
                self._latest_global = None


_store: Optional[GoalSelectionStore] = None


def get_goal_selection_store() -> GoalSelectionStore:
    global _store
    if _store is None:
        _store = GoalSelectionStore()
    return _store
