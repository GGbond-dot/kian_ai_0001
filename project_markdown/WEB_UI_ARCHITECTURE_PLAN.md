# Web UI 远程渲染架构规划

## 背景

当前项目在开发板上同时运行 AI 中控逻辑（LLM、音频处理、IoT）和 PyQt5/QML GUI 渲染，
导致性能瓶颈。方案是将 UI 渲染分离到平板设备的浏览器上，开发板只负责后端逻辑。

## 架构总览

```
┌──────────────────────────────────┐       WiFi (同一局域网)       ┌─────────────────────┐
│          开发板 (后端)            │                              │    平板 (前端)        │
│                                  │                              │                     │
│  main.py --mode web              │   HTTP    ┌──────────┐       │  浏览器访问           │
│     │                            │ ◄────────►│ FastAPI  │       │  http://<IP>:8080    │
│     ├── Application (不变)       │           │          │       │                     │
│     ├── AudioPlugin (不变)       │   WS      │ /ws      │       │  ┌───────────────┐  │
│     ├── IoTPlugin   (不变)       │ ◄────────►│ 实时推送  │◄─────►│  │  Web UI       │  │
│     ├── McpPlugin   (不变)       │           └──────────┘       │  │  (HTML/CSS/JS) │  │
│     ├── WakeWordPlugin (不变)    │                              │  └───────────────┘  │
│     ├── UIPlugin(mode="web") NEW │                              │                     │
│     │    └── WebDisplay     NEW  │                              │                     │
│     └── CalendarPlugin (不变)    │                              │                     │
│                                  │                              │                     │
│  麦克风/扬声器 直连开发板         │                              │  纯渲染，无音频处理   │
└──────────────────────────────────┘                              └─────────────────────┘
```

## 核心设计原则

1. **最小改动**：不动 Application、Plugin 体系、协议层，只新增一个 `WebDisplay` 实现
2. **统一接口**：`WebDisplay` 实现 `BaseDisplay` 抽象类，和 `GuiDisplay`/`CliDisplay` 平级
3. **音频不走网络**：麦克风和扬声器保持接在开发板上，平板只做显示和控制输入

## 文件变更清单

```
需要新增的文件:
  src/display/web_display.py          # WebDisplay 类，实现 BaseDisplay
  src/display/web_server.py           # FastAPI 服务器 + WebSocket 管理
  src/display/web_static/             # 静态前端资源目录
  src/display/web_static/index.html   # Web UI 主页面
  src/display/web_static/app.js       # 前端 WebSocket 客户端 + UI 逻辑
  src/display/web_static/style.css    # 样式（复刻 QML 的深色主题风格）

需要修改的文件:
  main.py                             # 新增 --mode web 选项，web 模式不初始化 Qt
  src/plugins/ui.py                   # _create_display() 新增 "web" 分支
```

## 各模块详细设计

### 1. WebDisplay (`src/display/web_display.py`)

实现 `BaseDisplay` 接口，内部持有一个 `WebServer` 实例。

```python
class WebDisplay(BaseDisplay):
    """Web 显示类 - 通过 WebSocket 将状态推送到浏览器"""

    def __init__(self, host="0.0.0.0", port=8080):
        super().__init__()
        self.server = WebServer(host, port)
        self._callbacks = {
            "button_press": None,
            "button_release": None,
            "auto": None,
            "abort": None,
            "send_text": None,
        }

    async def set_callbacks(self, **kwargs):
        # 存储回调，当浏览器发来控制指令时调用
        self._callbacks.update(...)

    async def update_status(self, status, connected):
        # 通过 WebSocket 广播给所有连接的浏览器
        await self.server.broadcast({"type": "status", "status": status, "connected": connected})

    async def update_text(self, text):
        await self.server.broadcast({"type": "text", "text": text})

    async def update_emotion(self, emotion_name):
        await self.server.broadcast({"type": "emotion", "emotion": emotion_name})

    async def update_button_status(self, text):
        await self.server.broadcast({"type": "button", "text": text})

    async def start(self):
        # 启动 FastAPI 服务器（不阻塞事件循环）
        await self.server.start()

    async def close(self):
        await self.server.stop()
```

### 2. WebServer (`src/display/web_server.py`)

基于 FastAPI + uvicorn，管理 WebSocket 连接。

```python
class WebServer:
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.app = FastAPI()
        self.connections: set[WebSocket] = set()
        self._server = None
        self._on_command = None  # 接收浏览器控制指令的回调

    async def start(self):
        # 挂载静态文件 (index.html, app.js, style.css)
        # 注册 WebSocket 路由 /ws
        # 用 uvicorn.Server + asyncio 启动（非阻塞）
        ...

    async def broadcast(self, data: dict):
        # JSON 序列化后发给所有活跃连接
        # 断开的连接自动清理
        ...

    async def _ws_handler(self, ws: WebSocket):
        # 接受连接
        # 发送当前完整状态快照（新连接立即看到当前界面）
        # 循环接收浏览器指令：
        #   {"action": "auto"}        -> 触发自动对话
        #   {"action": "abort"}       -> 中断
        #   {"action": "press"}       -> 手动按下
        #   {"action": "release"}     -> 手动释放
        #   {"action": "send_text", "text": "..."} -> 发送文本
        ...
```

