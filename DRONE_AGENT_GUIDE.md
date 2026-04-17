# 无人机物流 AI Agent 工作文档

本文档记录将原"手机店售货员"开源 AI Agent 改造为"多无人机协同物流系统智能终端"的全部变更与使用方式。

## 一、系统架构

```
┌──────────────────────┐        ┌──────────────────────┐
│  本机 (Ubuntu 24.04) │        │ 无人机开发板         │
│  ROS2 Jazzy (原生)   │        │ (Ubuntu 22.04)       │
│                      │        │ ROS2 Humble          │
│  ┌────────────────┐  │        │                      │
│  │ AI Agent (CLI) │  │        │  ┌────────────────┐  │
│  │  + STT/TTS/LLM │  │        │  │ ROS2 订阅节点  │  │
│  └───────┬────────┘  │        │  │ /drone_command │  │
│          │ 调用 MCP  │        │  └────────────────┘  │
│  ┌───────▼────────┐  │  DDS   │          ▲           │
│  │ Docker Humble  │◄─┼────────┼──────────┘           │
│  │ Bridge 容器    │  │ 跨机   │                      │
│  │ (发布 topic)   │  │ 通信   │                      │
│  └────────────────┘  │        │                      │
└──────────────────────┘        └──────────────────────┘
```

**为什么要 Docker Humble Bridge**
本机 ROS2 Jazzy 与开发板 ROS2 Humble 跨版本 DDS 不可靠（消息类型哈希、QoS 默认值在两版本间有破坏性变动）。通过本机起一个 Humble 容器发消息，保证两端同版本通信。

---

## 二、本次改造清单

### 1. TTS 流式播放修复
**文件**: `src/protocols/local_agent_protocol.py`
- **问题**: 原实现先完整生成 MP3 → 整个转 PCM → 再播放，首字节延迟高
- **修复**: 改为 `edge-tts → ffmpeg → paplay` 三段流式管道，边生成边播放
- **依赖**: 系统需安装 `ffmpeg`（`sudo apt install ffmpeg`）

### 2. TTS 文本清理
**文件**: `src/protocols/local_agent_protocol.py` — `_clean_text_for_tts()`
- 移除 emoji、markdown 粗体/斜体/标题/链接/代码块，避免被朗读出来

### 3. 角色切换：手机店 → 无人机物流系统
**文件**: `config/config.json`
- `LLM.system_prompt` 改为"多无人机协同物流系统的智能终端助手"

### 4. MCP 工具替换
**文件**: `src/mcp/tools/robot_dispatch/manager.py` 与 `tools.py`

| 新工具名 | 功能 | 触发词 |
|---------|------|--------|
| `drone.takeoff` | 发送起飞指令 | 开始起飞、系统启动、执行任务、出发 |
| `drone.land` | 发送降落/紧急降落指令 | 降落、返航、紧急降落、停止 |
| `drone.status` | 查询编队状态 | 无人机状态、飞机在哪、电量 |
| `drone.query_task_status` | 查询任务日志 | 任务进度、执行结果 |

### 5. 删除硬编码手机店逻辑
**文件**: `src/protocols/local_agent_protocol.py`
- 删除 `_PHONE_MODEL_ALIASES`、`_detect_phone_order_intent`、`_maybe_handle_direct_phone_order` 等所有手机相关硬编码
- 意图识别完全交由 LLM + 工具描述判断

### 6. 默认走 Docker Humble Bridge
**文件**: `src/mcp/tools/robot_dispatch/tools.py`
- `ROS2_PUBLISH_MODE` 默认值改为 `docker_humble_bridge`
- 通过 `ros2_publisher.py` → `docker_humble_bridge.py` → 容器内 `rclpy` → DDS 发布

---

## 三、ROS2 消息格式

**Topic**: `/drone_command`
**Type**: `std_msgs/String`
**Payload**（JSON 编码于 `data` 字段）:

### 起飞
```json
{
  "task_id": "takeoff-1728900000000",
  "command": "takeoff",
  "drone_id": "all",
  "mission": "default",
  "timestamp": 1728900000.123
}
```

### 降落 / 紧急降落
```json
{
  "task_id": "land-1728900050000",
  "command": "land",          // 或 "emergency_land"
  "drone_id": "all",
  "timestamp": 1728900050.456
}
```

**字段说明**
- `drone_id`: `"all"` 广播所有无人机，或 `"drone_1"`、`"drone_2"` 指定单机
- `mission`: 任务描述（`"default"` 表示默认巡航）
- `task_id`: 系统自动生成，用于追踪

---

## 四、环境配置

### 本机依赖
```bash
# 系统包
sudo apt install ffmpeg pulseaudio-utils docker.io

# Python 虚拟环境
source .venv/bin/activate
pip install -r requirements.txt        # 如已装可跳过
```

