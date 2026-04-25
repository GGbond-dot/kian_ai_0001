# ROS2 Native Humble Migration Notes

本次修改目标：PC 和开发板都已经安装 ROS2 Humble 后，项目默认不再依赖 Docker Humble bridge，直接使用本机 ROS2 通讯。

## 运行链路调整

- `src/mcp/tools/robot_dispatch/tools.py`
  - `drone.takeoff` / `drone.land` 不再调用 `scripts/docker_humble_bridge.py pub-int`。
  - 现在直接后台启动 `scripts/ros2_int32_publisher.py`，向 `/drone_command` 发布 `std_msgs/UInt8` 指令码。
  - 指令码保持不变：`1` 起飞，`2` 降落，`3` 紧急降落。

- `scripts/ros2_int32_publisher.py`
  - 增加本机 ROS2 环境自动探测。
  - 即使终端没有手动 `source /opt/ros/humble/setup.bash`，脚本也会尝试通过 `/opt/ros/<distro>` 补齐 `rclpy` 环境。
  - 节点名从 `docker_uint8_publisher` 改为 `aiagent_uint8_publisher`。

- `scripts/ros2_string_publisher.py`
  - 增加本机 ROS2 环境自动探测。
  - 节点名从 `docker_string_publisher` 改为 `aiagent_string_publisher`。

- `scripts/ros2_action_client.py`
  - 增加本机 ROS2 环境自动探测，便于直接使用本机 Humble action client。

- `src/utils/ros2_env.py`
  - ROS2 Python 包探测兼容 Ubuntu 常见的 `local/lib/python*/dist-packages`。
  - `reexec=True` 时会用补齐后的 ROS2 环境重启当前脚本，确保 `PYTHONPATH` 和 `LD_LIBRARY_PATH` 在进程启动阶段生效。

## 启动脚本调整

- `scripts/run_local_action_agent.sh`
  - 默认 `ROS2_PUBLISH_MODE=auto`。
  - 删除默认启动 Docker bridge 的步骤。
  - 启动前会 source `/opt/ros/humble/setup.bash`。
  - 如果存在 `${ROS2_HUMBLE_HOST_WS}/install/setup.bash`，也会自动 source overlay 工作区。

- `scripts/run_robot_action_demo_server.sh`
  - 默认 `ROS2_PUBLISH_MODE=auto`。
  - 删除 Docker bridge 启动、容器内构建、容器内运行 demo server。
  - 现在直接在本机 `${ROS2_HUMBLE_HOST_WS}` 下 `colcon build`，然后 `ros2 run robot_action_demo dispatch_server`。

## 测试脚本调整

- `scripts/test_ros2_e2e.sh`
  - 默认 ROS 发行版从 `jazzy` 改为 `humble`。

- `scripts/test_ros2_official_examples.sh`
  - 默认 ROS 发行版从 `jazzy` 改为 `humble`。

## 仍然保留的 Docker 文件

- `scripts/docker_humble_bridge.py` 暂时保留。
  - 它现在不是默认运行链路。
  - 如果以后又遇到 PC 和开发板 ROS2 版本不一致，仍可临时切回 Docker bridge。

## Markdown 文件整理

项目根目录原有 Markdown 文件已移动到 `project_markdown/`：

- `README.md`
- `README.en.md`
- `ROS2_DEBUG_NOTES.md`
- `DRONE_AGENT_GUIDE.md`
- `MAPPING_VIEW_FEATURE_PLAN.md`
- `WEB_UI_ARCHITECTURE_PLAN.md`

以后新增项目说明、调试记录、方案设计类 Markdown，建议统一放在 `project_markdown/`。
