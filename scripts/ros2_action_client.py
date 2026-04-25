#!/usr/bin/env python3
from __future__ import annotations

"""
Generic ROS2 action client that emits JSONL lifecycle events.

Expected usage:
    echo '{"task_id":"...","action":"restock","payload":{...}}' \
      | python3 scripts/ros2_action_client.py \
          --action-name /dispatch_order \
          --action-type robot_task_interfaces/action/DispatchOrder \
          --stdin
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


try:
    project_root = _project_root()
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
except Exception:
    pass

from src.utils.ros2_env import ensure_ros_runtime

ensure_ros_runtime(reexec=True)

try:
    import rclpy
    from action_msgs.msg import GoalStatus
    from rclpy.action import ActionClient
    from rclpy.node import Node
    from rosidl_runtime_py.convert import message_to_ordereddict
    from rosidl_runtime_py.set_message import set_message_fields
    from rosidl_runtime_py.utilities import get_action
except ImportError as exc:
    print(f"[ros2_action_client] import error: {exc}", file=sys.stderr)
    raise SystemExit(1)


GOAL_STATUS_NAMES = {
    GoalStatus.STATUS_UNKNOWN: "unknown",
    GoalStatus.STATUS_ACCEPTED: "accepted",
    GoalStatus.STATUS_EXECUTING: "executing",
    GoalStatus.STATUS_CANCELING: "canceling",
    GoalStatus.STATUS_SUCCEEDED: "succeeded",
    GoalStatus.STATUS_CANCELED: "canceled",
    GoalStatus.STATUS_ABORTED: "aborted",
}


def emit_event(event: str, **payload: Any) -> None:
    body = {"event": event, **payload}
    print(json.dumps(body, ensure_ascii=False), flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--action-name", default="/dispatch_order")
    parser.add_argument(
        "--action-type",
        default="robot_task_interfaces/action/DispatchOrder",
    )
    parser.add_argument(
        "--goal-mode",
        choices=["merged_task_payload", "raw", "payload_only"],
        default="merged_task_payload",
    )
    parser.add_argument("--server-timeout", type=float, default=8.0)
    parser.add_argument("--accept-timeout", type=float, default=8.0)
    parser.add_argument("--result-timeout", type=float, default=0.0)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--text")
    source.add_argument("--file")
    source.add_argument("--stdin", action="store_true")
    return parser.parse_args()


def load_input_text(args: argparse.Namespace) -> str:
    if args.text is not None:
        return args.text
    if args.file is not None:
        return Path(args.file).read_text(encoding="utf-8")
    payload = sys.stdin.read()
    if payload:
        return payload
    raise SystemExit("goal input is empty")


def load_goal_json(args: argparse.Namespace) -> Dict[str, Any]:
    raw_text = load_input_text(args).strip()
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        emit_event("error", message=f"goal JSON 解析失败: {exc}")
        raise SystemExit(1)
    if not isinstance(data, dict):
        emit_event("error", message="goal JSON 必须是对象")
        raise SystemExit(1)
    return data


def transform_goal(data: Dict[str, Any], goal_mode: str) -> Dict[str, Any]:
    if goal_mode == "raw":
        return dict(data)
    if goal_mode == "payload_only":
        payload = data.get("payload")
        if isinstance(payload, dict):
            return dict(payload)
        return {}

    goal = {key: value for key, value in data.items() if key != "payload"}
    payload = data.get("payload")
    if isinstance(payload, dict):
        goal.update(payload)
    return goal


def spin_until_future_complete(
    node: Node,
    future,
    timeout_sec: float,
    *,
    poll_interval_sec: float = 0.1,
) -> bool:
    deadline = None if timeout_sec <= 0 else time.monotonic() + timeout_sec
    while rclpy.ok():
        if future.done():
            return True
        if deadline is not None and time.monotonic() >= deadline:
            return future.done()
        rclpy.spin_once(node, timeout_sec=poll_interval_sec)
    return future.done()


def main() -> int:
    args = parse_args()
    input_json = load_goal_json(args)
    goal_json = transform_goal(input_json, args.goal_mode)

    try:
        action_type = get_action(args.action_type)
    except (AttributeError, ModuleNotFoundError, ValueError) as exc:
        emit_event(
            "error",
            message=(
                f"无法加载 action 类型 {args.action_type}: {exc}. "
                "请确认 Humble 容器已 source 到自定义接口包 install/setup.bash"
            ),
        )
        return 1

    node = None
    try:
        rclpy.init()
        node = Node("aiagent_dispatch_action_client")
        client = ActionClient(node, action_type, args.action_name)
        emit_event(
            "client_start",
            action_name=args.action_name,
            action_type=args.action_type,
            goal_mode=args.goal_mode,
        )

        if not client.wait_for_server(timeout_sec=max(0.1, args.server_timeout)):
            emit_event(
                "error",
                message=(
                    f"等待 action server 超时: name={args.action_name} "
                    f"type={args.action_type}"
                ),
            )
            return 1

        emit_event("server_ready", action_name=args.action_name)

        goal_msg = action_type.Goal()
        goal_fields = set(goal_msg.get_fields_and_field_types().keys())
        filtered_goal_json = {
            key: value for key, value in goal_json.items() if key in goal_fields
        }
        try:
            set_message_fields(goal_msg, filtered_goal_json)
        except Exception as exc:
            emit_event(
                "error",
                message=f"填充 action goal 失败: {exc}",
                goal_json=filtered_goal_json,
                available_fields=goal_msg.get_fields_and_field_types(),
            )
            return 1

        def feedback_callback(feedback_msg) -> None:
            try:
                feedback = message_to_ordereddict(feedback_msg.feedback)
            except Exception as exc:
                feedback = {"raw_error": str(exc)}
            emit_event("feedback", feedback=feedback)

        send_goal_future = client.send_goal_async(
            goal_msg,
            feedback_callback=feedback_callback,
        )
        if not spin_until_future_complete(
            node,
            send_goal_future,
            max(0.1, args.accept_timeout),
        ):
            emit_event("error", message="等待 goal response 超时")
            return 1

        goal_handle = send_goal_future.result()
        if goal_handle is None:
            emit_event("error", message="goal response 为空")
            return 1

        emit_event("goal_response", accepted=bool(goal_handle.accepted))
        if not goal_handle.accepted:
            return 2

        result_future = goal_handle.get_result_async()
        if not spin_until_future_complete(node, result_future, args.result_timeout):
            emit_event("error", message="等待 action result 超时")
            return 1

        result_wrapper = result_future.result()
        if result_wrapper is None:
            emit_event("error", message="action result 为空")
            return 1

        status_code = int(result_wrapper.status)
        status_name = GOAL_STATUS_NAMES.get(status_code, f"unknown({status_code})")
        try:
            result_dict = message_to_ordereddict(result_wrapper.result)
        except Exception as exc:
            result_dict = {"raw_error": str(exc)}

        emit_event(
            "result",
            status=status_name,
            status_code=status_code,
            result=result_dict,
        )
        return 0 if status_name == "succeeded" else 4
    except KeyboardInterrupt:
        emit_event("error", message="action client 被中断")
        return 130
    except Exception as exc:
        emit_event("error", message=f"action client 异常: {exc}")
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


if __name__ == "__main__":
    raise SystemExit(main())
