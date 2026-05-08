# aiagent

`aiagent` 是一个本地语音 Agent 项目，当前主要面向“平板/桌面端语音交互 + ROS2 无人机控制 + SLAM 地图展示”的联调场景。主程序用 Python 编写，默认走 `local` 协议，在本机完成语音输入、STT、意图路由、LLM 工具调用、TTS 播报和 Web/平板端状态同步。

当前代码已经不再依赖历史云端协议链路。仓库里还能看到 `mqtt_protocol.py`、`websocket_protocol.py`、`ota.py` 等历史文件，但主入口默认使用 `src/protocols/local_agent_protocol.py`，日常运行和 README 说明都以本地 Agent 链路为准。

## 功能概览

- 本地语音闭环：麦克风或平板 WebView 输入 PCM/Opus 音频，经过 STT 转写后进入 Agent 流水线，再通过 TTS 播报或推送到平板播放。
- 三层路由：Tier 0 关键词直达工具、Tier 1 快速闲聊模型、Tier 2 完整 LLM + MCP 工具兜底。
- 无人机控制：`drone.takeoff`、`drone.land`、`drone.hover` 等工具会向 ROS2 `/drone_command` 发布 `std_msgs/UInt8` 指令。
- 常驻 ROS2 bridge：优先复用 `DroneCommandBridge` 常驻 publisher，避免每条指令都冷启动 subprocess；不可用时回退脚本发布。
- Web 控制台：FastAPI 提供 `/` 控制页面、`/slam` 地图页面，以及控制、音频输入、音频输出和 SLAM 的 WebSocket 通道。
- 平板配套端：`android_webview/` 是一个横屏沉浸式 WebView 客户端，加载后端 Web 控制台，并通过 JS bridge 支持音频交互。
- SLAM 可视化：`src/display/slam_bridge.py` 与前端 `slam.html/slam.js` 用于实时展示建图、scan、轨迹和位姿。
- MCP 工具：默认暴露系统音量和无人机调度工具；日历、计时器、音乐、Web 等工具源码保留，可通过配置按需启用。
- 面试材料：仓库包含 ROS2 bridge 相关的八股文档和实战复盘，方便讲项目和准备追问。

## 快速开始

### 1. 准备 Python 环境

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
pip install -r requirements_local_agent.txt
```

macOS 可优先使用：

```bash
pip install -r requirements_mac.txt
pip install -r requirements_local_agent.txt
```

如果 `PyQt5` 安装失败，可以改走不含 PyQt5 的依赖文件，并用 conda 安装 Qt 相关包：

```bash
conda create -n aiagent python=3.10 -y
conda activate aiagent
conda install -c conda-forge -y pyqt=5.15
pip install -r requirements_no_pyqt.txt
pip install -r requirements_local_agent.txt
```

### 2. 准备配置

运行配置在 `config/` 下。`config/config.json` 通常包含 LLM/STT/TTS 密钥、模型、设备 ID、路由开关和意图关键词，迁移或部署前请先阅读 [config/README.md](config/README.md)。

本地 Agent 至少需要确认这些配置项可用：

- `LLM.*`：Tier 2 完整工具调用模型。
- `LLM_FAST.*`：Tier 1 快速闲聊模型。
- `STT.*`：语音识别 provider、流式识别和 VAD 参数。
- `TTS.*`：TTS provider、平板直连 TTS 和语音参数。
- `ROUTER.fast_path_enabled`：是否启用 Tier 0/Tier 1 快路径。
- `INTENT_KEYWORDS`：起飞、降落、悬停、看地图等关键词到工具的直达映射。
- `LLM.optional_tool_groups`：按需启用 `calendar`、`timer`、`music`、`web` 等可选工具组。

### 3. 启动项目

默认协议就是 `local`，一般直接指定运行模式即可：

```bash
# Web 模式，推荐用于远程访问和 SLAM 可视化
python main.py --mode web

# 桌面 GUI
python main.py --mode gui

# 命令行
python main.py --mode cli
```

Web 模式启动后常用入口：

- 控制页面：`http://<本机IP>:8080/`
- SLAM 页面：`http://<本机IP>:8080/slam`
- TTS PoC：`http://<本机IP>:8080/tts_poc`

## 当前语音链路

```text
平板/麦克风音频
  -> STT(qwen/whisper, 可选流式)
  -> Tier 0 关键词命中：直接调用 MCP 工具，跳过 LLM
  -> Tier 1 快速模型：短闲聊流式回复，按句推 TTS
  -> Tier 2 完整 Agent：LLM + OpenAI function calling + MCP 工具
  -> TTS(edge/qwen, 可推平板或本地播放)
```

