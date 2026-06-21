"""多机路径预约。

每架机下发路径时登记一条预约(整条规划路径点 + 安全半径 + 过期时间)。
其他机规划时把"非自己的、未过期的"预约路径点 + 各机当前位置 当作动态障碍喂给 planner,
由 planner 按 safety_radius 膨胀避开。任务结束(降落)主动释放;TTL 兜底防卡死。

v1:只做 2D 水平路径预约,不做时间维度速度避碰(见设计文档假设)。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class PathReservation:
    drone_key: str
    path_points: list[tuple[float, float]]
    safety_radius: float
    created_at: float
    expires_at: float


class ReservationStore:
    def __init__(self, ttl_sec: float = 120.0, safety_radius: float = 1.0) -> None:
        self.ttl_sec = float(ttl_sec)
        self.safety_radius = float(safety_radius)
        self._by_key: dict[str, PathReservation] = {}

    def reserve(self, drone_key: str, path_points: list[tuple[float, float]],
                safety_radius: Optional[float] = None) -> None:
        now = time.time()
        self._by_key[drone_key] = PathReservation(
            drone_key=drone_key,
            path_points=[(float(x), float(y)) for x, y in path_points],
            safety_radius=float(safety_radius if safety_radius is not None else self.safety_radius),
            created_at=now,
            expires_at=now + self.ttl_sec,
        )

    def release(self, drone_key: str) -> None:
        self._by_key.pop(drone_key, None)

    def _purge_expired(self) -> None:
        now = time.time()
        for k in [k for k, r in self._by_key.items() if r.expires_at < now]:
            self._by_key.pop(k, None)

    def obstacles_excluding(self, drone_key: str,
                            current_positions: dict[str, Optional[tuple[float, float]]]
                            ) -> list[tuple[float, float]]:
        """返回除 drone_key 外的其他机障碍点(各机当前位置 + 各机预约路径点)。

        已过期预约自动清除。返回的点交给 planner 按 safety_radius 膨胀。
        """
        self._purge_expired()
        points: list[tuple[float, float]] = []
        for k, r in self._by_key.items():
            if k == drone_key:
                continue
            points.extend(r.path_points)
        for k, pos in current_positions.items():
            if k == drone_key or pos is None:
                continue
            points.append((float(pos[0]), float(pos[1])))
        return points