### 关键环境变量（可选）
| 变量 | 默认值 | 作用 |
|------|--------|------|
| `ROS2_DRONE_COMMAND_TOPIC` | `/drone_command` | 发送指令的 topic |
| `ROS2_PUBLISH_MODE` | `docker_humble_bridge` | 发布模式（`auto` = 用本机 rclpy） |
| `ROS_DOMAIN_ID` | `10` | DDS 域 ID，两端必须一致 |
| `ROS2_HUMBLE_CONTAINER_NAME` | `ros2-humble-bridge` | 容器名 |
| `ROS2_HUMBLE_IMAGE` | `ros:humble-ros-base` | 镜像 |

---

## 五、完整启动流程

### 步骤 1 — 启动 Docker Humble 容器（首次或重启后）
```bash
cd ~/kian_project/aiagent
python3 scripts/docker_humble_bridge.py start
```
预期输出：`container=ros2-humble-bridge image=ros:humble-ros-base domain=10 ...`

验证容器：
```bash
docker ps --filter name=ros2-humble-bridge
```

### 步骤 2 — 无人机开发板（Ubuntu 22.04 + Humble）
在开发板终端设置同样的 ROS_DOMAIN_ID 并监听 topic：
```bash
export ROS_DOMAIN_ID=10
source /opt/ros/humble/setup.bash
ros2 topic echo /drone_command
```

### 步骤 3 — 本机通信链路冒烟测试（不跑 AI）
```bash
python3 scripts/docker_humble_bridge.py pub --topic /drone_command --text '{"command":"test"}'
```
无人机端 `ros2 topic echo` 窗口应立即打印消息。若成功则链路已通。

### 步骤 4 — 启动 AI Agent
```bash
source .venv/bin/activate
python main.py --mode cli --protocol local
```

### 步骤 5 — 与 AI 对话
**按住 Ctrl+J**（配置在 `config.json` → `SHORTCUTS.MANUAL_PRESS`）说话，例如：
- "开始起飞"
- "所有无人机起飞执行配送任务"
- "紧急降落所有无人机"
- "查询无人机状态"

AI 会语音回复执行结果，同时开发板能收到对应的 ROS2 消息。

---

## 六、故障排查

### 无人机端收不到消息
1. **容器是否运行**: `docker ps --filter name=ros2-humble-bridge`
2. **ROS_DOMAIN_ID 是否一致**: 本机容器默认 `10`，开发板必须同值
3. **网络连通**: 两机 `ping` 互通
4. **防火墙**: Ubuntu UFW 若启用，放行 UDP 7400-7500 端口段
5. **同网段**: DDS 默认用 UDP 组播发现，跨子网需手动配置 DDS peer list

### AI 没调用工具，只用文字回答
- 说得更明确："调用起飞工具让所有无人机起飞"
- 多次不命中时可以在 `config.json` → `system_prompt` 里强调"必须调用工具"

### TTS 没声音
- 确认 `ffmpeg` 已安装：`which ffmpeg`
- 确认 PulseAudio 可用：`pactl info`

### 语音识别不准
- 中文效果受 Whisper 模型大小影响，可以把 `config.json` → `STT.model` 从 `base` 改为 `small` 或 `medium`（更慢但更准）

---

## 七、关键文件索引

| 文件 | 作用 |
|------|------|
| `main.py` | CLI 入口 |
| `config/config.json` | 全局配置（system_prompt、LLM、STT、TTS、ROS） |
| `src/protocols/local_agent_protocol.py` | 本地 Agent 协议（STT→LLM→TTS 流水线） |
| `src/llm/agent.py` | LLM Tool-use 循环 |
| `src/mcp/tools/robot_dispatch/manager.py` | 无人机工具注册 |
| `src/mcp/tools/robot_dispatch/tools.py` | 无人机工具实现（发送 ROS2 消息） |
| `scripts/docker_humble_bridge.py` | Docker Humble 容器管理 |
| `scripts/ros2_publisher.py` | ROS2 发布器（支持本机/Docker 双模式） |
| `scripts/ros2_string_publisher.py` | 容器内执行的 String 发布节点 |

---

## 八、后续可扩展方向

- **双向通信**: 订阅无人机回传的状态 topic（如 `/drone_status`），让 `drone.status` 工具读取实时数据而不是本地日志
- **多机编队**: 扩展 `drone_id` 参数，区分不同无人机并发送针对性指令
- **任务编排**: 新增 `drone.set_waypoint`、`drone.formation_change` 等工具
- **视觉反馈**: 结合已有的 `take_photo` / `get_scene_status` 工具，让 AI 能看到无人机摄像头画面