### 3. 前端 Web UI (`src/display/web_static/`)

**index.html** - 单页应用，复刻 QML 界面的核心元素：
- 状态胶囊（STANDBY / VOICE INPUT / VOICE OUTPUT / OFFLINE）
- 表情/emoji 显示区
- 对话文本显示区
- 控制按钮（自动对话、中断、文本输入发送）

**app.js** - WebSocket 客户端：
```javascript
// 核心逻辑伪代码
const ws = new WebSocket(`ws://${location.host}/ws`);

ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    switch (msg.type) {
        case "status":    updateStatusUI(msg.status, msg.connected); break;
        case "text":      updateTextUI(msg.text); break;
        case "emotion":   updateEmotionUI(msg.emotion); break;
        case "button":    updateButtonUI(msg.text); break;
        case "snapshot":  // 完整状态快照，新连接时收到
                          applyFullState(msg); break;
    }
};

// 用户操作 -> 发送控制指令
function onAutoClick()  { ws.send(JSON.stringify({action: "auto"})); }
function onAbortClick() { ws.send(JSON.stringify({action: "abort"})); }
function onSendText(t)  { ws.send(JSON.stringify({action: "send_text", text: t})); }
```

**style.css** - 深色主题，与 QML 风格一致（`#040814` 背景色等）

### 4. UIPlugin 修改 (`src/plugins/ui.py`)

```python
def _create_display(self):
    if self.mode == "gui":
        from src.display.gui_display import GuiDisplay
        self._is_gui = True
        return GuiDisplay()
    elif self.mode == "web":                          # <-- 新增
        from src.display.web_display import WebDisplay
        self._is_gui = False
        return WebDisplay()
    else:
        from src.display.cli_display import CliDisplay
        self._is_gui = False
        return CliDisplay()
```

回调绑定方式和 CLI 模式一致（直接传协程函数），不需要 GUI 的 `_wrap_callback`。

### 5. main.py 修改

```python
parser.add_argument(
    "--mode",
    choices=["gui", "cli", "web"],    # <-- 新增 "web"
    default="gui",
)

# 入口逻辑调整：
if args.mode == "gui":
    # 现有 PyQt5 + qasync 逻辑不变
    ...
else:
    # CLI 和 Web 模式都用标准 asyncio 事件循环
    exit_code = asyncio.run(
        start_app(args.mode, args.protocol, args.skip_activation)
    )
```

Web 模式完全不需要 PyQt5、qasync、QApplication — 开发板甚至不用装这些依赖。

## WebSocket 消息协议

### 服务端 → 浏览器（状态推送）

| type       | 字段                          | 说明            |
|------------|-------------------------------|-----------------|
| `snapshot` | status, connected, text, emotion, button_text | 连接时的完整状态 |
| `status`   | status: str, connected: bool  | 设备状态变化     |
| `text`     | text: str                     | TTS/STT 文本    |
| `emotion`  | emotion: str                  | 表情名称         |
| `button`   | text: str                     | 按钮状态文本     |

### 浏览器 → 服务端（控制指令）

| action      | 额外字段     | 说明        |
|-------------|-------------|-------------|
| `auto`      | -           | 开启自动对话 |
| `abort`     | -           | 中断当前对话 |
| `press`     | -           | 手动模式按下 |
| `release`   | -           | 手动模式释放 |
| `send_text` | text: str   | 发送文本消息 |

## 延迟分析

```
浏览器操作 (点击/输入)
    │  ~0ms
    ▼
WebSocket 发送控制指令
    │  ~1-3ms (WiFi 局域网)
    ▼
开发板 WebServer 接收
    │  ~0ms
    ▼
回调 → Application 处理
    │  (AI 处理时间，与 UI 无关)
    ▼
状态变更 → WebSocket broadcast
    │  ~1-3ms (WiFi 局域网)
    ▼
浏览器渲染更新
    │  ~16ms (60fps 一帧)
    ▼
用户看到变化

UI 往返总延迟: ~20ms（人类感知阈值 ~100ms，完全无感）
```

瓶颈始终在 AI 推理和音频处理（几百 ms 到几秒），UI 通信延迟可忽略。

## 新增依赖

```
fastapi
uvicorn[standard]
```

添加到 `requirements_no_pyqt.txt`（无 PyQt 环境的依赖列表，正好适用于开发板 web 模式）。

## 实施步骤

### 第一步：延迟验证（快速原型）
- 写一个最简 FastAPI + WebSocket echo 服务
- 平板浏览器连上去测往返延迟
- 确认 WiFi 延迟可接受后继续

### 第二步：WebServer 骨架
- 实现 `web_server.py`：FastAPI 应用、WebSocket 管理、广播机制
- 实现静态文件挂载

### 第三步：WebDisplay 实现
- 实现 `web_display.py`：完整的 `BaseDisplay` 接口
- 状态快照机制（新连接同步当前状态）

### 第四步：前端 Web UI
- `index.html` + `app.js` + `style.css`
- 复刻 QML 界面核心元素
- WebSocket 连接管理（自动重连）

### 第五步：集成
- 修改 `ui.py` 和 `main.py`
- `--mode web` 端到端跑通

### 第六步：测试和优化
- 开发板 + 平板联调
- 表情资源通过 HTTP 静态文件服务提供
- 断线重连、多客户端支持