Tier 0 用于高确定性的控制命令，例如“起飞”“降落”“悬停”“看地图”。这类命令不需要 LLM 推理，命中后直接执行工具并播报 ack，能显著降低首响应延迟。

Tier 2 用于复杂语义、工具调用和兜底问答。Agent 使用 OpenAI-compatible 接口，支持普通 Chat Completions，也兼容部分 Responses API 路径。

## ROS2 与无人机控制

默认无人机指令 topic：

```text
/drone_command
```

消息类型：

```text
std_msgs/UInt8
```

当前指令码约定：

| 指令 | 工具 | UInt8 |
| --- | --- | --- |
| 起飞 | `drone.takeoff` | `1` |
| 降落/停止/返航 | `drone.land` | `2` |
| 悬停 | `drone.hover` | `3` |

发布路径：

- 主路径：`src/ros/drone_command_bridge.py` 常驻 ROS2 publisher。
- 回退路径：`scripts/ros2_int32_publisher.py` subprocess 发布。

## 代码结构

```text
aiagent/
├── main.py                         # 入口：解析 mode/protocol，设置 ROS2 环境变量，启动 Application
├── src/                            # Python 主体代码
│   ├── application.py              # 应用生命周期、插件注册、协议连接和状态管理
│   ├── protocols/                  # 当前主链路 local_agent_protocol 与意图匹配
│   ├── llm/                        # LLM 客户端、Agent 循环、记忆存储
│   ├── stt/                        # Whisper、Qwen 等语音识别实现
│   ├── tts/                        # Edge TTS、Qwen TTS 等语音合成实现
│   ├── audio_processing/           # 唤醒词、音频处理
│   ├── plugins/                    # 音频、UI、MCP、IoT、快捷键等插件
│   ├── mcp/                        # MCP Server 和工具集合
│   ├── display/                    # CLI、GUI、Web 展示层和 SLAM Web Viewer
│   ├── views/                      # PyQt/QML 设置窗口、激活窗口等界面组件
│   ├── ros/                        # DroneCommandBridge 等 ROS2 bridge
│   └── utils/                      # 配置、日志、资源定位、设备标识等通用工具
├── scripts/                        # 调试、备份、ROS2、自检和辅助脚本
├── ros2_ws/                        # ROS2 action/interface/demo 工作区
├── android_webview/                # Android WebView 配套客户端
├── libs/                           # Opus、WebRTC APM 等预置二进制库
├── config/                         # 本地配置和运行状态说明
├── project_markdown/               # 架构、Web UI、SLAM、延迟等专项文档
├── tech_stack/                     # 技术复盘与面试八股文档
└── requirements*.txt               # 不同平台/能力组合的依赖清单
```

## 核心模块

- `main.py`：统一入口，支持 `--mode gui|cli|web`；日常默认使用 `--protocol local`。
- `src/application.py`：应用单例，负责初始化配置、协议、插件、设备状态和关闭流程。
- `src/protocols/local_agent_protocol.py`：当前主协议，实现 STT、三层路由、Agent/MCP 和 TTS。
- `src/protocols/intent_matcher.py`：Tier 0 关键词直达工具匹配。
- `src/llm/`：封装 LLM 调用、工具调用循环、流式输出和记忆能力。
- `src/plugins/`：用插件方式组织音频、UI、MCP、IoT、唤醒词、快捷键等横切能力。
- `src/mcp/tools/robot_dispatch/`：无人机起飞、降落、悬停、状态查询和看地图工具。
- `src/ros/drone_command_bridge.py`：常驻 ROS2 publisher，降低控制命令冷启动延迟。
- `src/display/`：Web/CLI/GUI 展示层，`web_static/` 下是浏览器端资源。
- `ros2_ws/` 与 `scripts/ros2_*.py`：ROS2 action、消息发布订阅和联调脚本。

## 常用脚本

| 脚本 | 用途 |
| --- | --- |
| `scripts/backup_local_state.sh` | 备份 `config/`、`models/`、`cache/`、`logs/` 等本地运行状态 |
| `scripts/test_ros2_e2e.sh` | 使用项目脚本做 ROS2 端到端自测 |
| `scripts/test_ros2_official_examples.sh` | 使用 ROS2 官方示例验证底层 DDS 环境 |
| `scripts/ros2_publisher.py` | 向 ROS2 topic 发布测试任务 |
| `scripts/ros2_subscriber.py` | 订阅 ROS2 topic 观察消息 |
| `scripts/ros2_int32_publisher.py` | UInt8/整型指令发布回退脚本 |
| `scripts/run_local_action_agent.sh` | 启动本地 action agent 联调入口 |
| `scripts/py_audio_scanner.py` | 扫描本机音频设备 |
| `scripts/camera_scanner.py` | 扫描本机摄像头 |

