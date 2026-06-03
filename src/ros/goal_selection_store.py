"""Thread-safe selected-goal storage shared by Web UI and MCP tools."""
from __future__ import annotations

import threading
import time
from typing import Any, Optional


class GoalSelectionStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._selected: Optional[dict[str, Any]] = None

    async def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        selected = dict(payload)
        selected["interrupt_mode"] = 0
        selected["dwell_time"] = 0.0
        selected["yaw_deg"] = -1.0
        selected["updated_at"] = float(selected.get("updated_at") or time.time())
        with self._lock:
            self._selected = selected
        return {"configured": True, "published": False, "reason": "awaiting_dispatch"}

    def latest(self) -> Optional[dict[str, Any]]:
        with self._lock:
            return dict(self._selected) if self._selected is not None else None


_store: Optional[GoalSelectionStore] = None


def get_goal_selection_store() -> GoalSelectionStore:
    global _store
    if _store is None:
        _store = GoalSelectionStore()
    return _store
