#!/usr/bin/env python3
"""
ROS2 发布节点。

调用方式：
    echo '{"task_id":"...","action":"restock","payload":{...}}' \
        | python3 scripts/ros2_publisher.py

支持两种底层发布路径：
1. 本机 ROS2 `rclpy`
2. `ROS2_PUBLISH_MODE=docker_humble_bridge` 时转发到 Humble Docker bridge
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

try:
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
except Exception:
    project_root = Path(__file__).resolve().parents[1]

ROS2_TOPIC = os.environ.get("ROS2_TOPIC", "/robot_task")
ROS2_PUBLISH_MODE = os.environ.get("ROS2_PUBLISH_MODE", "auto")
LOCAL_PUBLISH_TIMEOUT_SEC = float(os.environ.get("ROS2_PUBLISH_TIMEOUT_SEC", "6.0"))
LOCAL_PUBLISH_INTERVAL_SEC = float(os.environ.get("ROS2_PUBLISH_INTERVAL_SEC", "0.1"))
LOCAL_PUBLISH_MIN_COUNT = max(1, int(os.environ.get("ROS2_PUBLISH_MIN_COUNT", "5")))
LOCAL_PUBLISH_AFTER_MATCH_COUNT = max(
    1, int(os.environ.get("ROS2_PUBLISH_AFTER_MATCH_COUNT", "5"))
)
NO_SUBSCRIBER_EXIT_CODE = 2
DOCKER_BRIDGE_MODES = {"docker_humble_bridge", "docker-humble-bridge", "docker"}


def _normalize_publish_mode() -> str:
    mode = (ROS2_PUBLISH_MODE or "auto").strip().lower()
    return mode or "auto"


def _load_payload() -> str:
    payload_json = sys.stdin.read().strip()
    if not payload_json:
        print("[ros2_publisher] ERROR: stdin 为空", file=sys.stderr)
        sys.exit(1)
    try:
        json.loads(payload_json)
    except json.JSONDecodeError as exc:
        print(f"[ros2_publisher] ERROR: JSON 解析失败: {exc}", file=sys.stderr)
        sys.exit(1)
    return payload_json


def _run_docker_bridge_publish(payload_json: str) -> int:
    bridge_script = project_root / "scripts" / "docker_humble_bridge.py"
    if not bridge_script.exists():
        print(
            f"[ros2_publisher] ❌ 未找到 bridge 脚本: {bridge_script}",
            file=sys.stderr,
        )
        return 1

    cmd = [
        sys.executable,
        str(bridge_script),
        "pub",
        "--topic",
        ROS2_TOPIC,
        "--stdin",
        "--timeout",
        str(LOCAL_PUBLISH_TIMEOUT_SEC),
        "--interval",
        str(LOCAL_PUBLISH_INTERVAL_SEC),
        "--min-count",
        str(LOCAL_PUBLISH_MIN_COUNT),
        "--after-match-count",
        str(LOCAL_PUBLISH_AFTER_MATCH_COUNT),
    ]
    print(
        (
            "[ros2_publisher] 使用 docker_humble_bridge 发布: "
            f"topic={ROS2_TOPIC}"
        ),
        file=sys.stderr,
    )
    try:
        proc = subprocess.run(
            cmd,
            input=payload_json,
            text=True,
            capture_output=True,
            check=False,
            env=os.environ.copy(),
            timeout=max(30.0, LOCAL_PUBLISH_TIMEOUT_SEC + 30.0),
        )
    except subprocess.TimeoutExpired:
        print("[ros2_publisher] ❌ Docker bridge 发布超时", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"[ros2_publisher] ❌ 无法启动 Docker bridge: {exc}", file=sys.stderr)
        return 1

    if proc.stdout:
        sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    return proc.returncode


def _publish_with_local_rclpy(payload_json: str, args=None) -> int:
    from src.utils.ros2_env import ensure_ros_runtime

    ros_install = ensure_ros_runtime(
        preferred_distro=os.environ.get("ROS_DISTRO"), reexec=True
    )

    try:
        import rclpy
        from rclpy.executors import ExternalShutdownException
        from rclpy.node import Node
        from std_msgs.msg import String
    except ImportError as exc:
        print(f"[ros2_publisher] ❌ 无法导入 rclpy: {exc}", file=sys.stderr)
        if ros_install is None:
            print(
                "[ros2_publisher] ❌ 未检测到可用的 /opt/ros/<distro> 安装",
                file=sys.stderr,
            )
        return 1

    class RobotTaskPublisher(Node):
        def __init__(self, payload: str):
            super().__init__("robot_task_publisher")
            self.publisher_ = self.create_publisher(String, ROS2_TOPIC, 10)
            self.msg = String()
            self.msg.data = payload

        def publish_until_ready(self) -> tuple[int, int]:
            deadline = time.monotonic() + max(0.5, LOCAL_PUBLISH_TIMEOUT_SEC)
            publish_count = 0
            max_subscription_count = 0
            first_match_publish_count = None

            while True:
                max_subscription_count = max(
                    max_subscription_count, self.publisher_.get_subscription_count()
                )
                self.publisher_.publish(self.msg)
                publish_count += 1
                rclpy.spin_once(self, timeout_sec=max(0.01, LOCAL_PUBLISH_INTERVAL_SEC))
                max_subscription_count = max(
                    max_subscription_count, self.publisher_.get_subscription_count()
                )
                if max_subscription_count > 0 and first_match_publish_count is None:
                    # Discovery can flip to "matched" slightly before the subscriber
                    # is actually ready to consume the first user message.
                    first_match_publish_count = publish_count

                if publish_count < LOCAL_PUBLISH_MIN_COUNT:
                    continue

                if first_match_publish_count is not None:
                    if publish_count >= (
                        first_match_publish_count + LOCAL_PUBLISH_AFTER_MATCH_COUNT
                    ):
                        return publish_count, max_subscription_count
                    continue

                if time.monotonic() >= deadline:
                    return publish_count, max_subscription_count

    node = None
    try:
        rclpy.init(args=args)
        node = RobotTaskPublisher(payload_json)
        publish_count, max_subscription_count = node.publish_until_ready()
    except (KeyboardInterrupt, ExternalShutdownException):
        print("[ros2_publisher] 已取消", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"[ros2_publisher] ❌ 本机 rclpy 发布异常: {exc}", file=sys.stderr)
        print("[ros2_publisher] ❌ 请检查上方 DDS/RMW 错误输出", file=sys.stderr)
        return 1
    finally:
        if node is not None:
            try:
                node.destroy_node()
            except Exception:
                pass
        try:
            rclpy.shutdown()
        except Exception:
            pass

    distro = (
        ros_install.distro if ros_install is not None else os.environ.get("ROS_DISTRO", "?")
    )
    if max_subscription_count > 0:
        print(
            (
                "[ros2_publisher] ✅ 本机发布成功: "
                f"distro={distro} topic={ROS2_TOPIC} "
                f"publishes={publish_count} subs_seen={max_subscription_count}"
            ),
            file=sys.stderr,
        )
        return 0

    print(
        (
            "[ros2_publisher] ⚠️ 本机发布完成但未发现订阅者: "
            f"distro={distro} topic={ROS2_TOPIC} "
            f"publishes={publish_count} subs_seen=0 "
            "hint=先用官方 examples_rclpy_minimal_publisher/subscriber 验证 DDS 是否正常"
        ),
        file=sys.stderr,
    )
    return NO_SUBSCRIBER_EXIT_CODE


def main(args=None) -> int:
    payload_json = _load_payload()
    mode = _normalize_publish_mode()
    if mode in DOCKER_BRIDGE_MODES:
        return _run_docker_bridge_publish(payload_json)
    return _publish_with_local_rclpy(payload_json, args=args)


if __name__ == "__main__":
    sys.exit(main())
