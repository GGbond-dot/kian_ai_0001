"""
SlamBridge — 订阅 SLAM topic，降采样，通过 WebServer 推送二进制帧到 /ws/slam.

设计要点:
  - rclpy 作为可选依赖：环境无 ROS 时自动进入 stub 模式
  - 所有可配置量集中在 slam_constants.py
  - 与 WebServer 的耦合仅通过 broadcast_slam_bytes(payload) 一个方法
    （将来要拆成独立进程，只需把这个调用换成 IPC 即可）
"""

from __future__ import annotations

import asyncio
import math
import os
import struct
import time
from typing import TYPE_CHECKING, Optional

import numpy as np

from src.display import slam_constants as C
from src.utils.logging_config import get_logger

if TYPE_CHECKING:
    from src.display.web_server import WebServer

logger = get_logger(__name__)


# ===================== 二进制编码 =====================

def encode_points(channel: int, xyz: np.ndarray) -> bytes:
    """编码点云: [u8 channel][u32 N][N*3*float32]."""
    n = xyz.shape[0]
    buf = bytearray()
    buf.append(channel)
    buf += struct.pack("<I", n)
    buf += xyz.astype(np.float32, copy=False).tobytes()
    return bytes(buf)


def encode_pose(x: float, y: float, z: float,
                qx: float, qy: float, qz: float, qw: float) -> bytes:
    """编码 pose: [u8 channel][7*float32]."""
    return bytes([C.CHAN_ODOM]) + struct.pack("<7f", x, y, z, qx, qy, qz, qw)


# ===================== Bridge =====================