## MCP 工具说明

默认注册给 LLM 的工具比较克制，核心是：

- `self.audio_speaker.set_volume`
- `self.audio_speaker.get_volume`
- `drone.takeoff`
- `drone.land`
- `drone.hover`
- `drone.status`
- `mapping.view`

可选工具组源码仍在 `src/mcp/tools/` 下，包括：

- `calendar`
- `timer`
- `music`
- `web`
- `camera`
- `screenshot`
- `bazi`
- `system`

其中 `calendar`、`timer`、`music`、`web` 可以通过 `LLM.optional_tool_groups` 恢复注册；`camera`、`screenshot`、`bazi` 和桌面应用管理工具当前默认下线，需要时再到 `src/mcp/mcp_server.py` 和对应 manager 里恢复。

## 平板与 Web

后端 Web 服务由 `src/display/web_server.py` 提供：

- `/`：主控制台。
- `/slam`：SLAM 可视化页面。
- `/tts_poc`：平板直连 TTS 验证页。
- `/ws`：控制信令。
- `/ws/slam`：SLAM 二进制/实时数据通道。
- `/ws/audio_in`：平板麦克风 PCM 上行。
- `/ws/audio_out`：TTS 音频或文本下行。

Android 客户端在 [android_webview/](android_webview/)，默认加载：

```text
http://192.168.10.1:8080/
```

如需改服务端地址，编辑 `android_webview/app/src/main/java/com/kian/aiagent/MainActivity.kt` 里的 `TARGET_URL`，并同步检查网络安全配置。

## 配套文档

更细的背景和设计文档主要放在 `project_markdown/`：

- [project_markdown/README.md](project_markdown/README.md)：更完整的部署、运行和维护说明，部分内容可能包含历史背景。
- [project_markdown/WEB_UI_ARCHITECTURE_PLAN.md](project_markdown/WEB_UI_ARCHITECTURE_PLAN.md)：Web UI 架构规划。
- [project_markdown/SLAM_WEB_VIEWER_DESIGN.md](project_markdown/SLAM_WEB_VIEWER_DESIGN.md)：SLAM Web Viewer 设计。
- [project_markdown/FIRST_RESPONSE_LATENCY.md](project_markdown/FIRST_RESPONSE_LATENCY.md)：首响应延迟分析。
- [project_markdown/ROS2_NATIVE_HUMBLE_CHANGELOG.md](project_markdown/ROS2_NATIVE_HUMBLE_CHANGELOG.md)：ROS2 Humble 原生适配记录。
- [android_webview/README.md](android_webview/README.md)：Android WebView 构建和调试说明。
- [libs/webrtc_apm/README.md](libs/webrtc_apm/README.md)：WebRTC APM 动态库说明。

## 面试八股与复盘

仓库里有配套的技术复盘和八股文档，适合用来准备项目讲解、面试追问和 ROS2 bridge 相关问题：

- [tech_stack/subprocess_ros_2_bridge_面试八股文档.md](tech_stack/subprocess_ros_2_bridge_面试八股文档.md)
- [tech_stack/subprocess_ros_2_bridge_第一轮实战复盘.md](tech_stack/subprocess_ros_2_bridge_第一轮实战复盘.md)

如果要对外介绍这个项目，可以先读根 README 建立整体认知，再结合八股文档准备这些问题：

- 为什么 AI Agent 不直接控制任意 ROS2 topic？
- 为什么早期用 subprocess，后来引入常驻 bridge？
- PyQt、asyncio、rclpy 的事件循环冲突怎么隔离？
- Tier 0 关键词直达为什么比全走 LLM 更适合飞控命令？
- bridge 不可用、超时、重启和安全兜底怎么处理？

## Git 注意事项

源码、脚本、模板和文档适合提交到 GitHub；本地运行状态要谨慎处理。以下内容通常不应该作为公共仓库内容提交：

- `config/config.json` 中的 API key、模型服务地址、设备 ID、token。
- `config/*.jsonl` 中的任务记录和本地运行状态。
- `models/`、`cache/`、`logs/`、`.venv/`、`backups/`。

需要完整迁移本机运行状态时，优先使用：

```bash
bash scripts/backup_local_state.sh
```

公共仓库建议只提交源码、脚本、文档、模板配置和必要的本地动态库，不提交真实密钥和运行日志。
