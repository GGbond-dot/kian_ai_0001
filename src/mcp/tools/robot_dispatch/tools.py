"""
多无人机协同物流系统 - ROS2 指令工具（UInt8 版）

通过 ROS2 Topic `/drone_command` 向无人机开发板发送 std_msgs/UInt8 指令码。
无人机 uart_to_stm32 节点以 UInt8 订阅该 topic，类型必须一致，否则 DDS
仅能完成 topic 名发现而无法建立端到端连接。

指令码约定：
    1 = takeoff（起飞）
    2 = land（降落/返航）
    3 = emergency_land（紧急降落）

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
# 持续发布时长（秒），避免订阅者错过指令
ROS2_PUBLISH_DURATION_SEC = float(os.environ.get("ROS2_PUBLISH_DURATION_SEC", "30"))
ROS2_PUBLISH_INTERVAL_SEC = 0.1

# 指令码常量
CMD_TAKEOFF = 1
CMD_LAND = 2
CMD_EMERGENCY_LAND = 3

PROJECT_ROOT = Path(__file__).resolve().parents[4]
UINT8_PUBLISHER_SCRIPT = PROJECT_ROOT / "scripts" / "ros2_int32_publisher.py"
RVIZ_BIN = os.environ.get("RVIZ_BIN", "rviz2")
RVIZ_CONFIG_PATH = Path(
    os.environ.get(
        "RVIZ_MAPPING_CONFIG",
        str(PROJECT_ROOT / "dcl_fast_lio_mid360.rviz"),
    )
).expanduser()


def _append_jsonl(p: Path, obj: dict):
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _append_status(task_id: str, status: str, detail: str = ""):
    _append_jsonl(
        STATUS_FILE,
        {"ts": time.time(), "task_id": task_id, "status": status, "detail": detail},
    )


def _publish_int_fire_and_forget(topic: str, value: int) -> tuple[str, str]:
    """Fire-and-forget 发布 UInt8：立即返回，后台持续发送一段时间。

    现在 PC 和开发板均使用本机 ROS2 Humble，直接调用本仓库的 UInt8
    发布脚本。该脚本会自动探测 /opt/ros/<distro> 并补齐 rclpy 环境。
    """
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

    return "dispatched", f"后台持续发布 {int(ROS2_PUBLISH_DURATION_SEC)}s pid={proc.pid}"


async def drone_takeoff(args: dict) -> str:
    """向无人机发送起飞指令（Int32 = 1）。"""
    task_id = f"takeoff-{int(time.time() * 1000)}"
    _logger.info(f"[无人机] 发送起飞指令 Int32={CMD_TAKEOFF}")

    ros_state, ros_detail = _publish_int_fire_and_forget(
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
    """向无人机发送降落指令（Int32 = 2 或 3）。"""
    emergency = (args.get("emergency") or "false").strip().lower() in ("true", "1", "yes")
    value = CMD_EMERGENCY_LAND if emergency else CMD_LAND
    cmd_label = "紧急降落" if emergency else "降落"

    task_id = f"land-{int(time.time() * 1000)}"
    _logger.info(f"[无人机] 发送{cmd_label}指令 Int32={value}")

    ros_state, ros_detail = _publish_int_fire_and_forget(
        ROS2_DRONE_COMMAND_TOPIC, value
    )

    _append_jsonl(QUEUE_FILE, {
        "task_id": task_id,
        "ts": time.time(),
        "command": "emergency_land" if emergency else "land",
        "value": value,
        "status": ros_state,
    })
    _append_status(task_id, ros_state, ros_detail)

    if ros_state == "dispatched":
        return f"{cmd_label}指令已下达，执行成功。"
    return f"{cmd_label}指令下发失败：{ros_detail}"


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


def _rviz_already_running(config_path: Path) -> bool:
    """检查是否已经存在一个 rviz2 进程加载了同一个配置文件。"""
    try:
        result = subprocess.run(
            ["pgrep", "-af", "rviz2"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return False
    needle_abs = str(config_path)
    needle_name = config_path.name
    for line in result.stdout.splitlines():
        if needle_abs in line or needle_name in line:
            return True
    return False


async def mapping_view(args: dict) -> str:
    """启动 rviz2 查看 FAST-LIO MID360 建图效果（fire-and-forget）。"""
    _ = args
    task_id = f"mapview-{int(time.time() * 1000)}"
    _logger.info(f"[建图] 启动 rviz2 查看建图效果 config={RVIZ_CONFIG_PATH}")

    if not RVIZ_CONFIG_PATH.exists():
        detail = f"未找到 RViz 配置：{RVIZ_CONFIG_PATH}"
        _append_status(task_id, "error", detail)
        return "未找到建图配置文件。"

    if _rviz_already_running(RVIZ_CONFIG_PATH):
        _append_status(task_id, "already_running", str(RVIZ_CONFIG_PATH))
        return "建图视图已打开。"

    env = os.environ.copy()
    if not env.get("DISPLAY"):
        env["DISPLAY"] = ":0"

    cmd = [RVIZ_BIN, "-d", str(RVIZ_CONFIG_PATH)]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=env,
            cwd=str(PROJECT_ROOT),
        )
    except FileNotFoundError:
        _append_status(task_id, "error", f"未找到 {RVIZ_BIN}")
        return "未安装 rviz2。"
    except Exception as exc:
        _append_status(task_id, "error", f"启动失败：{exc}")
        return "建图视图启动失败。"

    _append_jsonl(
        QUEUE_FILE,
        {
            "task_id": task_id,
            "ts": time.time(),
            "command": "mapping_view",
            "pid": proc.pid,
            "status": "launched",
        },
    )
    _append_status(task_id, "launched", f"pid={proc.pid}")
    return "已打开建图视图。"
