#!/bin/bash
# 一键启动本地 AI + ROS2 Humble Docker bridge + Action 派单环境。
# 默认读取 config/ros2_action.env 中的自定义配置；若不存在则使用脚本内默认值。

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
ENV_FILE="${PROJECT_ROOT}/config/ros2_action.env"
DEFAULT_WORKSPACE_PATH="${PROJECT_ROOT}/ros2_ws"

if [[ -f "${ENV_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
fi

export ROS2_DISPATCH_TRANSPORT="${ROS2_DISPATCH_TRANSPORT:-action}"
export ROS2_PUBLISH_MODE="${ROS2_PUBLISH_MODE:-docker_humble_bridge}"
export ROS2_ACTION_NAME="${ROS2_ACTION_NAME:-/dispatch_order}"
export ROS2_ACTION_TYPE="${ROS2_ACTION_TYPE:-robot_task_interfaces/action/DispatchOrder}"
export ROS2_ACTION_GOAL_MODE="${ROS2_ACTION_GOAL_MODE:-merged_task_payload}"
if [[ -z "${ROS2_HUMBLE_HOST_WS:-}" && -d "${DEFAULT_WORKSPACE_PATH}" ]]; then
  export ROS2_HUMBLE_HOST_WS="${DEFAULT_WORKSPACE_PATH}"
else
  export ROS2_HUMBLE_HOST_WS="${ROS2_HUMBLE_HOST_WS:-/home/orangepi/ros2_ws}"
fi
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-10}"
export RMW_IMPLEMENTATION="${RMW_IMPLEMENTATION:-rmw_fastrtps_cpp}"
export ROS_AUTOMATIC_DISCOVERY_RANGE="${ROS_AUTOMATIC_DISCOVERY_RANGE:-SUBNET}"
export AIAGENT_MODE="${AIAGENT_MODE:-cli}"
export AIAGENT_PROTOCOL="${AIAGENT_PROTOCOL:-local}"

cd "${PROJECT_ROOT}"

echo "=== AI Agent Action Launch ==="
echo "ROS2_DISPATCH_TRANSPORT=${ROS2_DISPATCH_TRANSPORT}"
echo "ROS2_PUBLISH_MODE=${ROS2_PUBLISH_MODE}"
echo "ROS2_ACTION_NAME=${ROS2_ACTION_NAME}"
echo "ROS2_ACTION_TYPE=${ROS2_ACTION_TYPE}"
echo "ROS2_HUMBLE_HOST_WS=${ROS2_HUMBLE_HOST_WS}"
echo "ROS_DOMAIN_ID=${ROS_DOMAIN_ID}"
echo "RMW_IMPLEMENTATION=${RMW_IMPLEMENTATION}"
echo "ROS_AUTOMATIC_DISCOVERY_RANGE=${ROS_AUTOMATIC_DISCOVERY_RANGE}"
echo "AIAGENT_MODE=${AIAGENT_MODE}"
echo "AIAGENT_PROTOCOL=${AIAGENT_PROTOCOL}"
echo ""

python3 scripts/docker_humble_bridge.py start
exec python3 main.py --mode "${AIAGENT_MODE}" --protocol "${AIAGENT_PROTOCOL}" "$@"
