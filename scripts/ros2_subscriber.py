#!/usr/bin/env python3
"""
ROS2 订阅者。

实现保持接近 ROS 2 官方 minimal subscriber 示例，只保留本仓库需要的
JSON 解析和可读日志输出。
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

try:
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
except Exception:
    pass

from src.utils.ros2_env import ensure_ros_runtime

ROS2_TOPIC = os.environ.get("ROS2_TOPIC", "/robot_task")
ros_install = ensure_ros_runtime(
    preferred_distro=os.environ.get("ROS_DISTRO"), reexec=True
)

try:
    import rclpy
    from rclpy.executors import ExternalShutdownException
    from rclpy.node import Node
    from std_msgs.msg import String
except ImportError as exc:
    print(f"[ros2_subscriber] ❌ 无法导入 rclpy: {exc}")
    if ros_install is None:
        print("[ros2_subscriber] ❌ 未检测到 /opt/ros/<distro> 本机安装")
    else:
        print(
            f"[ros2_subscriber] 已探测到 ROS2 {ros_install.distro}，"
            f"建议使用 {ros_install.python_executable} 运行此脚本。"
        )
    sys.exit(1)


class RobotTaskSubscriber(Node):
    def __init__(self):
        super().__init__("robot_task_subscriber")
        self.subscription = self.create_subscription(
            String,
            ROS2_TOPIC,
            self._on_message,
            10,
        )
        print("=" * 60, flush=True)
        if ros_install is not None:
            print(
                f"📡 正在监听 {ROS2_TOPIC} topic "
                f"(ROS2 {ros_install.distro}, Python: {ros_install.python_executable})...",
                flush=True,
            )
        else:
            print(f"📡 正在监听 {ROS2_TOPIC} topic...", flush=True)
        print("等待消息中（在主程序对话下单，这里会实时显示）", flush=True)
        print("=" * 60, flush=True)

    def _on_message(self, msg: String):
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(f"\n{'=' * 60}", flush=True)
        print(f"✅ [{ts}] 收到 ROS2 消息！", flush=True)
        print(f"原始数据: {msg.data}", flush=True)
        try:
            data = json.loads(msg.data)
            print(f"  task_id : {data.get('task_id')}", flush=True)
            print(f"  action  : {data.get('action')}", flush=True)
            payload = data.get("payload", {})
            print(f"  手机型号: {payload.get('item')}", flush=True)
            print(f"  数量    : {payload.get('quantity')}", flush=True)
            print(
                f"  路径    : {payload.get('src_location')} -> {payload.get('dst_location')}",
                flush=True,
            )
        except json.JSONDecodeError:
            pass
        print(f"{'=' * 60}\n", flush=True)


def main(args=None):
    node = None
    try:
        rclpy.init(args=args)
        node = RobotTaskSubscriber()
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        print("\n[订阅者] 已停止", flush=True)
    except Exception as exc:
        print(f"\n[订阅者] 异常退出: {exc}", flush=True)
        print("[订阅者] 请检查上方 DDS/RMW 错误输出", flush=True)
        sys.exit(1)
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


if __name__ == "__main__":
    main()
