"""Kian-side ROS 2 global A* planner over a local offline PCD map."""
from __future__ import annotations

import asyncio
import heapq
import math
import struct
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class KianGlobalPlanner:
    def __init__(self, config: dict[str, Any], project_root: Path):
        self.config = config
        self.project_root = project_root
        self.enabled = bool(config.get("enabled", True))
        self.resolution = float(config.get("resolution", 0.6))
        self.inflation_radius = float(config.get("inflation_radius", 0.35))
        self.planning_z = float(config.get("planning_z", 0.5))
        self.path_min_spacing = float(config.get("path_min_spacing", 0.5))
        self.map_margin = float(config.get("map_margin", 2.0))
        self.obstacle_min_z = float(config.get("obstacle_min_z", -1.0))
        self.obstacle_max_z = float(config.get("obstacle_max_z", 3.0))
        self.enable_ray_clearing = bool(config.get("enable_ray_clearing", True))
        self.clearing_ttl = float(config.get("clearing_ttl", 2.0))
        self.ray_step = float(config.get("ray_step", 0.15))
        self.hit_keep_radius = float(config.get("hit_keep_radius", 0.45))
        namespace = str(config.get("namespace", "a")).strip("/")
        drone_id = str(config.get("drone_id", "0"))
        self.drone_key = str(config.get("drone_key") or f"{namespace}{drone_id}")
        prefix = f"/{namespace}/" if namespace else "/"
        self.odom_topic = f"{prefix}drone_{drone_id}_Odometry_world"
        self.cloud_topic = f"{prefix}drone_{drone_id}_cloud_registered_world"
        self.goal_topic = str(config.get("goal_topic", "/goal_with_type"))
        self.path_topic = f"{prefix}drone_{drone_id}_planning/global_path"
        self.world_frame = str(config.get("world_frame", "world"))

        pcd_path = Path(str(config.get("pcd_path", "maps/global_map_ds.pcd")))
        self.pcd_path = pcd_path if pcd_path.is_absolute() else project_root / pcd_path
        self._lock = threading.Lock()
        self._node = None
        self._goal_pub = None
        self._path_pub = None
        self._goal_msg_cls = None
        self._path_msg_cls = None
        self._pose_cls = None
        self._latest_odom: Optional[tuple[float, float]] = None
        self._last_goal: Optional[tuple[float, float]] = None
        self._latest_live_points = np.zeros((0, 3), dtype=np.float32)
        self._last_cloud_updated_at: Optional[float] = None
        self._last_ray_cleared_cells = 0
        self._last_plan: Optional[dict[str, Any]] = None
        self._path_callback = None
        self._takeoff_position: Optional[tuple[float, float]] = None
        self._monitoring_goal: Optional[tuple[float, float]] = None
        self._monitoring_goal_type: int = 0
        self.completion_threshold = float(config.get("completion_threshold", 0.5))
        # 多机防撞:其他无人机位置 / 预约路径走廊的膨胀半径(米)
        self.safety_radius = float(config.get("safety_radius", 1.0))
        self._last_path_points: list[tuple[float, float]] = []
        self._completion_callback: Optional[Callable] = None
        # 放物到达后是否由 planner 自带逻辑触发返航降落。
        # 单机默认 True;多机编排器接管时置 False(由 coordinator 决定回区还是降落)。
        self._auto_land_enabled = True
        self._landing_triggered = False
        self._pending_landing: Optional[tuple[float, float]] = None
        self._static_points = self._load_pcd_xyz(self.pcd_path)
        self._initialize_grid(self._static_points)

    @property
    def available(self) -> bool:
        return self._node is not None and self._goal_pub is not None and self._path_pub is not None

    def attach_ros(self, node: Any) -> None:
        from drone_task_interfaces.msg import GlobalPathWithGoal, GoalWithType
        from nav_msgs.msg import Odometry
        from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
        from sensor_msgs.msg import PointCloud2

        self._node = node
        self._goal_msg_cls = GoalWithType
        self._path_msg_cls = GlobalPathWithGoal
        self._goal_pub = node.create_publisher(GoalWithType, self.goal_topic, 10)
        self._path_pub = node.create_publisher(GlobalPathWithGoal, self.path_topic, 1)
        qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=5,
        )
        node.create_subscription(Odometry, self.odom_topic, self._on_odom, qos)
        node.create_subscription(PointCloud2, self.cloud_topic, self._on_cloud, qos)
        logger.info("KianGlobalPlanner: ROS attached odom=%s cloud=%s path=%s",
                    self.odom_topic, self.cloud_topic, self.path_topic)

    def dispatch_selected(self, selected: dict[str, Any], goal_type: int,
                          external_obstacles: Optional[list[tuple[float, float]]] = None) -> dict[str, Any]:
        if goal_type not in (0, 1, 2, 3):
            raise ValueError("goal_type must be one of 0(normal), 1(pickup), 2(place), 3(land)")
        if not self.available:
            raise RuntimeError("ROS terminal planner is unavailable")
        msg = self._goal_msg_cls()
        stamp = self._node.get_clock().now().to_msg()
        msg.header.stamp = stamp
        msg.header.frame_id = self.world_frame
        msg.goal.header = msg.header
        msg.goal.pose.position.x = float(selected["cx"])
        msg.goal.pose.position.y = float(selected["cy"])
        msg.goal.pose.position.z = self.planning_z
        msg.goal.pose.orientation.w = 1.0
        msg.goal_type = int(goal_type)
        msg.dwell_time = 0.0
        msg.yaw_deg = -1.0
        msg.interrupt_mode = 0
        self._goal_pub.publish(msg)
        return self.plan_and_publish(msg, external_obstacles=external_obstacles)

    def plan_and_publish(self, goal_msg: Any,
                         external_obstacles: Optional[list[tuple[float, float]]] = None) -> dict[str, Any]:
        with self._lock:
            if self._latest_odom is None:
                raise RuntimeError("world odometry is not ready")
            start = self._last_goal if self._last_goal is not None else self._latest_odom
            goal = (float(goal_msg.goal.pose.position.x), float(goal_msg.goal.pose.position.y))
            zones = self._active_no_fly_zones()
            # 目标点落在生效的禁飞区内 → 拒绝下发（不能把目标"挪"到禁区外去飞）
            if self._point_in_no_fly(goal[0], goal[1], zones):
                raise RuntimeError(
                    f"目标点 ({goal[0]:.2f}, {goal[1]:.2f}) 落在禁飞区内,已拒绝下发"
                )
            occupancy = self._build_occupancy(
                self._latest_live_points, zones=zones, external_obstacles=external_obstacles
            )
            start_cell = self._adjust_to_free(self._world_to_grid(*start), occupancy)
            goal_cell = self._adjust_to_free(self._world_to_grid(*goal), occupancy)
            cells = self._astar(start_cell, goal_cell, occupancy)
            points = self._decimate([self._grid_to_world(*cell) for cell in cells])
            self._last_goal = goal
            self._last_path_points = list(points)

        path_msg = self._path_msg_cls()
        path_msg.header = goal_msg.header
        path_msg.path.header = goal_msg.header
        path_msg.goal_type = int(goal_msg.goal_type)
        path_msg.dwell_time = 0.0
        path_msg.yaw_deg = -1.0
        path_msg.interrupt_mode = 0
        from geometry_msgs.msg import PoseStamped
        for x, y in points:
            pose = PoseStamped()
            pose.header = goal_msg.header
            pose.pose.position.x = x
            pose.pose.position.y = y
            pose.pose.position.z = self.planning_z
            pose.pose.orientation.w = 1.0
            path_msg.path.poses.append(pose)
        self._path_pub.publish(path_msg)
        if self._path_callback is not None:
            rendered = self._path_callback(points, self.planning_z)
            if asyncio.iscoroutine(rendered):
                asyncio.create_task(rendered)
        self._last_plan = {
            "drone_key": self.drone_key,
            "status": "published", "waypoints": len(points), "goal_type": int(goal_msg.goal_type),
            "goal": {"x": goal[0], "y": goal[1]}, "topic": self.path_topic, "updated_at": time.time(),
        }
        logger.info("KianGlobalPlanner: published %d waypoints type=%d", len(points), goal_msg.goal_type)
        if self._auto_land_enabled and int(goal_msg.goal_type) == 2 and len(points) > 1:
            self._monitoring_goal = points[-1]
            self._monitoring_goal_type = 2
            logger.info("KianGlobalPlanner: monitoring PLACE goal (%.2f, %.2f)", points[-1][0], points[-1][1])
        return dict(self._last_plan)

    def set_path_callback(self, callback: Any) -> None:
        self._path_callback = callback

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "drone_key": self.drone_key,
                "enabled": self.enabled, "available": self.available, "map_ready": True,
                "pcd_path": str(self.pcd_path), "odom_ready": self._latest_odom is not None,
                "path_topic": self.path_topic, "last_plan": self._last_plan,
                "live_cloud_points": len(self._latest_live_points),
                "last_cloud_updated_at": self._last_cloud_updated_at,
                "ray_clearing_enabled": self.enable_ray_clearing,
                "ray_cleared_cells": self._last_ray_cleared_cells,
                "clearing_ttl": self.clearing_ttl,
            }

    def set_odometry_for_test(self, x: float, y: float) -> None:
        with self._lock:
            self._latest_odom = (float(x), float(y))

    def _on_odom(self, msg: Any) -> None:
        p = msg.pose.pose.position
        x, y = float(p.x), float(p.y)
        with self._lock:
            self._latest_odom = (x, y)
            if self._takeoff_position is None:
                self._takeoff_position = (x, y)
                logger.info("KianGlobalPlanner: takeoff position saved (%.2f, %.2f)", x, y)
            if self._monitoring_goal is not None:
                dist = math.hypot(x - self._monitoring_goal[0], y - self._monitoring_goal[1])
                if dist < self.completion_threshold:
                    self._pending_landing = self._monitoring_goal
                    self._monitoring_goal = None
                    logger.info("KianGlobalPlanner: goal reached, dist=%.2f, pending landing", dist)

    def _trigger_landing(self, completed_goal: tuple[float, float]) -> None:
        if self._takeoff_position is None or self._landing_triggered:
            return
        if not self.available:
            logger.error("KianGlobalPlanner: cannot auto-land, planner unavailable")
            return
        self._landing_triggered = True
        msg = self._goal_msg_cls()
        stamp = self._node.get_clock().now().to_msg()
        msg.header.stamp = stamp
        msg.header.frame_id = self.world_frame
        msg.goal.header = msg.header
        msg.goal.pose.position.x = float(self._takeoff_position[0])
        msg.goal.pose.position.y = float(self._takeoff_position[1])
        msg.goal.pose.position.z = self.planning_z
        msg.goal.pose.orientation.w = 1.0
        msg.goal_type = 3
        msg.dwell_time = 0.0
        msg.yaw_deg = -1.0
        msg.interrupt_mode = 0
        self._goal_pub.publish(msg)
        result = self.plan_and_publish(msg)
        logger.info("KianGlobalPlanner: auto LAND dispatched, takeoff=(%.2f, %.2f)",
                    self._takeoff_position[0], self._takeoff_position[1])
        if self._completion_callback is not None:
            try:
                self._completion_callback(result)
            except Exception as exc:
                logger.error("KianGlobalPlanner: completion callback failed: %s", exc)

    def set_completion_callback(self, callback: Callable) -> None:
        self._completion_callback = callback

    def set_auto_land(self, enabled: bool) -> None:
        """开/关 planner 自带的"放物到达→自动返航降落"。多机编排器接管时关闭。"""
        self._auto_land_enabled = bool(enabled)

    def latest_odom(self) -> Optional[tuple[float, float]]:
        """线程安全读取最新里程计 (x, y);无数据返回 None。供编排器轮询。"""
        with self._lock:
            return self._latest_odom

    def poll_pending_landing(self) -> Optional[tuple[float, float]]:
        """取出待处理的 landing 目标（线程安全）。由 asyncio 侧轮询调用。"""
        with self._lock:
            goal = self._pending_landing
            self._pending_landing = None
            return goal

    def trigger_landing(self, completed_goal: tuple[float, float]) -> None:
        """从 asyncio 线程调用，执行 landing 规划与发布。"""
        self._trigger_landing(completed_goal)

    def _on_cloud(self, msg: Any) -> None:
        try:
            from sensor_msgs_py import point_cloud2
            xyz = point_cloud2.read_points_numpy(msg, field_names=("x", "y", "z"), skip_nans=True)
            self.update_live_points(xyz)
        except Exception as exc:
            logger.warning("KianGlobalPlanner: live PointCloud2 parse failed: %s", exc)

    def update_live_points(self, points: np.ndarray, now_s: Optional[float] = None) -> None:
        xyz = np.asarray(points, dtype=np.float32)
        if xyz.size == 0:
            xyz = np.zeros((0, 3), dtype=np.float32)
        else:
            xyz = xyz.reshape((-1, 3))
            xyz = xyz[np.isfinite(xyz).all(axis=1)]
        updated_at = time.time() if now_s is None else float(now_s)
        with self._lock:
            self._latest_live_points = xyz
            self._last_cloud_updated_at = updated_at
            self._last_ray_cleared_cells = self._apply_ray_clearing(xyz, updated_at)

    def _initialize_grid(self, points: np.ndarray) -> None:
        if points.size == 0:
            raise ValueError(f"PCD map is empty: {self.pcd_path}")
        self.min_x = float(points[:, 0].min()) - self.map_margin
        self.max_x = float(points[:, 0].max()) + self.map_margin
        self.min_y = float(points[:, 1].min()) - self.map_margin
        self.max_y = float(points[:, 1].max()) + self.map_margin
        self.width = max(1, int(math.ceil((self.max_x - self.min_x) / self.resolution)))
        self.height = max(1, int(math.ceil((self.max_y - self.min_y) / self.resolution)))
        self._static_occupancy = self._occupancy_for_points(points)
        self._cleared_until = np.zeros((self.height, self.width), dtype=np.float64)
        logger.info("KianGlobalPlanner: loaded map=%s points=%d grid=%dx%d",
                    self.pcd_path, len(points), self.width, self.height)

    def _build_occupancy(self, live_points: np.ndarray, now_s: Optional[float] = None,
                         zones: Optional[list[dict[str, Any]]] = None,
                         external_obstacles: Optional[list[tuple[float, float]]] = None) -> np.ndarray:
        occupancy = self._static_occupancy.copy()
        if self.enable_ray_clearing:
            current_time = time.time() if now_s is None else float(now_s)
            occupancy &= self._cleared_until <= current_time
        if live_points.size:
            occupancy |= self._occupancy_for_points(live_points)
        # 禁飞区：把生效禁区(z 区间覆盖 planning_z)的矩形格子标占用,A* 自动绕开。
        # 禁飞区是用户硬约束,放在 ray_clearing 之后,不被点云清除覆盖掉。
        if zones is None:
            zones = self._active_no_fly_zones()
        if zones:
            occupancy |= self._no_fly_occupancy(zones)
        # 多机防撞：其他无人机当前位置 + 已预约路径走廊,按 safety_radius 膨胀为占用。
        if external_obstacles:
            occupancy |= self._occupancy_for_xy(external_obstacles, self.safety_radius)
        return occupancy

    def _apply_ray_clearing(self, points: np.ndarray, now_s: float) -> int:
        if not self.enable_ray_clearing or self._latest_odom is None or points.size == 0:
            return 0
        origin_x, origin_y = self._latest_odom
        clear_until = now_s + max(0.1, self.clearing_ttl)
        step = max(0.05, self.ray_step)
        cleared_cells = 0
        obstacle_points = points[
            (points[:, 2] >= self.obstacle_min_z) & (points[:, 2] <= self.obstacle_max_z)
        ]
        for hit_x, hit_y, _ in obstacle_points:
            ray_x = float(hit_x) - origin_x
            ray_y = float(hit_y) - origin_y
            ray_length = math.hypot(ray_x, ray_y)
            clear_length = ray_length - max(0.0, self.hit_keep_radius)
            if clear_length <= self.resolution:
                continue
            direction_x = ray_x / ray_length
            direction_y = ray_y / ray_length
            distance = 0.0
            while distance <= clear_length:
                gx = int(math.floor((origin_x + direction_x * distance - self.min_x) / self.resolution))
                gy = int(math.floor((origin_y + direction_y * distance - self.min_y) / self.resolution))
                if self._in_bounds(gx, gy):
                    if self._static_occupancy[gy, gx] and self._cleared_until[gy, gx] < clear_until:
                        cleared_cells += 1
                    self._cleared_until[gy, gx] = clear_until
                distance += step
        return cleared_cells

    def _occupancy_for_points(self, points: np.ndarray) -> np.ndarray:
        occupancy = np.zeros((self.height, self.width), dtype=bool)
        if points.size == 0:
            return occupancy
        points = points[(points[:, 2] >= self.obstacle_min_z) & (points[:, 2] <= self.obstacle_max_z)]
        gx = np.floor((points[:, 0] - self.min_x) / self.resolution).astype(int)
        gy = np.floor((points[:, 1] - self.min_y) / self.resolution).astype(int)
        valid = (gx >= 0) & (gy >= 0) & (gx < self.width) & (gy < self.height)
        radius = int(math.ceil(self.inflation_radius / self.resolution))
        for x, y in zip(gx[valid], gy[valid]):
            for dx in range(-radius, radius + 1):
                for dy in range(-radius, radius + 1):
                    if dx * dx + dy * dy <= radius * radius:
                        nx, ny = x + dx, y + dy
                        if 0 <= nx < self.width and 0 <= ny < self.height:
                            occupancy[ny, nx] = True
        return occupancy

    def _occupancy_for_xy(self, xy_points: list[tuple[float, float]], radius_m: float) -> np.ndarray:
        """把一组 (x, y) 平面点按 radius_m 膨胀成占用栅格(多机防撞用,无 z 过滤)。"""
        occupancy = np.zeros((self.height, self.width), dtype=bool)
        radius = int(math.ceil(max(0.0, radius_m) / self.resolution))
        for px, py in xy_points:
            gx = int(math.floor((float(px) - self.min_x) / self.resolution))
            gy = int(math.floor((float(py) - self.min_y) / self.resolution))
            for dx in range(-radius, radius + 1):
                for dy in range(-radius, radius + 1):
                    if dx * dx + dy * dy <= radius * radius:
                        nx, ny = gx + dx, gy + dy
                        if 0 <= nx < self.width and 0 <= ny < self.height:
                            occupancy[ny, nx] = True
        return occupancy

    def last_path_points(self) -> list[tuple[float, float]]:
        """最近一次规划出的路径点(供编排器登记路径预约)。"""
        with self._lock:
            return list(self._last_path_points)

    def _active_no_fly_zones(self) -> list[dict[str, Any]]:
        """读取当前禁飞区。z 仅前端展示用,2D 规划在固定 planning_z 单层跑,
        所有框选禁区一律按 xy 生效(不再用 z 区间过滤,避免禁区因高度对不上而悄悄失效)。

        禁飞区由 Web 框选 → POST /api/noflyzone → NoFlyZoneBridge 存储,这里直接读
        bridge 单例的最新 payload,与规划器解耦(不依赖 web_server 句柄)。
        """
        try:
            from src.ros.nofly_zone_bridge import get_nofly_zone_bridge
            payload = get_nofly_zone_bridge().latest_payload()
        except Exception as exc:
            logger.warning("KianGlobalPlanner: 读取禁飞区失败: %s", exc)
            return []
        return (payload or {}).get("zones", []) or []

    def _no_fly_occupancy(self, zones: list[dict[str, Any]]) -> np.ndarray:
        """把生效禁飞区的 (x,y) 矩形范围内的格子标记为占用。"""
        occupancy = np.zeros((self.height, self.width), dtype=bool)
        for zone in zones:
            try:
                min_x = float(zone["minX"]); max_x = float(zone["maxX"])
                min_y = float(zone["minY"]); max_y = float(zone["maxY"])
            except (KeyError, TypeError, ValueError):
                continue
            if min_x > max_x:
                min_x, max_x = max_x, min_x
            if min_y > max_y:
                min_y, max_y = max_y, min_y
            gx0 = int(math.floor((min_x - self.min_x) / self.resolution))
            gx1 = int(math.floor((max_x - self.min_x) / self.resolution))
            gy0 = int(math.floor((min_y - self.min_y) / self.resolution))
            gy1 = int(math.floor((max_y - self.min_y) / self.resolution))
            gx0 = max(0, min(self.width - 1, gx0))
            gx1 = max(0, min(self.width - 1, gx1))
            gy0 = max(0, min(self.height - 1, gy0))
            gy1 = max(0, min(self.height - 1, gy1))
            occupancy[gy0:gy1 + 1, gx0:gx1 + 1] = True
        return occupancy

    def _point_in_no_fly(self, x: float, y: float, zones: list[dict[str, Any]]) -> bool:
        """判断世界坐标点 (x, y) 是否落在任一生效禁飞区矩形内。"""
        for zone in zones:
            try:
                min_x = float(zone["minX"]); max_x = float(zone["maxX"])
                min_y = float(zone["minY"]); max_y = float(zone["maxY"])
            except (KeyError, TypeError, ValueError):
                continue
            if min_x > max_x:
                min_x, max_x = max_x, min_x
            if min_y > max_y:
                min_y, max_y = max_y, min_y
            if min_x <= x <= max_x and min_y <= y <= max_y:
                return True
        return False

    def _world_to_grid(self, x: float, y: float) -> tuple[int, int]:
        cell = (int(math.floor((x - self.min_x) / self.resolution)),
                int(math.floor((y - self.min_y) / self.resolution)))
        if not self._in_bounds(*cell):
            raise ValueError(f"point outside map bounds: ({x:.3f}, {y:.3f})")
        return cell

    def _grid_to_world(self, x: int, y: int) -> tuple[float, float]:
        return (self.min_x + (x + 0.5) * self.resolution,
                self.min_y + (y + 0.5) * self.resolution)

    def _adjust_to_free(self, cell: tuple[int, int], occupancy: np.ndarray) -> tuple[int, int]:
        x, y = cell
        if not occupancy[y, x]:
            return cell
        max_radius = int(math.ceil(2.0 / self.resolution))
        for radius in range(1, max_radius + 1):
            for dx in range(-radius, radius + 1):
                for dy in range(-radius, radius + 1):
                    if abs(dx) != radius and abs(dy) != radius:
                        continue
                    nx, ny = x + dx, y + dy
                    if self._in_bounds(nx, ny) and not occupancy[ny, nx]:
                        return nx, ny
        raise RuntimeError("unable to find a free cell near start or goal")

    def _astar(self, start: tuple[int, int], goal: tuple[int, int], occupancy: np.ndarray) -> list[tuple[int, int]]:
        neighbors = ((1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1))
        open_heap = [(0.0, start)]
        costs = {start: 0.0}
        parents: dict[tuple[int, int], tuple[int, int]] = {}
        closed: set[tuple[int, int]] = set()
        while open_heap:
            _, current = heapq.heappop(open_heap)
            if current in closed:
                continue
            if current == goal:
                path = [current]
                while current in parents:
                    current = parents[current]
                    path.append(current)
                return list(reversed(path))
            closed.add(current)
            for dx, dy in neighbors:
                nxt = (current[0] + dx, current[1] + dy)
                if not self._in_bounds(*nxt) or occupancy[nxt[1], nxt[0]]:
                    continue
                new_cost = costs[current] + (math.sqrt(2.0) if dx and dy else 1.0)
                if new_cost < costs.get(nxt, math.inf):
                    costs[nxt] = new_cost
                    parents[nxt] = current
                    heapq.heappush(open_heap, (new_cost + math.hypot(nxt[0] - goal[0], nxt[1] - goal[1]), nxt))
        raise RuntimeError("global A* failed")

    def _decimate(self, points: list[tuple[float, float]]) -> list[tuple[float, float]]:
        kept: list[tuple[float, float]] = []
        for point in points:
            if not kept or math.hypot(point[0] - kept[-1][0], point[1] - kept[-1][1]) >= self.path_min_spacing:
                kept.append(point)
        if points and kept[-1] != points[-1]:
            kept.append(points[-1])
        return kept

    def _in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    @staticmethod
    def _load_pcd_xyz(path: Path) -> np.ndarray:
        with path.open("rb") as stream:
            header: dict[str, list[str]] = {}
            while True:
                line = stream.readline()
                if not line:
                    raise ValueError(f"invalid PCD header: {path}")
                decoded = line.decode("ascii").strip()
                if decoded and not decoded.startswith("#"):
                    parts = decoded.split()
                    header[parts[0].upper()] = parts[1:]
                if decoded.upper().startswith("DATA "):
                    break
            fields = header["FIELDS"]
            sizes = [int(v) for v in header["SIZE"]]
            types = header["TYPE"]
            counts = [int(v) for v in header.get("COUNT", ["1"] * len(fields))]
            point_count = int(header.get("POINTS", header["WIDTH"])[0])
            data_kind = header["DATA"][0].lower()
            if data_kind == "ascii":
                rows = np.loadtxt(stream, max_rows=point_count)
                indices = [fields.index(axis) for axis in ("x", "y", "z")]
                return np.asarray(rows[:, indices], dtype=np.float32)
            if data_kind != "binary":
                raise ValueError(f"unsupported PCD DATA mode: {data_kind}")
            formats = {"F": {4: "f", 8: "d"}, "I": {1: "b", 2: "h", 4: "i", 8: "q"}, "U": {1: "B", 2: "H", 4: "I", 8: "Q"}}
            fmt = "<" + "".join(formats[t][size] * count for t, size, count in zip(types, sizes, counts))
            unpacker = struct.Struct(fmt)
            indices = [fields.index(axis) for axis in ("x", "y", "z")]
            xyz = np.empty((point_count, 3), dtype=np.float32)
            for index in range(point_count):
                values = unpacker.unpack(stream.read(unpacker.size))
                xyz[index] = [values[i] for i in indices]
            return xyz