class SlamBridge:
    """SLAM 订阅与推送的统一入口."""

    def __init__(self, web_server: "WebServer"):
        self.web_server = web_server
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stub_task: Optional[asyncio.Task] = None
        self._ros_task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

        # 节流时间戳
        self._last_map_t = 0.0
        self._last_scan_t = 0.0
        self._last_odom_t = 0.0

    async def start(self) -> None:
        """启动桥接器. 自动选择 ROS 模式或 Stub 模式."""
        self._loop = asyncio.get_running_loop()
        self._stop.clear()

        force_stub = os.environ.get("AIAGENT_SLAM_STUB") == "1"

        if force_stub:
            logger.info("SlamBridge: 强制 Stub 模式 (AIAGENT_SLAM_STUB=1)")
            self._stub_task = asyncio.create_task(self._run_stub())
            return

        try:
            import rclpy  # noqa: F401
        except ImportError:
            if C.SLAM_STUB_FALLBACK_WHEN_NO_ROS:
                logger.warning("SlamBridge: rclpy 不可用，进入 Stub 模式")
                self._stub_task = asyncio.create_task(self._run_stub())
                return
            else:
                logger.error("SlamBridge: rclpy 不可用且未启用 stub fallback")
                return

        logger.info("SlamBridge: 启动 ROS 模式")
        self._ros_task = asyncio.create_task(self._run_ros())

    async def stop(self) -> None:
        self._stop.set()
        for task in (self._stub_task, self._ros_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    # ===================== Stub 模式 =====================

    async def _run_stub(self) -> None:
        """推送假数据用于前端联调.

        - 地图：一团螺旋点云（缓慢扩张）
        - scan：当前位置附近的小球
        - odom：沿圆轨迹运动
        - path：圆轨迹的历史采样
        """
        t0 = time.time()
        path_pts: list[tuple[float, float, float]] = []

        while not self._stop.is_set():
            t = time.time() - t0

            # ---- 地图: 1Hz ----
            if t - (self._last_map_t - t0) >= 1.0:
                self._last_map_t = time.time()
                n = 5000
                theta = np.linspace(0, 8 * np.pi, n)
                r = np.linspace(0.1, 5.0 + 0.5 * math.sin(t * 0.1), n)
                xyz = np.stack([
                    r * np.cos(theta),
                    r * np.sin(theta),
                    np.linspace(-0.5, 1.5, n),
                ], axis=1)
                await self.web_server.broadcast_slam_bytes(
                    encode_points(C.CHAN_MAP, xyz)
                )

            # ---- odom + scan: 10Hz / 5Hz ----
            now = time.time()
            cx = 2.0 * math.cos(t * 0.3)
            cy = 2.0 * math.sin(t * 0.3)
            yaw = t * 0.3 + math.pi / 2
            qz = math.sin(yaw / 2)
            qw = math.cos(yaw / 2)

            if now - self._last_odom_t >= 1.0 / C.SLAM_ODOM_MAX_HZ:
                self._last_odom_t = now
                await self.web_server.broadcast_slam_bytes(
                    encode_pose(cx, cy, 0.2, 0.0, 0.0, qz, qw)
                )

            if now - self._last_scan_t >= 1.0 / C.SLAM_SCAN_MAX_HZ:
                self._last_scan_t = now
                n = 800
                rng = np.random.default_rng(int(t * 10))
                local = rng.normal(0, 0.3, (n, 3))
                local[:, 2] *= 0.2
                local[:, 0] += cx
                local[:, 1] += cy
                await self.web_server.broadcast_slam_bytes(
                    encode_points(C.CHAN_SCAN, local)
                )

            # ---- path: 每秒追加一次 ----
            if int(t * 1.0) > len(path_pts) - 1:
                path_pts.append((cx, cy, 0.2))
                if len(path_pts) > 1:
                    arr = np.array(path_pts, dtype=np.float32)
                    await self.web_server.broadcast_slam_bytes(
                        encode_points(C.CHAN_PATH, arr)
                    )

            await asyncio.sleep(0.05)

    # ===================== ROS 模式 =====================

    async def _run_ros(self) -> None:
        """真实 ROS 订阅. 在线程池里跑 rclpy spin，回调里跨线程 schedule 推送."""
        # NOTE: 完整实现需要队友确认 topic 名称、frame、PointCloud2 字段格式
        # 当前为骨架 — 留 TODO 标记，等接口确认后填充
        import rclpy
        from rclpy.executors import MultiThreadedExecutor
        from rclpy.node import Node
        from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

        # TODO(slam-team): 确认下列消息类型导入路径正确
        from nav_msgs.msg import Odometry, Path
        from sensor_msgs.msg import PointCloud2

        # 点云通常以 Best Effort 发布 (传感器数据 QoS), Reliable 订阅会收不到
        sensor_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=5,
        )

        rclpy.init(args=None)
        node = Node("aiagent_slam_bridge")

        def on_map(msg: PointCloud2):
            now = time.time()
            if now - self._last_map_t < 1.0 / C.SLAM_MAP_MAX_HZ:
                return
            self._last_map_t = now
            xyz = self._pc2_to_numpy(msg)
            xyz = self._voxel_downsample(xyz, C.SLAM_MAP_VOXEL_SIZE)
            self._schedule_send(encode_points(C.CHAN_MAP, xyz))

        def on_scan(msg: PointCloud2):
            now = time.time()
            if now - self._last_scan_t < 1.0 / C.SLAM_SCAN_MAX_HZ:
                return
            self._last_scan_t = now
            xyz = self._pc2_to_numpy(msg)
            xyz = self._voxel_downsample(xyz, C.SLAM_SCAN_VOXEL_SIZE)
            self._schedule_send(encode_points(C.CHAN_SCAN, xyz))

        def on_odom(msg: Odometry):
            now = time.time()
            if now - self._last_odom_t < 1.0 / C.SLAM_ODOM_MAX_HZ:
                return
            self._last_odom_t = now
            p = msg.pose.pose.position
            q = msg.pose.pose.orientation
            self._schedule_send(encode_pose(p.x, p.y, p.z, q.x, q.y, q.z, q.w))

        def on_path(msg: Path):
            pts = [(ps.pose.position.x, ps.pose.position.y, ps.pose.position.z)
                   for ps in msg.poses[::C.SLAM_PATH_DECIMATE]]
            if not pts:
                return
            arr = np.array(pts, dtype=np.float32)
            self._schedule_send(encode_points(C.CHAN_PATH, arr))

        node.create_subscription(PointCloud2, C.SLAM_TOPIC_MAP, on_map, sensor_qos)
        node.create_subscription(PointCloud2, C.SLAM_TOPIC_SCAN, on_scan, sensor_qos)
        node.create_subscription(Odometry, C.SLAM_TOPIC_ODOM, on_odom, 10)
        node.create_subscription(Path, C.SLAM_TOPIC_PATH, on_path, 1)

        executor = MultiThreadedExecutor()
        executor.add_node(node)

        try:
            await asyncio.get_running_loop().run_in_executor(None, executor.spin)
        finally:
            node.destroy_node()
            rclpy.shutdown()

    def _schedule_send(self, payload: bytes) -> None:
        """从 ROS 回调线程跨线程 schedule 到 asyncio loop."""
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(
            self.web_server.broadcast_slam_bytes(payload), self._loop
        )

    @staticmethod
    def _pc2_to_numpy(msg) -> np.ndarray:
        """PointCloud2 → (N,3) float32.

        TODO(slam-team): 确认字段顺序后用 sensor_msgs_py.point_cloud2.read_points 实现.
        当前占位返回空数组以避免崩溃.
        """
        try:
            from sensor_msgs_py import point_cloud2 as pc2
            pts = pc2.read_points_numpy(msg, field_names=("x", "y", "z"),
                                        skip_nans=True)
            return pts.astype(np.float32, copy=False)
        except Exception as e:
            logger.warning("PointCloud2 解析失败: %s", e)
            return np.zeros((0, 3), dtype=np.float32)

    @staticmethod
    def _voxel_downsample(xyz: np.ndarray, voxel: float) -> np.ndarray:
        """简易 numpy voxel 降采样 (不依赖 open3d)."""
        if xyz.size == 0:
            return xyz
        keys = np.floor(xyz / voxel).astype(np.int64)
        # 用 view 把 (N,3) int64 当成结构化标量做 unique
        _, idx = np.unique(
            keys.view([("", keys.dtype)] * 3).ravel(),
            return_index=True,
        )
        return xyz[idx]
