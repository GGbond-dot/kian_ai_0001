#!/usr/bin/env python3
"""
Manage a persistent ROS 2 Humble Docker container and publish String payloads.

Examples:
    python3 scripts/docker_humble_bridge.py start
    python3 scripts/docker_humble_bridge.py topics
    python3 scripts/docker_humble_bridge.py pub --topic /hello --text "hello"
    python3 scripts/docker_humble_bridge.py pub --topic /robot_task --file payload.json
    python3 scripts/docker_humble_bridge.py action-send --stdin \
        --action-name /dispatch_order \
        --action-type robot_task_interfaces/action/DispatchOrder
    python3 scripts/docker_humble_bridge.py shell
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

CONTAINER_NAME = os.environ.get("ROS2_HUMBLE_CONTAINER_NAME", "ros2-humble-bridge")
IMAGE = os.environ.get("ROS2_HUMBLE_IMAGE", "ros:humble-ros-base")
ROS_DOMAIN_ID = os.environ.get("ROS_DOMAIN_ID", "10")
RMW_IMPLEMENTATION = os.environ.get("RMW_IMPLEMENTATION", "rmw_fastrtps_cpp")
DISCOVERY_RANGE = os.environ.get("ROS_AUTOMATIC_DISCOVERY_RANGE", "SUBNET")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTAINER_WORKSPACE = "/workspace_host"
DEFAULT_PROJECT_OVERLAY_WORKSPACE = PROJECT_ROOT / "ros2_ws"
DEFAULT_HOST_OVERLAY_WORKSPACE = Path.home() / "ros2_ws"
HOST_OVERLAY_WORKSPACE_RAW = os.environ.get("ROS2_HUMBLE_HOST_WS", "").strip()
HOST_OVERLAY_WORKSPACE = (
    Path(HOST_OVERLAY_WORKSPACE_RAW).expanduser().resolve()
    if HOST_OVERLAY_WORKSPACE_RAW
    else (
        DEFAULT_PROJECT_OVERLAY_WORKSPACE.resolve()
        if DEFAULT_PROJECT_OVERLAY_WORKSPACE.exists()
        else (
            DEFAULT_HOST_OVERLAY_WORKSPACE.resolve()
            if DEFAULT_HOST_OVERLAY_WORKSPACE.exists()
            else None
        )
    )
)
CONTAINER_OVERLAY_WORKSPACE = "/ros2_overlay_ws"
EXTRA_SETUP_SCRIPTS = [
    value.strip()
    for value in os.environ.get("ROS2_HUMBLE_SETUP_SCRIPTS", "").split(":")
    if value.strip()
]
PUBLISHER_SCRIPT = f"{CONTAINER_WORKSPACE}/scripts/ros2_string_publisher.py"
INT32_PUBLISHER_SCRIPT = f"{CONTAINER_WORKSPACE}/scripts/ros2_int32_publisher.py"
ACTION_CLIENT_SCRIPT = f"{CONTAINER_WORKSPACE}/scripts/ros2_action_client.py"


def run(
    cmd: list[str],
    *,
    check: bool = True,
    capture_output: bool = False,
    text: bool = True,
    stdin=None,
):
    return subprocess.run(
        cmd,
        check=check,
        capture_output=capture_output,
        text=text,
        stdin=stdin,
    )


def container_exists() -> bool:
    result = run(
        ["docker", "container", "inspect", CONTAINER_NAME],
        check=False,
        capture_output=True,
    )
    return result.returncode == 0


def container_running() -> bool:
    result = run(
        ["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER_NAME],
        check=False,
        capture_output=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def expected_mounts() -> dict[str, str]:
    mounts = {CONTAINER_WORKSPACE: str(PROJECT_ROOT.resolve())}
    if HOST_OVERLAY_WORKSPACE is not None and HOST_OVERLAY_WORKSPACE.exists():
        mounts[CONTAINER_OVERLAY_WORKSPACE] = str(HOST_OVERLAY_WORKSPACE)
    return mounts


def container_mounts() -> dict[str, str]:
    result = run(
        ["docker", "inspect", CONTAINER_NAME],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        return {}
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}
    if not payload or not isinstance(payload[0], dict):
        return {}
    mounts = payload[0].get("Mounts") or []
    mount_map: dict[str, str] = {}
    for mount in mounts:
        if not isinstance(mount, dict):
            continue
        destination = str(mount.get("Destination") or "").strip()
        source = str(mount.get("Source") or "").strip()
        if destination and source:
            mount_map[destination] = source
    return mount_map


def container_needs_recreate() -> bool:
    actual_mounts = container_mounts()
    required_mounts = expected_mounts()
    if not actual_mounts:
        return False
    for destination, source in required_mounts.items():
        if actual_mounts.get(destination) != source:
            return True
    return False


def remove_container() -> None:
    run(["docker", "rm", "-f", CONTAINER_NAME], check=False)


def _build_mount_args() -> list[str]:
    mount_args = ["-v", f"{PROJECT_ROOT}:{CONTAINER_WORKSPACE}"]
    if HOST_OVERLAY_WORKSPACE is not None and HOST_OVERLAY_WORKSPACE.exists():
        mount_args.extend(
            ["-v", f"{HOST_OVERLAY_WORKSPACE}:{CONTAINER_OVERLAY_WORKSPACE}"]
        )
    return mount_args


def _build_ros_source_prefix() -> str:
    setup_scripts = ["/opt/ros/humble/setup.bash"]
    overlay_setup = f"{CONTAINER_OVERLAY_WORKSPACE}/install/setup.bash"
    setup_scripts.append(overlay_setup)
    setup_scripts.extend(EXTRA_SETUP_SCRIPTS)

    commands = []
    seen = set()
    for script in setup_scripts:
        if not script or script in seen:
            continue
        seen.add(script)
        quoted = shlex.quote(script)
        commands.append(f"if [ -f {quoted} ]; then source {quoted}; fi")
    return " && ".join(commands)


def ensure_container() -> None:
    if container_exists() and container_needs_recreate():
        print(
            (
                "[docker_humble_bridge] 检测到容器挂载配置已变化，"
                f"正在重建 {CONTAINER_NAME}"
            ),
            file=sys.stderr,
        )
        remove_container()

    if container_running():
        return

    if container_exists():
        run(["docker", "start", CONTAINER_NAME])
        return

    hold_cmd = "trap exit TERM INT; while sleep 3600; do :; done"
    run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            CONTAINER_NAME,
            "--net=host",
            "--ipc=host",
            "-e",
            f"RMW_IMPLEMENTATION={RMW_IMPLEMENTATION}",
            "-e",
            f"ROS_DOMAIN_ID={ROS_DOMAIN_ID}",
            "-e",
            f"ROS_AUTOMATIC_DISCOVERY_RANGE={DISCOVERY_RANGE}",
            *_build_mount_args(),
            IMAGE,
            "bash",
            "-lc",
            hold_cmd,
        ]
    )


def docker_exec_bash(command: str, *, interactive: bool = False, use_stdin: bool = False) -> int:
    ensure_container()
    source_prefix = _build_ros_source_prefix()
    exec_cmd = [
        "docker",
        "exec",
        "-e",
        f"RMW_IMPLEMENTATION={RMW_IMPLEMENTATION}",
        "-e",
        f"ROS_DOMAIN_ID={ROS_DOMAIN_ID}",
        "-e",
        f"ROS_AUTOMATIC_DISCOVERY_RANGE={DISCOVERY_RANGE}",
    ]
    if interactive:
        exec_cmd.extend(["-i", "-t"])
    elif use_stdin:
        exec_cmd.append("-i")
    exec_cmd.extend(
        [
            CONTAINER_NAME,
            "bash",
            "-lc",
            f"{source_prefix} && {command}",
        ]
    )
    stdin = sys.stdin if use_stdin else None
    return run(exec_cmd, check=False, stdin=stdin).returncode


def graph_query_command(command: str) -> str:
    return (
        "ros2 daemon stop >/dev/null 2>&1 || true; "
        "ros2 daemon start >/dev/null 2>&1 || true; "
        "sleep 2; "
        f"{command}"
    )


def cmd_start(_: argparse.Namespace) -> int:
    ensure_container()
    overlay = (
        str(HOST_OVERLAY_WORKSPACE)
        if HOST_OVERLAY_WORKSPACE is not None and HOST_OVERLAY_WORKSPACE.exists()
        else "<none>"
    )
    print(
        (
            f"container={CONTAINER_NAME} image={IMAGE} domain={ROS_DOMAIN_ID} "
            f"rmw={RMW_IMPLEMENTATION} overlay_ws={overlay}"
        )
    )
    return 0


def cmd_shell(_: argparse.Namespace) -> int:
    return docker_exec_bash("exec bash", interactive=True)


def cmd_topics(_: argparse.Namespace) -> int:
    return docker_exec_bash(graph_query_command("ros2 topic list -t"))


def cmd_nodes(_: argparse.Namespace) -> int:
    return docker_exec_bash(graph_query_command("ros2 node list"))


def cmd_actions(_: argparse.Namespace) -> int:
    return docker_exec_bash(graph_query_command("ros2 action list -t"))


def cmd_action_info(args: argparse.Namespace) -> int:
    return docker_exec_bash(
        graph_query_command(f"ros2 action info {shlex.quote(args.action_name)}"),
        interactive=True,
    )


def cmd_listener(_: argparse.Namespace) -> int:
    install_cmd = "apt-get update && apt-get install -y ros-humble-demo-nodes-cpp >/dev/null"
    rc = docker_exec_bash(install_cmd)
    if rc != 0:
        return rc
    return docker_exec_bash("ros2 run demo_nodes_cpp listener", interactive=True)


def cmd_pub(args: argparse.Namespace) -> int:
    ensure_container()
    publisher_cmd = [
        "python3",
        PUBLISHER_SCRIPT,
        "--topic",
        args.topic,
    ]
    if args.text is not None:
        publisher_cmd.extend(["--text", args.text])
    elif args.file is not None:
        host_path = Path(args.file).expanduser().resolve()
        try:
            container_relpath = host_path.relative_to(PROJECT_ROOT.resolve())
        except ValueError:
            print(
                f"file must be inside project root: {PROJECT_ROOT}",
                file=sys.stderr,
            )
            return 1
        publisher_cmd.extend(["--file", f"{CONTAINER_WORKSPACE}/{container_relpath}"])
    else:
        publisher_cmd.append("--stdin")
    publisher_cmd.extend(
        [
            "--timeout",
            str(args.timeout),
            "--interval",
            str(args.interval),
            "--min-count",
            str(args.min_count),
            "--after-match-count",
            str(args.after_match_count),
        ]
    )
    return docker_exec_bash(
        " ".join(shlex.quote(part) for part in publisher_cmd),
        use_stdin=args.stdin,
    )


def cmd_pub_int(args: argparse.Namespace) -> int:
    ensure_container()
    publisher_cmd = [
        "python3",
        INT32_PUBLISHER_SCRIPT,
        "--topic",
        args.topic,
        "--value",
        str(args.value),
        "--timeout",
        str(args.timeout),
        "--interval",
        str(args.interval),
        "--min-count",
        str(args.min_count),
        "--after-match-count",
        str(args.after_match_count),
    ]
    return docker_exec_bash(
        " ".join(shlex.quote(part) for part in publisher_cmd),
    )


def cmd_build_overlay(_: argparse.Namespace) -> int:
    if HOST_OVERLAY_WORKSPACE is None or not HOST_OVERLAY_WORKSPACE.exists():
        print(
            "[docker_humble_bridge] ❌ 未找到 overlay 工作区，请设置 ROS2_HUMBLE_HOST_WS",
            file=sys.stderr,
        )
        return 1
    return docker_exec_bash(
        (
            "if ! command -v colcon >/dev/null 2>&1; then "
            "echo '[docker_humble_bridge] ❌ 容器内未安装 colcon，请先在容器内安装 python3-colcon-common-extensions' >&2; "
            "exit 1; "
            "fi; "
            f"cd {shlex.quote(CONTAINER_OVERLAY_WORKSPACE)} && colcon build"
        ),
        interactive=True,
    )


def cmd_action_send(args: argparse.Namespace) -> int:
    ensure_container()
    action_cmd = [
        "python3",
        ACTION_CLIENT_SCRIPT,
        "--action-name",
        args.action_name,
        "--action-type",
        args.action_type,
        "--goal-mode",
        args.goal_mode,
        "--server-timeout",
        str(args.server_timeout),
        "--accept-timeout",
        str(args.accept_timeout),
        "--result-timeout",
        str(args.result_timeout),
    ]
    if args.text is not None:
        action_cmd.extend(["--text", args.text])
    elif args.file is not None:
        host_path = Path(args.file).expanduser().resolve()
        try:
            container_relpath = host_path.relative_to(PROJECT_ROOT.resolve())
        except ValueError:
            print(
                f"file must be inside project root: {PROJECT_ROOT}",
                file=sys.stderr,
            )
            return 1
        action_cmd.extend(["--file", f"{CONTAINER_WORKSPACE}/{container_relpath}"])
    else:
        action_cmd.append("--stdin")
    return docker_exec_bash(
        " ".join(shlex.quote(part) for part in action_cmd),
        use_stdin=args.stdin,
    )


def cmd_run_demo_server(_: argparse.Namespace) -> int:
    return docker_exec_bash(
        "ros2 run robot_action_demo dispatch_server",
        interactive=True,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("start")
    p.set_defaults(func=cmd_start)

    p = sub.add_parser("shell")
    p.set_defaults(func=cmd_shell)

    p = sub.add_parser("topics")
    p.set_defaults(func=cmd_topics)

    p = sub.add_parser("nodes")
    p.set_defaults(func=cmd_nodes)

    p = sub.add_parser("actions")
    p.set_defaults(func=cmd_actions)

    p = sub.add_parser("action-info")
    p.add_argument("--action-name", default="/dispatch_order")
    p.set_defaults(func=cmd_action_info)

    p = sub.add_parser("listen-demo")
    p.set_defaults(func=cmd_listener)

    p = sub.add_parser("build-overlay")
    p.set_defaults(func=cmd_build_overlay)

    p = sub.add_parser("pub")
    p.add_argument("--topic", default="/robot_task")
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--text")
    source.add_argument("--file")
    source.add_argument("--stdin", action="store_true")
    p.add_argument("--timeout", type=float, default=6.0)
    p.add_argument("--interval", type=float, default=0.1)
    p.add_argument("--min-count", type=int, default=5)
    p.add_argument("--after-match-count", type=int, default=5)
    p.set_defaults(func=cmd_pub)

    p = sub.add_parser("pub-int")
    p.add_argument("--topic", default="/drone_command")
    p.add_argument("--value", type=int, required=True)
    p.add_argument("--timeout", type=float, default=30.0)
    p.add_argument("--interval", type=float, default=0.1)
    p.add_argument("--min-count", type=int, default=300)
    p.add_argument("--after-match-count", type=int, default=300)
    p.set_defaults(func=cmd_pub_int)

    p = sub.add_parser("action-send")
    p.add_argument("--action-name", default="/dispatch_order")
    p.add_argument(
        "--action-type",
        default="robot_task_interfaces/action/DispatchOrder",
    )
    p.add_argument(
        "--goal-mode",
        choices=["merged_task_payload", "raw", "payload_only"],
        default="merged_task_payload",
    )
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--text")
    source.add_argument("--file")
    source.add_argument("--stdin", action="store_true")
    p.add_argument("--server-timeout", type=float, default=8.0)
    p.add_argument("--accept-timeout", type=float, default=8.0)
    p.add_argument("--result-timeout", type=float, default=0.0)
    p.set_defaults(func=cmd_action_send)

    p = sub.add_parser("run-demo-server")
    p.set_defaults(func=cmd_run_demo_server)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except FileNotFoundError as exc:
        missing = exc.filename or str(exc)
        print(f"[docker_humble_bridge] ❌ 未找到命令: {missing}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        cmd_text = " ".join(str(part) for part in exc.cmd)
        print(
            (
                "[docker_humble_bridge] ❌ 命令执行失败: "
                f"exit_code={exc.returncode} cmd={cmd_text}"
            ),
            file=sys.stderr,
        )
        return exc.returncode or 1


if __name__ == "__main__":
    raise SystemExit(main())
