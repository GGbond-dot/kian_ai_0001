#!/bin/bash
# 启动 Humble bridge，构建 overlay，并运行仓库内的 demo action server。

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
ENV_FILE="${PROJECT_ROOT}/config/ros2_action.env"

if [[ -f "${ENV_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
fi

export ROS2_PUBLISH_MODE="${ROS2_PUBLISH_MODE:-docker_humble_bridge}"
export ROS2_ACTION_NAME="${ROS2_ACTION_NAME:-/dispatch_order}"
export ROS2_ACTION_TYPE="${ROS2_ACTION_TYPE:-robot_task_interfaces/action/DispatchOrder}"
export ROS2_HUMBLE_HOST_WS="${ROS2_HUMBLE_HOST_WS:-${PROJECT_ROOT}/ros2_ws}"
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-10}"
export RMW_IMPLEMENTATION="${RMW_IMPLEMENTATION:-rmw_fastrtps_cpp}"
export ROS_AUTOMATIC_DISCOVERY_RANGE="${ROS_AUTOMATIC_DISCOVERY_RANGE:-SUBNET}"

cd "${PROJECT_ROOT}"

echo "=== Robot Action Demo Server ==="
echo "ROS2_ACTION_NAME=${ROS2_ACTION_NAME}"
echo "ROS2_ACTION_TYPE=${ROS2_ACTION_TYPE}"
echo "ROS2_HUMBLE_HOST_WS=${ROS2_HUMBLE_HOST_WS}"
echo "ROS_DOMAIN_ID=${ROS_DOMAIN_ID}"
echo "RMW_IMPLEMENTATION=${RMW_IMPLEMENTATION}"
echo "ROS_AUTOMATIC_DISCOVERY_RANGE=${ROS_AUTOMATIC_DISCOVERY_RANGE}"
echo ""

python3 scripts/docker_humble_bridge.py start
python3 scripts/docker_humble_bridge.py build-overlay
exec python3 scripts/docker_humble_bridge.py run-demo-server
