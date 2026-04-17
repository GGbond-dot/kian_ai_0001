#!/usr/bin/env python3
"""
Publish std_msgs/UInt8 with a short discovery window and repeated publication.

Usage:
    python3 scripts/ros2_int32_publisher.py --topic /drone_command --value 1
    python3 scripts/ros2_int32_publisher.py --topic /drone_command --value 1 \
        --timeout 30 --interval 0.1 --min-count 300 --after-match-count 300
"""

from __future__ import annotations

import argparse
import sys
import time

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from std_msgs.msg import UInt8

DEFAULT_TOPIC = "/drone_command"
DEFAULT_TIMEOUT_SEC = 30.0
DEFAULT_INTERVAL_SEC = 0.1
DEFAULT_MIN_COUNT = 300
DEFAULT_AFTER_MATCH_COUNT = 300


class Int32Publisher(Node):
    def __init__(self, topic: str, value: int):
        super().__init__("docker_uint8_publisher")
        self.publisher_ = self.create_publisher(UInt8, topic, 10)
        if not 0 <= value <= 255:
            raise ValueError(f"UInt8 value must be in [0, 255], got {value}")
        self.msg = UInt8()
        self.msg.data = value
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
    parser.add_argument("--value", type=int, required=True)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SEC)
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SEC)
    parser.add_argument("--min-count", type=int, default=DEFAULT_MIN_COUNT)
    parser.add_argument(
        "--after-match-count", type=int, default=DEFAULT_AFTER_MATCH_COUNT
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    node = None
    try:
        rclpy.init()
        node = Int32Publisher(args.topic, args.value)
        publish_count, max_subscription_count = node.publish_until_ready(
            timeout_sec=args.timeout,
            interval_sec=args.interval,
            min_count=max(1, args.min_count),
            after_match_count=max(1, args.after_match_count),
        )
    except (KeyboardInterrupt, ExternalShutdownException):
        print("[ros2_uint8_publisher] cancelled", file=sys.stderr)
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
                "[ros2_uint8_publisher] published successfully: "
                f"topic={args.topic} value={args.value} "
                f"publishes={publish_count} subs_seen={max_subscription_count}"
            ),
            file=sys.stderr,
        )
        return 0

    print(
        (
            "[ros2_uint8_publisher] published but no subscriber discovered: "
            f"topic={args.topic} value={args.value} publishes={publish_count}"
        ),
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
