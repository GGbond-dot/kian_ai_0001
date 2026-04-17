#!/bin/bash
# ROS2 端对端测试脚本
# 在干净 shell 中验证本仓库发布者和订阅者能否互通。

set -euo pipefail

ROS_DISTRO_NAME=${ROS_DISTRO_NAME:-jazzy}
ROS_DOMAIN_ID_VALUE=${ROS_DOMAIN_ID_VALUE:-88}
RMW_IMPLEMENTATION_VALUE=${RMW_IMPLEMENTATION_VALUE:-rmw_fastrtps_cpp}
ROS2_TOPIC_VALUE=${ROS2_TOPIC_VALUE:-/robot_task}
ROS_LOCALHOST_ONLY_VALUE=${ROS_LOCALHOST_ONLY_VALUE:-}
ROS_AUTOMATIC_DISCOVERY_RANGE_VALUE=${ROS_AUTOMATIC_DISCOVERY_RANGE_VALUE:-}
LOG_FILE=${LOG_FILE:-/tmp/aiagent_ros2_subscriber.log}
DISCOVERY_GRACE_SEC=${DISCOVERY_GRACE_SEC:-6}
ROS_HOME_DIR=${ROS_HOME_DIR:-/tmp/aiagent_ros_home_clean}

if command -v rg >/dev/null 2>&1; then
  MATCH_CMD=(rg -q)
else
  MATCH_CMD=(grep -q)
fi

cd "$(dirname "$0")/.."
PROJECT_ROOT=$(pwd)
: >"$LOG_FILE"

run_clean_bash() {
  local script="$1"
  env -i \
    HOME="$HOME" \
    USER="${USER:-}" \
    TERM="${TERM:-xterm-256color}" \
    PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin" \
    bash --noprofile --norc -lc "
      set -eo pipefail
      export AMENT_TRACE_SETUP_FILES=
      source /opt/ros/${ROS_DISTRO_NAME}/setup.bash
      set -u
      cd '${PROJECT_ROOT}'
      export ROS_HOME='${ROS_HOME_DIR}'
      export ROS_LOG_DIR='${ROS_HOME_DIR}/log'
      export ROS_DOMAIN_ID='${ROS_DOMAIN_ID_VALUE}'
      export RMW_IMPLEMENTATION='${RMW_IMPLEMENTATION_VALUE}'
      export ROS2_TOPIC='${ROS2_TOPIC_VALUE}'
      unset FASTRTPS_DEFAULT_PROFILES_FILE FASTDDS_DEFAULT_PROFILES_FILE
      unset CYCLONEDDS_URI
      if [[ -n '${ROS_LOCALHOST_ONLY_VALUE}' ]]; then
        export ROS_LOCALHOST_ONLY='${ROS_LOCALHOST_ONLY_VALUE}'
      else
        unset ROS_LOCALHOST_ONLY
      fi
      if [[ -n '${ROS_AUTOMATIC_DISCOVERY_RANGE_VALUE}' ]]; then
        export ROS_AUTOMATIC_DISCOVERY_RANGE='${ROS_AUTOMATIC_DISCOVERY_RANGE_VALUE}'
      else
        unset ROS_AUTOMATIC_DISCOVERY_RANGE
      fi
      mkdir -p \"\$ROS_LOG_DIR\"
      ${script}
    "
}

cleanup() {
  if [[ -n "${SUB_PID:-}" ]]; then
    kill -INT "$SUB_PID" 2>/dev/null || true
    wait "$SUB_PID" 2>/dev/null || true
  fi
}

trap cleanup EXIT

echo "=== ROS2 项目自测 ==="
echo "ROS_DISTRO=${ROS_DISTRO_NAME}"
echo "ROS_DOMAIN_ID=${ROS_DOMAIN_ID_VALUE}"
echo "RMW_IMPLEMENTATION=${RMW_IMPLEMENTATION_VALUE}"
echo "ROS2_TOPIC=${ROS2_TOPIC_VALUE}"
echo "ROS_LOCALHOST_ONLY=${ROS_LOCALHOST_ONLY_VALUE:-<unset>}"
echo "ROS_AUTOMATIC_DISCOVERY_RANGE=${ROS_AUTOMATIC_DISCOVERY_RANGE_VALUE:-<unset>}"
echo ""

echo "=== 启动订阅者（后台）==="
run_clean_bash "python3 -u scripts/ros2_subscriber.py" >"$LOG_FILE" 2>&1 &
SUB_PID=$!

for _ in {1..20}; do
  if [[ ! -e "/proc/$SUB_PID" ]]; then
    cat "$LOG_FILE"
    exit 1
  fi
  if "${MATCH_CMD[@]}" "等待消息中" "$LOG_FILE"; then
    break
  fi
  sleep 0.2
done

sleep "$DISCOVERY_GRACE_SEC"

echo ""
echo "=== 发布测试消息 ==="
run_clean_bash "echo '{\"task_id\":\"e2e-test\",\"action\":\"restock\",\"payload\":{\"item\":\"华为手机\",\"quantity\":1,\"src_location\":\"货架\",\"dst_location\":\"收银台\"}}' | python3 scripts/ros2_publisher.py"

for _ in {1..60}; do
  if "${MATCH_CMD[@]}" "e2e-test" "$LOG_FILE"; then
    break
  fi
  sleep 0.2
done

echo ""
echo "=== 订阅者输出 ==="
cat "$LOG_FILE"

if ! "${MATCH_CMD[@]}" "e2e-test" "$LOG_FILE"; then
  echo "未在订阅者日志中检测到测试消息"
  exit 1
fi

echo "测试完成"
