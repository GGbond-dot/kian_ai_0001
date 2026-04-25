#!/bin/bash
# 使用本机 ROS2 Humble 构建 overlay，并运行仓库内的 demo action server。

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
ENV_FILE="${PROJECT_ROOT}/config/ros2_action.env"

if [[ -f "${ENV_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
fi

export ROS2_PUBLISH_MODE="${ROS2_PUBLISH_MODE:-auto}"
export ROS2_ACTION_NAME="${ROS2_ACTION_NAME:-/dispatch_order}"
export ROS2_ACTION_TYPE="${ROS2_ACTION_TYPE:-robot_task_interfaces/action/DispatchOrder}"
export ROS2_HUMBLE_HOST_WS="${ROS2_HUMBLE_HOST_WS:-${PROJECT_ROOT}/ros2_ws}"
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-10}"
export RMW_IMPLEMENTATION="${RMW_IMPLEMENTATION:-rmw_fastrtps_cpp}"
export ROS_AUTOMATIC_DISCOVERY_RANGE="${ROS_AUTOMATIC_DISCOVERY_RANGE:-SUBNET}"

cd "${PROJECT_ROOT}"

if [[ -f "/opt/ros/humble/setup.bash" ]]; then
  # shellcheck disable=SC1091
  source /opt/ros/humble/setup.bash
fi

echo "=== Robot Action Demo Server ==="
echo "ROS2_ACTION_NAME=${ROS2_ACTION_NAME}"
echo "ROS2_ACTION_TYPE=${ROS2_ACTION_TYPE}"
echo "ROS2_HUMBLE_HOST_WS=${ROS2_HUMBLE_HOST_WS}"
echo "ROS_DOMAIN_ID=${ROS_DOMAIN_ID}"
echo "RMW_IMPLEMENTATION=${RMW_IMPLEMENTATION}"
echo "ROS_AUTOMATIC_DISCOVERY_RANGE=${ROS_AUTOMATIC_DISCOVERY_RANGE}"
echo ""

if ! command -v colcon >/dev/null 2>&1; then
  echo "未找到 colcon，请先安装 python3-colcon-common-extensions" >&2
  exit 1
fi

cd "${ROS2_HUMBLE_HOST_WS}"
colcon build
# shellcheck disable=SC1090
source "${ROS2_HUMBLE_HOST_WS}/install/setup.bash"
exec ros2 run robot_action_demo dispatch_server
