# aiagent

一个基于 Python 的桌面/Web AI 客户端，支持：

- GUI / CLI / Web 三种运行方式
- **本地 Agent 协议（默认）** ：直接接你自己的 LLM API，不依赖任何云端服务
- MCP 工具调用
- ROS2 任务发布与联调
- SLAM Web Viewer（`/slam` 页面，three.js 实时可视化 FAST-LIO 输出）

历史上这个仓库 fork 自 [py-xiaozhi](https://github.com/huangjunsen0406/py-xiaozhi)，曾用过小智云端协议（websocket / mqtt）。**当前默认已切换到本地 Agent**，小智相关代码保留但不再使用。如果你想用小智，看本文档底部的"遗留：小智协议"。

这个 README 的目标不是覆盖所有细节，而是让第一次接手这个仓库的人能快速看懂：

1. 这个项目是干什么的
2. 该从哪里启动
3. 核心代码在哪
4. 哪些文件会进 git，哪些不会

更细的专项说明见：

- [config/README.md](../config/README.md)
- [ROS2_DEBUG_NOTES.md](ROS2_DEBUG_NOTES.md)
- [SLAM_WEB_VIEWER_DESIGN.md](SLAM_WEB_VIEWER_DESIGN.md)

## 部署前先看

如果你是把整个项目交给别人部署，这几个文件必须一起看：

- `README.md`
- `config/README.md`
- `config/config.example.json`

## 快速开始

### 1. 安装系统依赖

Linux（Debian / Ubuntu）至少先装：

```bash
sudo apt-get update
sudo apt-get install -y portaudio19-dev libportaudio2 ffmpeg libopus0 libopus-dev \
                        build-essential python3-venv python3-pip libasound2-dev \
                        libxcb-xinerama0 libxkbcommon-x11-0
```

如果只是先在 Windows/macOS 上复现，可以先跳过这一步，但 Linux 不建议跳。

### 2. 创建环境并安装 Python 依赖

最常见的安装方式：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
pip install -r requirements_local_agent.txt
```

如果你是 macOS，优先改用：

```bash
pip install -r requirements_mac.txt
pip install -r requirements_local_agent.txt
```

### 3. 安装失败时的替代路线

最常见的卡点是 `PyQt5`。

如果 `pip install -r requirements.txt` 卡在 `PyQt5`：

1. 不要装 `apt` 的 `python3-pyqt5`
2. 改用 conda 安装 PyQt
3. 再安装不含 `PyQt5` 的其余依赖

示例：

```bash
conda create -n aiagent python=3.10 -y
conda activate aiagent
conda install -c conda-forge -y pyqt=5.15 libstdcxx-ng>=13 libgcc-ng>=13
pip install -r requirements_no_pyqt.txt
pip install -r requirements_local_agent.txt
```

仓库里已经补了这个文件：

- `requirements_no_pyqt.txt`

其他常见替代：

- 如果 `miniaudio` 安装失败，可改用 `pydub + ffmpeg`
- 如果你暂时不需要唤醒词，可优先参考 `requirements_no_sherpa.txt`

### 4. 准备配置

```bash
cp config/config.example.json config/config.json
```

至少补齐这些字段（本地 Agent 必填）：

- `LLM.api_key`
- `LLM.base_url`
- `LLM.model`
- `SYSTEM_OPTIONS.CLIENT_ID`
- `SYSTEM_OPTIONS.DEVICE_ID`

### 5. 运行

默认协议是 `local`，所以最简启动就够：

```bash
# Web（推荐，可远程访问、含 /slam 可视化）
python main.py --mode web

# GUI 桌面
python main.py --mode gui

# CLI 终端
python main.py --mode cli
```

显式写也等价：

```bash
python main.py --mode web --protocol local
python main.py --mode gui --protocol local
python main.py --mode cli --protocol local
```

Web 模式启动后：

- 控制页面：`http://<本机IP>:8080/`
- SLAM 可视化：`http://<本机IP>:8080/slam`

## 可复刻性说明

如果你的目标是"把仓库交给别人，让别人尽量无脑复刻"，当前仓库已经接近可用，但还要满足这几个前提：

1. 对方按 README 安装系统依赖
2. 对方知道 `PyQt5` 失败时要切换到 `requirements_no_pyqt.txt`
3. 对方按 `config/config.example.json` 补齐真实配置（至少 `LLM.*`）
4. 如果你依赖本地模型、唤醒词文件、缓存或运行状态，还要额外给 `backups/` 归档

## 项目结构

```text
aiagent/
├── main.py
├── config/
├── scripts/
├── src/
├── assets/
├── libs/
└── project_markdown/
    ├── README.md
    ├── ROS2_DEBUG_NOTES.md
    └── SLAM_WEB_VIEWER_DESIGN.md
```

核心目录说明：

- `main.py`
  程序入口，负责解析参数、初始化事件循环、启动 `Application`
- `src/application.py`
  应用生命周期、设备状态切换、协议与 UI 调度的主逻辑
- `src/protocols/`
  - `local_agent_protocol.py` ← **当前默认**
  - `websocket_protocol.py` / `mqtt_protocol.py` ← 小智遗留，保留不删
- `src/llm/`
  LLM 接入、工具调用循环、Responses API 兼容逻辑
- `src/mcp/tools/`
  MCP 工具实现，按领域拆分
- `src/plugins/`
  UI、快捷键、唤醒词、音频等插件层
- `src/display/`
  GUI / CLI / Web 显示层（Web 含 `/slam` 三维可视化、SlamBridge ROS 桥）
- `src/views/`
  设置窗口、激活窗口等界面组件（小智时代留下的，GUI 模式仍用）
- `scripts/`
  调试、诊断、ROS2、自检、备份脚本

## 运行模式

### 界面模式

- `--mode gui`
  桌面图形界面
- `--mode cli`
  终端模式
- `--mode web`
  Web 模式（FastAPI + WebSocket，可远程访问，含 SLAM 可视化）

### 协议模式

- `--protocol local`（**默认**）
  本地 STT + LLM + TTS 闭环，直接走你自己的 LLM API
- `--protocol websocket` / `--protocol mqtt`
  小智云端协议，遗留保留，**默认不走**

## 常用脚本

| 脚本 | 用途 |
| --- | --- |
| `scripts/backup_local_state.sh` | 归档本地运行状态 |
| `scripts/ros2_publisher.py` | 向 `/robot_task` 发布任务 |
| `scripts/ros2_subscriber.py` | 订阅 `/robot_task` 观察消息 |
| `scripts/test_ros2_e2e.sh` | 用项目自己的发布/订阅脚本做 ROS2 自测 |
| `scripts/test_ros2_official_examples.sh` | 用 ROS2 官方示例验证底层 DDS 是否正常 |
| `scripts/mock_runner.py` | 写入 mock 状态记录，用于联调 |
| `scripts/camera_scanner.py` | 扫描本机摄像头 |
| `scripts/py_audio_scanner.py` | 扫描本机音频设备 |
| `scripts/music_cache_scanner.py` | 扫描音乐缓存 |
| `scripts/keyword_generator.py` | 生成关键词/唤醒词相关辅助内容 |

## MCP 工具概览

当前工具主要放在 `src/mcp/tools/`，按领域分组：

- `system`
- `calendar`
- `timer`
- `music`
- `camera`
- `screenshot`
- `web`
- `robot_dispatch`
- `bazi`

如果你要查某个工具的真实行为，先看：

```text
src/mcp/tools/<domain>/
```

不要先从 README 猜。

## ROS2 相关

这个仓库已经包含一套最小的 ROS2 联调脚本。

常用入口：

```bash
bash scripts/test_ros2_e2e.sh
```

如果要看更详细的联调过程、环境变量约定、常见坑，直接看：

- [ROS2_DEBUG_NOTES.md](ROS2_DEBUG_NOTES.md)

当前约定：

- 默认 topic：`/robot_task`
- 推荐 ROS 2 Humble
- `ROS_DOMAIN_ID` 必须小于 `233`

## SLAM Web Viewer

`/slam` 页面用 three.js 渲染 FAST-LIO 输出的累积地图、实时 scan、轨迹和位姿。
设计、topic 列表、参数说明见 [SLAM_WEB_VIEWER_DESIGN.md](SLAM_WEB_VIEWER_DESIGN.md)。

启动方式（DK2500 上的 aiagent 终端）：

```bash
export ROS_DOMAIN_ID=10
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
source .venv/bin/activate
python3 main.py --mode web
```

然后浏览器开 `http://<DK2500_IP>:8080/slam`。

## 哪些内容不会进 git

这个仓库默认只跟踪源码、脚本、模板配置和文档。

下面这些是本地运行状态，不建议提交：

- `config/config.json`
- `config/*.jsonl`
- `models/`
- `cache/`
- `logs/`
- `.venv/`
- `backups/`

原因很简单：

- 里面通常包含 API key、设备标识、缓存和临时状态
- 这些内容会让仓库显得脏，而且不利于复现

## 如何做完整备份

如果你只是备份代码：

```bash
git add .
git commit -m "your message"
git push origin main
```

如果你还想保留本机运行状态，再额外执行：

```bash
bash scripts/backup_local_state.sh
```

这个脚本会把 `config/`、`models/`、`cache/`、`logs/` 等本地状态打包到 `backups/`。

## 给下一个维护者的建议

- 先看 `main.py`、`src/application.py`、`src/protocols/local_agent_protocol.py`、`src/mcp/tools/`
- 改 UI 时优先看 `src/display/` 和 `src/views/`
- 改本地 Agent 时优先看 `src/llm/`、`src/protocols/local_agent_protocol.py`
- 改 ROS2 时优先看 `scripts/ros2_*.py` 和 `src/mcp/tools/robot_dispatch/`
- 改 SLAM Web Viewer 时优先看 `src/display/slam_bridge.py`、`src/display/web_static/slam.js`、`src/display/slam_constants.py`
- 新增本地状态目录时，记得同步更新 `.gitignore` 和 `scripts/backup_local_state.sh`

## 当前整理原则

这个仓库现在按下面的思路维护：

- 根目录尽量只保留入口、依赖、文档和少量通用脚本
- 运行态文件不进 git
- 备份文件不进 git
- 业务逻辑优先按 `src/` 目录归类，不在根目录堆脚本

## 遗留：小智协议

历史上这个仓库连过小智云端（`websocket` / `mqtt` + OTA + 设备激活）。这部分代码现在**保留但默认不启用**，相关文件：

- `src/protocols/websocket_protocol.py`、`src/protocols/mqtt_protocol.py`
- `src/core/ota.py`、`src/core/system_initializer.py`
- `src/utils/device_activator.py`

如果你确实要回到小智模式：

```bash
python main.py --mode gui --protocol websocket
```

需要在 `config/config.json` 里补齐 `WEBSOCKET_ACCESS_TOKEN` 或 `MQTT_INFO.*`，并保证 OTA 服务器认你的设备 SN/MAC。否则会看到 `OTA服务器错误: HTTP 400` 然后协议连接失败。

不建议。本地 Agent 已经够用，自己掌控。
