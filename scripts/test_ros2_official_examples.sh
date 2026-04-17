#!/bin/bash
# 只验证 ROS2 官方 minimal publisher/subscriber 是否能互通。

set -euo pipefail

ROS_DISTRO_NAME=${ROS_DISTRO_NAME:-jazzy}
ROS_DOMAIN_ID_VALUE=${ROS_DOMAIN_ID_VALUE:-88}
RMW_IMPLEMENTATION_VALUE=${RMW_IMPLEMENTATION_VALUE:-rmw_fastrtps_cpp}
DISCOVERY_GRACE_SEC=${DISCOVERY_GRACE_SEC:-6}
SUB_LOG_FILE=${SUB_LOG_FILE:-/tmp/ros2_official_subscriber.log}
ROS_HOME_DIR=${ROS_HOME_DIR:-/tmp/ros2_official_home}

if command -v rg >/dev/null 2>&1; then
  MATCH_CMD=(rg -q)
else
  MATCH_CMD=(grep -q)
fi

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
      export ROS_HOME='${ROS_HOME_DIR}'
      export ROS_LOG_DIR='${ROS_HOME_DIR}/log'
      export ROS_DOMAIN_ID='${ROS_DOMAIN_ID_VALUE}'
      export RMW_IMPLEMENTATION='${RMW_IMPLEMENTATION_VALUE}'
      unset FASTRTPS_DEFAULT_PROFILES_FILE FASTDDS_DEFAULT_PROFILES_FILE
      unset ROS_LOCALHOST_ONLY ROS_AUTOMATIC_DISCOVERY_RANGE
      unset CYCLONEDDS_URI
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

: >"$SUB_LOG_FILE"

echo "=== ROS2 官方示例自测 ==="
echo "ROS_DISTRO=${ROS_DISTRO_NAME}"
echo "ROS_DOMAIN_ID=${ROS_DOMAIN_ID_VALUE}"
echo "RMW_IMPLEMENTATION=${RMW_IMPLEMENTATION_VALUE}"
echo "SUB_LOG_FILE=${SUB_LOG_FILE}"
echo ""

echo "=== 启动官方 subscriber（后台）==="
run_clean_bash "ros2 run examples_rclpy_minimal_subscriber subscriber_member_function" \
  >"$SUB_LOG_FILE" 2>&1 &
SUB_PID=$!

sleep "$DISCOVERY_GRACE_SEC"

if [[ ! -e "/proc/$SUB_PID" ]]; then
  echo "subscriber 进程提前退出："
  cat "$SUB_LOG_FILE"
  exit 1
fi

echo "=== 启动官方 publisher ==="
set +e
run_clean_bash "timeout 8 ros2 run examples_rclpy_minimal_publisher publisher_member_function"
PUBLISHER_STATUS=$?
set -e

if [[ "$PUBLISHER_STATUS" -ne 0 ]]; then
  echo ""
  echo "publisher 已退出，exit_code=${PUBLISHER_STATUS}。继续检查 subscriber 是否实际收到消息。"
fi

for _ in {1..60}; do
  if "${MATCH_CMD[@]}" "I heard:" "$SUB_LOG_FILE"; then
    break
  fi
  sleep 0.2
done

echo ""
echo "=== subscriber 输出 ==="
cat "$SUB_LOG_FILE"

if ! "${MATCH_CMD[@]}" "I heard:" "$SUB_LOG_FILE"; then
  echo ""
  echo "官方示例未互通。问题已定位到 ROS2/DDS/系统环境，而不是本仓库业务脚本。"
  exit 1
fi

echo ""
echo "官方示例互通正常。"
