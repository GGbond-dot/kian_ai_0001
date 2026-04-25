#!/usr/bin/env python3
"""
Publish std_msgs/String payloads with a short discovery window.

Usage examples:
    python3 scripts/ros2_string_publisher.py --topic /hello --text "hello"
    python3 scripts/ros2_string_publisher.py --topic /robot_task --file payload.json
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

try:
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
except Exception:
    pass

from src.utils.ros2_env import ensure_ros_runtime

ensure_ros_runtime(reexec=True)

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from std_msgs.msg import String

DEFAULT_TOPIC = "/robot_task"
DEFAULT_TIMEOUT_SEC = 6.0
DEFAULT_INTERVAL_SEC = 0.1
DEFAULT_MIN_COUNT = 5
DEFAULT_AFTER_MATCH_COUNT = 5


class StringPublisher(Node):
    def __init__(self, topic: str, payload: str):
        super().__init__("aiagent_string_publisher")
        self.publisher_ = self.create_publisher(String, topic, 10)
        self.msg = String()
        self.msg.data = payload
        self.topic = topic

    def publish_until_ready(
        self,
        timeout_sec: float,
        interval_sec: float,
        min_count: int,
        after_match_count: int,
    ) -> tuple[int, int]:
        deadline = time.monotonic() + max(0.5, timeout_sec)
        publish_count = 0
        max_subscription_count = 0
        first_match_publish_count = None

        while True:
            max_subscription_count = max(
                max_subscription_count, self.publisher_.get_subscription_count()
            )
            self.publisher_.publish(self.msg)
            publish_count += 1
            rclpy.spin_once(self, timeout_sec=max(0.01, interval_sec))
            max_subscription_count = max(
                max_subscription_count, self.publisher_.get_subscription_count()
            )

            if max_subscription_count > 0 and first_match_publish_count is None:
                first_match_publish_count = publish_count

            if publish_count < min_count:
                continue

            if first_match_publish_count is not None:
                if publish_count >= first_match_publish_count + after_match_count:
                    return publish_count, max_subscription_count
                continue

            if time.monotonic() >= deadline:
                return publish_count, max_subscription_count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--topic", default=DEFAULT_TOPIC)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--text")
    source.add_argument("--file")
    source.add_argument("--stdin", action="store_true")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SEC)
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SEC)
    parser.add_argument("--min-count", type=int, default=DEFAULT_MIN_COUNT)
    parser.add_argument(
        "--after-match-count", type=int, default=DEFAULT_AFTER_MATCH_COUNT
    )
    return parser.parse_args()


def load_payload(args: argparse.Namespace) -> str:
    if args.text is not None:
        return args.text
    if args.file is not None:
        return Path(args.file).read_text(encoding="utf-8")
    if args.stdin:
        payload = sys.stdin.read()
        if payload:
            return payload
    raise SystemExit("payload is empty")


def main() -> int:
    args = parse_args()
    payload = load_payload(args)
    node = None
    try:
        rclpy.init()
        node = StringPublisher(args.topic, payload)
        publish_count, max_subscription_count = node.publish_until_ready(
            timeout_sec=args.timeout,
            interval_sec=args.interval,
            min_count=max(1, args.min_count),
            after_match_count=max(1, args.after_match_count),
        )
    except (KeyboardInterrupt, ExternalShutdownException):
        print("[ros2_string_publisher] cancelled", file=sys.stderr)
        return 130
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

    if max_subscription_count > 0:
        print(
            (
                "[ros2_string_publisher] published successfully: "
                f"topic={args.topic} publishes={publish_count} "
                f"subs_seen={max_subscription_count}"
            ),
            file=sys.stderr,
        )
        return 0

    print(
        (
            "[ros2_string_publisher] published but no subscriber discovered: "
            f"topic={args.topic} publishes={publish_count}"
        ),
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
