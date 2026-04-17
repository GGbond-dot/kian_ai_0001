# ROS2 联调与排障记录

更新日期：2026-03-20

## 结论

当前机器上的 ROS2 Jazzy、WSL 网络、Fast DDS 都是可用的。

最终验证通过的条件：

- 使用干净 shell，不继承 `~/.bashrc` 里的全局 DDS 配置
- 使用合法的小 `ROS_DOMAIN_ID`，当前验证值为 `88`
- 在共享网络环境里，本机自测时使用 `ROS_LOCALHOST_ONLY=1`
- 本机自测时使用独占 topic，例如 `/robot_task_miao_selftest`
- publisher 在首次发现 subscriber 后继续多发几条，避免 discovery 刚收敛时丢首包

## 最终通过的命令

```bash
cd /home/miao/aiagent
ROS_DOMAIN_ID_VALUE=88 \
RMW_IMPLEMENTATION_VALUE=rmw_fastrtps_cpp \
ROS_LOCALHOST_ONLY_VALUE=1 \
ROS2_TOPIC_VALUE=/robot_task_miao_selftest \
bash scripts/test_ros2_e2e.sh
```

## AI 接入实测步骤

目标：

- AI 主程序真正调用 `dispatch_phone_order`
- 主程序发出 ROS2 消息
- 本地 subscriber 实际收到消息

### 终端 1：启动本地 subscriber

建议用干净 shell：

```bash
bash --noprofile --norc
cd /home/miao/aiagent
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=88
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
export ROS_LOCALHOST_ONLY=1
export ROS2_TOPIC=/robot_task_miao_selftest
unset FASTRTPS_DEFAULT_PROFILES_FILE FASTDDS_DEFAULT_PROFILES_FILE
python3 scripts/ros2_subscriber.py
```

### 终端 2：启动 AI 主程序

主程序必须在同一个 ROS 环境下启动，因为它会把当前环境透传给发布子进程。

```bash
bash --noprofile --norc
cd /home/miao/aiagent
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=88
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
export ROS_LOCALHOST_ONLY=1
export ROS2_TOPIC=/robot_task_miao_selftest
unset FASTRTPS_DEFAULT_PROFILES_FILE FASTDDS_DEFAULT_PROFILES_FILE
python main.py --mode cli --protocol local
```

如果你平时是虚拟环境或 conda 环境启动项目，就在导出以上变量之后，再切回那个能正常运行项目依赖的 Python 环境。

### 对 AI 说什么

推荐直接说清楚型号和数量，例如：

- `我要买一台小米手机`
- `确认`

或者：

- `给我拿两台华为手机`
- `好的`

说明：

- 工具侧是“确认后才真正发布”
- 没有确认词时，可能只生成待确认提案，不会真正发 ROS

### 如何判断成功

终端 1 的 subscriber 应该打印：

- `收到 ROS2 消息！`
- `task_id`
- `action: restock`
- 手机型号和数量

### 为什么主程序要在同一套环境里启动

`src/mcp/tools/robot_dispatch/tools.py` 中的 `_build_ros_publish_env()` 会直接复制当前进程环境，再启动 `scripts/ros2_publisher.py`。

因此：

- 如果主程序启动时没有导出 `ROS_DOMAIN_ID`
- 或者没有导出 `ROS_LOCALHOST_ONLY`
- 或者没有导出你自己的 `ROS2_TOPIC`

那么发布子进程也不会自动拿到这些值。

## 这次真正定位到的问题

### 1. 不是 ROS2 坏了

官方最小示例已经验证通过：

```bash
cd /home/miao/aiagent
ROS_DOMAIN_ID_VALUE=88 \
RMW_IMPLEMENTATION_VALUE=rmw_fastrtps_cpp \
bash scripts/test_ros2_official_examples.sh
```

这说明：

- WSL 网络正常
- ROS2 Jazzy 正常
- Fast DDS 正常

### 2. `ROS_DOMAIN_ID=242` 是错误配置

`ROS_DOMAIN_ID` 不是越大越安全。DDS 会把它映射到 UDP 端口。

- 大于 `232` 时可能导致端口越界
- `242` 在这台机器上会直接让节点启动失败

