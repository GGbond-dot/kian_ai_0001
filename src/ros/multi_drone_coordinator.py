"""多无人机配送编排器(时序状态机)。

文档的"独立单循环"模型不满足真实场景,这里实现真实流程的编排:
画抓取框 → "货到了配送" → 默认机起飞飞往抓取区 → 进框停留→播报"识别到N个货物"→
抓取(写死,到框点即算)→ 扣减剩余 → 扫码取放物点 → 飞往放物点送货 →
送达后判断抓取区剩余: >0 回框点抓下一个(多循环,不落地) / =0 返航降落 → 降落即空闲。

事件全部由 odom 轮询驱动(不依赖飞控反馈):进框/离框/到放物点/到家 都用位置判定。
P4 范围:单架跑通多循环。P5 再接"离框触发下一架""空闲机派新区"。
"""
from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from src.ros.path_reservation import ReservationStore
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class Phase(str, Enum):
    IDLE = "idle"
    GOTO_ZONE = "goto_zone"      # 飞往抓取区,等 odom 进框
    AT_ZONE = "at_zone"          # 在框内停留 → 播报 → 抓取 → 发配送
    DELIVERING = "delivering"    # 飞往放物点,等 odom 到达
    LANDING = "landing"          # 抓取区已空,返航降落


@dataclass
class GrabZone:
    zone_id: str
    min_x: float
    max_x: float
    min_y: float
    max_y: float
    remaining: int = 3

    @property
    def cx(self) -> float:
        return (self.min_x + self.max_x) / 2.0

    @property
    def cy(self) -> float:
        return (self.min_y + self.max_y) / 2.0

    def contains(self, x: float, y: float) -> bool:
        return self.min_x <= x <= self.max_x and self.min_y <= y <= self.max_y


@dataclass
class DroneTask:
    drone_key: str
    phase: Phase = Phase.IDLE
    zone_id: Optional[str] = None
    home: Optional[tuple[float, float]] = None        # 起飞点(返航降落用)
    zone_enter_at: Optional[float] = None             # 进框时刻(停留计时)
    grabbed_this_visit: bool = False                  # 本次进框是否已抓取+发配送
    left_zone_fired: bool = False                     # 本趟"离框"事件是否已触发(P5用)
    place_goal: Optional[tuple[float, float]] = None  # 当前配送放物点


