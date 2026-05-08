"""
多无人机协同物流系统 - ROS2 指令工具（UInt8 版）

通过 ROS2 Topic `/drone_command` 向无人机开发板发送 std_msgs/UInt8 指令码。
无人机 uart_to_stm32 节点以 UInt8 订阅该 topic，类型必须一致，否则 DDS
仅能完成 topic 名发现而无法建立端到端连接。

指令码约定：
    1 = takeoff（起飞）
    2 = land（降落/停止/返航）
    3 = hover（悬停）

发布路径（v5 改造）：
    - 主路径：DroneCommandBridge 主进程驻留 publisher 单例（src/ros/drone_command_bridge.py）
    - Fallback：bridge 不可用时回退到 subprocess.Popen scripts/ros2_int32_publisher.py

跨机通信要求：
    - 两台机器在同一网段
    - ROS_DOMAIN_ID 一致（默认 10）
    - PC 和开发板均使用本机 ROS2 Humble 时，无需 Docker bridge
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from src.utils.logging_config import get_logger

_logger = get_logger(__name__)

STATUS_FILE = Path("config/task_status.jsonl")
QUEUE_FILE = Path("config/task_queue.jsonl")

# ROS2 配置
ROS2_DRONE_COMMAND_TOPIC = os.environ.get("ROS2_DRONE_COMMAND_TOPIC", "/drone_command")
ROS2_PYTHON = sys.executable
# 持续发布时长（秒）。v5 由 30s 缩短为 8s（决策 Q2）。
ROS2_PUBLISH_DURATION_SEC = float(os.environ.get("ROS2_PUBLISH_DURATION_SEC", "8"))
ROS2_PUBLISH_INTERVAL_SEC = 0.1

# 指令码常量
CMD_TAKEOFF = 1
CMD_LAND = 2
CMD_HOVER = 3

PROJECT_ROOT = Path(__file__).resolve().parents[4]
UINT8_PUBLISHER_SCRIPT = PROJECT_ROOT / "scripts" / "ros2_int32_publisher.py"


def _append_jsonl(p: Path, obj: dict):
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _append_status(task_id: str, status: str, detail: str = ""):
    _append_jsonl(
        STATUS_FILE,
        {"ts": time.time(), "task_id": task_id, "status": status, "detail": detail},
    )


async def _publish_int_fire_and_forget(topic: str, value: int) -> tuple[str, str]:
    """Fire-and-forget 发布 UInt8。

    v5：优先走 DroneCommandBridge 主进程驻留 publisher（避免 subprocess + import rclpy
    每次冷启动 ~1-1.5s）。bridge 不可用时回退到原 subprocess.Popen 路径。
    """
    # ── 主路径：DroneCommandBridge ────────────────────────
    try:
        from src.ros.drone_command_bridge import get_drone_command_bridge
        bridge = get_drone_command_bridge()
        if bridge.available:
            try:
                await bridge.publish_command(value, ROS2_PUBLISH_DURATION_SEC)
                return "dispatched", f"bridge 持续发布 {int(ROS2_PUBLISH_DURATION_SEC)}s"
            except Exception as exc:
                _logger.warning("[drone_bridge] publish 失败,走 subprocess fallback: %s", exc)
    except Exception as exc:
        _logger.warning("[drone_bridge] 不可用,走 subprocess fallback: %s", exc)

    # ── Fallback：subprocess.Popen ────────────────────────
    count = int(ROS2_PUBLISH_DURATION_SEC / ROS2_PUBLISH_INTERVAL_SEC)
    cmd = [
        ROS2_PYTHON,
        str(UINT8_PUBLISHER_SCRIPT),
        "--topic", topic,
        "--value", str(value),
        "--timeout", str(ROS2_PUBLISH_DURATION_SEC),
        "--interval", str(ROS2_PUBLISH_INTERVAL_SEC),
        "--min-count", str(count),
        "--after-match-count", str(count),
    ]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except FileNotFoundError:
        return "error", f"未找到 UInt8 发布脚本：{UINT8_PUBLISHER_SCRIPT}"
    except Exception as exc:
        return "error", f"启动发布进程失败：{exc}"

    return "dispatched", f"subprocess 持续发布 {int(ROS2_PUBLISH_DURATION_SEC)}s pid={proc.pid}"


async def drone_takeoff(args: dict) -> str:
    """向无人机发送起飞指令（UInt8 = 1）。"""
    task_id = f"takeoff-{int(time.time() * 1000)}"
    _logger.info(f"[无人机] 发送起飞指令 UInt8={CMD_TAKEOFF}")

    ros_state, ros_detail = await _publish_int_fire_and_forget(
        ROS2_DRONE_COMMAND_TOPIC, CMD_TAKEOFF
    )

    _append_jsonl(QUEUE_FILE, {
        "task_id": task_id,
        "ts": time.time(),
        "command": "takeoff",
        "value": CMD_TAKEOFF,
        "status": ros_state,
    })
    _append_status(task_id, ros_state, ros_detail)

    if ros_state == "dispatched":
        return "起飞指令已下达，执行成功。"
    return f"起飞指令下发失败：{ros_detail}"


async def drone_land(args: dict) -> str:
    """向无人机发送降落指令（UInt8 = 2）。

    v5：取消 emergency 参数，"停止/停下/降落/返航" 统一映射 value=2。
    """
    _ = args
    task_id = f"land-{int(time.time() * 1000)}"
    _logger.info(f"[无人机] 发送降落指令 UInt8={CMD_LAND}")

    ros_state, ros_detail = await _publish_int_fire_and_forget(
        ROS2_DRONE_COMMAND_TOPIC, CMD_LAND
    )

    _append_jsonl(QUEUE_FILE, {
        "task_id": task_id,
        "ts": time.time(),
        "command": "land",
        "value": CMD_LAND,
        "status": ros_state,
    })
    _append_status(task_id, ros_state, ros_detail)

    if ros_state == "dispatched":
        return "降落指令已下达，执行成功。"
    return f"降落指令下发失败：{ros_detail}"


async def drone_hover(args: dict) -> str:
    """向无人机发送悬停指令（UInt8 = 3）。"""
    _ = args
    task_id = f"hover-{int(time.time() * 1000)}"
    _logger.info(f"[无人机] 发送悬停指令 UInt8={CMD_HOVER}")

    ros_state, ros_detail = await _publish_int_fire_and_forget(
        ROS2_DRONE_COMMAND_TOPIC, CMD_HOVER
    )

    _append_jsonl(QUEUE_FILE, {
        "task_id": task_id,
        "ts": time.time(),
        "command": "hover",
        "value": CMD_HOVER,
        "status": ros_state,
    })
    _append_status(task_id, ros_state, ros_detail)

    if ros_state == "dispatched":
        return "悬停指令已下达，执行成功。"
    return f"悬停指令下发失败：{ros_detail}"


async def drone_status(args: dict) -> str:
    """查询最近任务状态日志。"""
    if not STATUS_FILE.exists():
        return "暂无任务记录。"

    raw_lines = STATUS_FILE.read_text(encoding="utf-8").strip().splitlines()
    tail = raw_lines[-10:] if len(raw_lines) > 10 else raw_lines

    rendered = []
    for line in tail:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = time.strftime("%H:%M:%S", time.localtime(float(item.get("ts", 0))))
        tid = item.get("task_id", "?")
        status = item.get("status", "?")
        rendered.append(f"[{ts}] {tid} {status}")

    return "最近任务：\n" + "\n".join(rendered) if rendered else "暂无任务记录。"


async def query_status(args: dict) -> str:
    """查询最近任务状态日志（别名）。"""
    return await drone_status(args)


async def mapping_view(args: dict) -> str:
    """查看建图效果。

    v5：建图已由 SlamBridge 自动订阅 /a/Laser_map 等 topic 流推到平板 /ws/slam，
    平板 WebView 实时显示。本工具不再启动桌面 rviz2，直接返回固定提示文案。
    """
    _ = args
    return "地图正在实时更新，直接看屏幕就好"