后续统一使用小于 `233` 的值。当前推荐 `88`。

### 3. 共享网络会污染 `subs_seen`

之前出现过这种情况：

- publisher 打印 `subs_seen=1`
- 但本地 subscriber 日志里没有收到消息

这说明 `subs_seen` 看到的可能是共享网络里别人的 subscriber，不一定是当前测试窗口里的那个进程。

因此在多人共网环境里：

- 本机自测优先使用 `ROS_LOCALHOST_ONLY=1`
- 或者使用独占 topic
- 或者换独占 `ROS_DOMAIN_ID`

### 4. 全局 DDS 配置会污染测试结果

用户 shell 里存在全局配置：

- `FASTRTPS_DEFAULT_PROFILES_FILE=~/fastdds_unicast.xml`
- `RMW_IMPLEMENTATION=rmw_fastrtps_cpp`
- `ROS_DOMAIN_ID=42`

其中 `fastdds_unicast.xml` 里存在写死 IP 的单播配置，会影响所有从普通 shell 启动的 ROS 进程。

所以后续排障时：

- 不要直接相信普通终端里的 ROS 行为
- 要优先在干净 shell 里测
- 先清掉 `FASTRTPS_DEFAULT_PROFILES_FILE` 和 `FASTDDS_DEFAULT_PROFILES_FILE`

## 代码改动说明

### 1. `scripts/ros2_publisher.py`

已收敛成接近官方 minimal publisher 的最小实现。

当前保留的行为：

- 从 stdin 读 JSON
- 使用 `rclpy` 直接发 `std_msgs/msg/String`
- 在 discovery 窗口内重复发布
- 首次发现 subscriber 后，继续额外多发几条

额外多发条数由以下环境变量控制：

```bash
ROS2_PUBLISH_AFTER_MATCH_COUNT
```

默认值是 `5`。

### 2. `scripts/ros2_subscriber.py`

已收敛成接近官方 minimal subscriber 的最小实现。

当前保留的行为：

- 订阅 `ROS2_TOPIC`
- 打印原始 JSON
- 打印业务字段

### 3. `scripts/test_ros2_official_examples.sh`

新增，用来只验证 ROS 官方 publisher/subscriber 是否互通。

用途：

- 先切分“ROS 基础环境问题”还是“项目脚本问题”
- 后续 AI 改 ROS 逻辑前，先跑它

### 4. `scripts/test_ros2_e2e.sh`

已改成在干净 shell 中运行项目 publisher/subscriber。

支持这些环境变量：

- `ROS_DOMAIN_ID_VALUE`
- `RMW_IMPLEMENTATION_VALUE`
- `ROS2_TOPIC_VALUE`
- `ROS_LOCALHOST_ONLY_VALUE`
- `ROS_AUTOMATIC_DISCOVERY_RANGE_VALUE`

## 后续 AI 修改规则

1. 改 ROS 脚本前，先跑官方示例：

```bash
bash scripts/test_ros2_official_examples.sh
```

2. 官方示例通过后，再跑项目自测：

```bash
bash scripts/test_ros2_e2e.sh
```

3. 不要直接依赖普通 shell 里的 ROS 环境，优先用干净 shell。

4. 不要把 `ROS_DOMAIN_ID` 设成大于 `232` 的值。

5. 在多人共网环境里，不要只看 `subs_seen`，要同时看 subscriber 实际日志。

6. 如果只是本机自测，优先：

- `ROS_LOCALHOST_ONLY=1`
- 独占 topic

7. 如果是跨机联调，再关闭 `ROS_LOCALHOST_ONLY`，改用双方一致的：

- `ROS_DOMAIN_ID`
- `RMW_IMPLEMENTATION`
- 发现策略

## 推荐排障顺序

1. 先确认 WSL 有网络
2. 再跑官方 ROS 示例
3. 再跑项目 e2e
4. 最后才去改 Fast DDS XML、静态 peer 或跨机发现策略

## 当前相关文件

- `scripts/ros2_publisher.py`
- `scripts/ros2_subscriber.py`
- `scripts/test_ros2_official_examples.sh`
- `scripts/test_ros2_e2e.sh`
- `README.md`