class MultiDroneCoordinator:
    def __init__(self, plugin: Any) -> None:
        self._plugin = plugin                 # RosTerminalPlugin: planners / app / _drone_configs
        self._zones: dict[str, GrabZone] = {}
        self._tasks: dict[str, DroneTask] = {}
        self._tick_task: Optional[asyncio.Task] = None
        self._running = False
        self._zone_seq = 0
        # 可调参数
        self.dwell_seconds = 1.0              # 进框停留多久后播报+抓取
        self.arrive_threshold = 0.5           # 到达放物点/家 的判定半径(米)
        self.items_per_zone = 3               # 写死:每个抓取区货物数
        # 多机路径预约(防撞):TTL / safety_radius 从 MULTI_DRONE 配置读
        ttl, safety = 120.0, 1.0
        try:
            cfg = plugin.app.config if getattr(plugin, "app", None) else None
            if cfg is not None:
                ttl = float(cfg.get_config("MULTI_DRONE.reservation_ttl_sec", 120) or 120)
                safety = float(cfg.get_config("MULTI_DRONE.safety_radius", 1.0) or 1.0)
        except Exception:  # noqa: BLE001
            pass
        self.reservations = ReservationStore(ttl_sec=ttl, safety_radius=safety)

    # ── 访问 plugin 资源 ──────────────────────────────
    @property
    def app(self) -> Any:
        return self._plugin.app

    def _planner(self, key: str):
        return self._plugin.planners.get(key)

    def _command_topic(self, key: str) -> str:
        for c in self._plugin._drone_configs:
            if c.key == key:
                return c.command_topic
        return "/drone_command"

    def _label(self, key: str) -> str:
        for c in self._plugin._drone_configs:
            if c.key == key:
                return c.label
        return key

    # ── 生命周期 ──────────────────────────────────────
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._tick_task = asyncio.create_task(self._tick_loop())
        logger.info("MultiDroneCoordinator: started")

    async def stop(self) -> None:
        self._running = False
        if self._tick_task is not None:
            self._tick_task.cancel()
            self._tick_task = None

    # ── 触发:"货到了配送" ─────────────────────────────
    async def start_delivery(self, drone_key: Optional[str] = None) -> dict[str, Any]:
        """画好抓取框后启动配送任务。前置校验:未画抓取框则拒绝并提醒。"""
        from src.ros.goal_selection_store import get_goal_selection_store

        selected = get_goal_selection_store().latest(drone_key)
        rect = (selected or {}).get("rect")
        # 前置校验:必须是带 rect 的框选抓取区(排除扫码放物点)
        if not selected or selected.get("source") == "vision_qr" or not rect:
            raise RuntimeError("还没有抓取区，请先在地图上画好抓取框，再说货到了配送")

        # 点名 → 该机;未点名 → 空闲机(默认机优先)
        if drone_key and drone_key in self._plugin.planners:
            key = drone_key
        else:
            key = self._pick_idle_drone()
            if key is None:
                raise RuntimeError("当前没有空闲的无人机可以配送")
        planner = self._planner(key)
        if planner is None:
            raise RuntimeError("全局规划器未初始化")
        if not planner.available:
            raise RuntimeError(f"{self._label(key)}规划器未就绪（ROS 未连接）")

        task = self._tasks.get(key)
        if task is not None and task.phase != Phase.IDLE:
            raise RuntimeError(f"{self._label(key)}正在执行任务，请等待空闲或换一架")

        self._zone_seq += 1
        zone = GrabZone(
            zone_id=f"zone{self._zone_seq}",
            min_x=float(rect["minX"]), max_x=float(rect["maxX"]),
            min_y=float(rect["minY"]), max_y=float(rect["maxY"]),
            remaining=self.items_per_zone,
        )
        self._zones[zone.zone_id] = zone
        await self._assign_to_zone(key, zone)
        return {"drone_key": key, "zone_id": zone.zone_id, "remaining": zone.remaining}

    async def _assign_to_zone(self, key: str, zone: GrabZone) -> None:
        planner = self._planner(key)
        task = self._tasks.get(key) or DroneTask(key)
        task.phase = Phase.GOTO_ZONE
        task.zone_id = zone.zone_id
        task.grabbed_this_visit = False
        task.left_zone_fired = False
        task.place_goal = None
        if task.home is None:
            task.home = planner.latest_odom()
        self._tasks[key] = task

        planner.set_auto_land(False)  # 编排器接管:关掉 planner 自带的放物→自动降落
        await self._send_takeoff(key)
        self._dispatch(key, zone.cx, zone.cy, 1)  # goal_type=1 抓取
        logger.info(
            "Coordinator: %s 派往 %s 中心(%.2f, %.2f) 剩%d个货",
            key, zone.zone_id, zone.cx, zone.cy, zone.remaining,
        )
        self._broadcast(f"{self._label(key)}起飞，前往抓取区。")

    async def _send_takeoff(self, key: str) -> None:
        try:
            from src.ros.drone_command_bridge import get_drone_command_bridge
            bridge = get_drone_command_bridge(self._command_topic(key))
            if bridge.available:
                await bridge.publish_command(1)  # UInt8 = 1 起飞
            else:
                logger.warning("Coordinator: %s 命令 bridge 不可用,跳过起飞指令", key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Coordinator: %s 起飞指令发送失败: %s", key, exc)

    # ── 主循环:odom 驱动状态机 ─────────────────────────
    async def _tick_loop(self) -> None:
        while self._running:
            try:
                for key, task in list(self._tasks.items()):
                    if task.phase != Phase.IDLE:
                        await self._step(key, task)
            except Exception as exc:  # noqa: BLE001
                logger.error("Coordinator tick error: %s", exc, exc_info=True)
            await asyncio.sleep(0.1)

    async def _step(self, key: str, task: DroneTask) -> None:
        planner = self._planner(key)
        if planner is None:
            return
        odom = planner.latest_odom()
        if odom is None:
            return
        x, y = odom
        if task.home is None:
            task.home = (x, y)
        zone = self._zones.get(task.zone_id) if task.zone_id else None

        if task.phase == Phase.GOTO_ZONE:
            if zone and zone.contains(x, y):
                task.phase = Phase.AT_ZONE
                task.zone_enter_at = time.time()
                task.grabbed_this_visit = False
                logger.info("Coordinator: %s 进入 %s", key, zone.zone_id)

        elif task.phase == Phase.AT_ZONE:
            if (not task.grabbed_this_visit and task.zone_enter_at is not None
                    and (time.time() - task.zone_enter_at) >= self.dwell_seconds):
                await self._grab_and_deliver(key, task, zone)

        elif task.phase == Phase.DELIVERING:
            # 离框事件:区内还剩货且有空闲机 → 起飞下一架接力
            if zone and not task.left_zone_fired and not zone.contains(x, y):
                task.left_zone_fired = True
                await self._on_left_zone(key, zone)
            if task.place_goal and self._reached(x, y, task.place_goal):
                await self._on_delivered(key, task, zone)

        elif task.phase == Phase.LANDING:
            if task.home and self._reached(x, y, task.home):
                self._finish(key, task)

    async def _grab_and_deliver(self, key: str, task: DroneTask, zone: Optional[GrabZone]) -> None:
        label = self._label(key)
        planner = self._planner(key)
        # 防重复抓取:到达时货已被别的机抓完 → 直接返航降落,不抓空货
        if zone is None or zone.remaining <= 0:
            self._broadcast(f"{label}到达时抓取区已空，返航降落。")
            task.phase = Phase.LANDING
            home = task.home or planner.latest_odom()
            if home:
                self._dispatch(key, home[0], home[1], 3)
            logger.info("Coordinator: %s 到区时已空,返航降落", key)
            return
        # 播报识别(写死货物数)
        self._broadcast(f"{label}已到达抓取区，识别到{self.items_per_zone}个货物。")
        # 读放物点(扫码结果);无则保持在区,下个 tick 重试
        place = self._read_place_point(key)
        if place is None:
            logger.info("Coordinator: %s 暂无放物点(等扫码),保持在区重试", key)
            return
        # 抓取(写死,到框点即算抓到)→ 扣减剩余
        if zone and zone.remaining > 0:
            zone.remaining -= 1
        task.grabbed_this_visit = True
        task.place_goal = place
        self._dispatch(key, place[0], place[1], 2)  # goal_type=2 放物
        task.phase = Phase.DELIVERING
        logger.info(
            "Coordinator: %s 抓取完成,配送至(%.2f, %.2f),区剩%d个",
            key, place[0], place[1], zone.remaining if zone else -1,
        )

    async def _on_delivered(self, key: str, task: DroneTask, zone: Optional[GrabZone]) -> None:
        label = self._label(key)
        planner = self._planner(key)
        remaining = zone.remaining if zone else 0
        if remaining > 0:
            # 多循环:不落地,回抓取区抓下一个
            self._broadcast(f"{label}配送完成，返回抓取区，还剩{remaining}个货物。")
            task.phase = Phase.GOTO_ZONE
            task.grabbed_this_visit = False
            task.left_zone_fired = False
            task.place_goal = None
            self._dispatch(key, zone.cx, zone.cy, 1)
            logger.info("Coordinator: %s 返回 %s 抓下一个,剩%d", key, zone.zone_id, remaining)
        else:
            # 抓取区清空:返航降落
            self._broadcast(f"{label}配送完成，抓取区货物已取完，返航降落。")
            task.phase = Phase.LANDING
            home = task.home or planner.latest_odom()
            if home:
                self._dispatch(key, home[0], home[1], 3)  # goal_type=3 降落
            logger.info("Coordinator: %s 抓取区已空,返航降落", key)

    async def _on_left_zone(self, key: str, zone: GrabZone) -> None:
        """无人机离开抓取框。区内还剩货且有空闲机 → 起飞下一架接力(先后起飞防撞)。"""
        logger.info("Coordinator: %s 已离开 %s,区剩%d个", key, zone.zone_id, zone.remaining)
        if zone.remaining <= 0:
            return
        nxt = self._pick_idle_drone(exclude=key)
        if nxt is None:
            logger.info("Coordinator: 无空闲机,%s 剩%d个挂起(等谁先空)", zone.zone_id, zone.remaining)
            return
        await self._assign_to_zone(nxt, zone)  # 内部含起飞 + 播报

    def _is_idle(self, key: str) -> bool:
        task = self._tasks.get(key)
        return task is None or task.phase == Phase.IDLE

    def _pick_idle_drone(self, exclude: Optional[str] = None) -> Optional[str]:
        """挑一架空闲且可用的机;默认机优先。无则返回 None。"""
        keys = list(self._plugin.planners.keys())
        keys.sort(key=lambda k: (k != self._default_key))  # 默认机排前
        for k in keys:
            if k == exclude:
                continue
            planner = self._planner(k)
            if planner is not None and planner.available and self._is_idle(k):
                return k
        return None

    @property
    def _default_key(self) -> str:
        return getattr(self._plugin, "_default_key", "")

    def _finish(self, key: str, task: DroneTask) -> None:
        planner = self._planner(key)
        if planner is not None:
            planner.set_auto_land(True)  # 恢复 planner 默认行为
        self.reservations.release(key)   # 落地 → 释放路径预约
        task.phase = Phase.IDLE
        task.zone_id = None
        task.place_goal = None
        self._broadcast(f"{self._label(key)}已降落，任务结束。")
        logger.info("Coordinator: %s 任务结束,空闲", key)

    # ── 工具 ──────────────────────────────────────────
    def _dispatch(self, key: str, cx: float, cy: float, goal_type: int) -> None:
        """统一下发:合并其他机障碍 → planner 规划发布 → 登记本机路径预约。"""
        planner = self._planner(key)
        if planner is None:
            return
        positions = {k: p.latest_odom() for k, p in self._plugin.planners.items()}
        obstacles = self.reservations.obstacles_excluding(key, positions)
        planner.dispatch_selected({"cx": cx, "cy": cy}, goal_type, external_obstacles=obstacles)
        # 用实际规划出的路径登记预约(降落任务也登记,落地后释放)
        self.reservations.reserve(key, planner.last_path_points())

    def _reached(self, x: float, y: float, goal: tuple[float, float]) -> bool:
        return math.hypot(x - goal[0], y - goal[1]) <= self.arrive_threshold

    def _read_place_point(self, key: str) -> Optional[tuple[float, float]]:
        """从视觉检测结果读放物点(扫码解码坐标)。无有效结果返回 None。"""
        try:
            from src.vision.detection_store import get_detection_store
            det = get_detection_store().latest(key)
            if det and det.get("verified") and (det.get("place_x") or det.get("place_y")):
                return (float(det["place_x"]), float(det["place_y"]))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Coordinator: 读放物点失败: %s", exc)
        return None

    def _broadcast(self, text: str) -> None:
        try:
            if self.app is not None:
                self.app.trigger_proactive_response(text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Coordinator: 播报失败: %s", exc)

    def status(self) -> dict[str, Any]:
        return {
            "zones": {
                z.zone_id: {"remaining": z.remaining, "center": [round(z.cx, 2), round(z.cy, 2)]}
                for z in self._zones.values()
            },
            "drones": {
                k: {"phase": t.phase.value, "zone": t.zone_id, "place_goal": t.place_goal}
                for k, t in self._tasks.items()
            },
        }
