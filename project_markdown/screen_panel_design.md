# 开发板屏幕表情/状态面板设计 (`/screen`)

> 状态: 已按此实现 — 待真机压测与验证
> 补充需求: 屏幕物理安装是颠倒的,页面整体 `rotate(180deg)`(`#screen.flipped`),
> 调试时可加 `?flip=0` 关闭;触摸命中随 CSS transform 自动换算,无需处理坐标。
> 前提: `python3 main.py --mode web` 不变,平板继续连原页面;屏幕面板是同一 web 服务的新页面

## 1. 背景与目标

开发板新接了一块**触摸屏**。目标是在 `--mode web` 运行时,屏幕同时显示一个交互界面,
定位为**表情/状态面板**(机器人小递的"脸"),而不是平板那套完整控制台。

实现路线(已确认方案 A):板上用 chromium kiosk 全屏打开本机 web 服务的专属页面:

```
chromium-browser --kiosk http://localhost:8080/screen
```

后端架构零改动 —— `WebServer._connections` 本来就是 set,状态/文本/表情全部 broadcast,
屏幕页面只是 `/ws` 上多一个客户端。

**待验证风险**:板上跑 chromium + GIF 动画的 CPU/内存占用,需真机实测(见 §5),
结果决定动画方案是否降级。

## 2. 改动清单

| 文件 | 改动 |
|---|---|
| `src/display/web_static/screen.html` | 新增,屏幕面板页面 |
| `src/display/web_static/screen.js` | 新增,WS 连接 + 表情/状态渲染 + 触摸指令 |
| `src/display/web_static/screen.css` | 新增,全屏深色布局 |
| `src/display/web_server.py` | 新增 `GET /screen` 路由(返回 screen.html,照抄 `/slam` 的写法) |

后端 Python 仅加一个静态路由;`WebDisplay` / `UIPlugin` / 协议层全部不动。

## 3. 页面设计

### 3.1 布局(横屏为主,竖屏自适应)

```
┌──────────────────────────────────────┐
│  ● 已连接          [自动对话]  状态条   │
│                                      │
│            ┌──────────┐              │
│            │  表情GIF  │   大表情居中   │
│            └──────────┘              │
│                                      │
│        "当前说的话 / 识别到的文字"        │
│                                      │
│  [ 按住说话 ]   [ 打断 ]   [ 模式 ]    │
└──────────────────────────────────────┘
```

### 3.2 表情渲染

- 复用现有 `/emojis/{name}.gif`(assets/emojis 下 22 个 GIF,后端路由现成)。
- `/ws` 收到 `{type:"emotion", emotion:"happy"}` → `<img src="/emojis/happy.gif">`。
- 收不到或文件缺失 → 回退 `neutral.gif`。
- 切换表情只换 `img.src`,不做额外 JS 动画,渲染压力交给浏览器解码 GIF。

### 3.3 触摸交互(复用现有 `/ws` 指令,后端零改动)

| 控件 | 发送指令 | 现有处理位置 |
|---|---|---|
| 按住说话(touchstart/touchend) | `press` / `release` | `WebDisplay._handle_command` |
| 打断 | `abort` | 同上 |
| 自动对话 | `auto` | 同上 |
| 模式切换 | `mode` | 同上 |

按住说话的 touch 事件处理直接参考 `app.js` 里平板的实现(含 touchcancel 兜底)。

### 3.4 音频边界(重要)

- 屏幕页面**不连** `/ws/audio_out` —— 否则 TTS 会与本机/平板重复出声。
- **不连** `/ws/audio_in` —— 拾音继续走本机麦克风(或平板),屏幕只发 press/release 信令。
- 不注册 Service Worker(sw.js 是给平板 PWA 的),避免缓存干扰。

## 4. 板上启动方式(随主程序同步拉起)

不需要单独命令。`src/display/screen_kiosk.py` 在 `WebDisplay.start()` 时自动:
等 8080 端口就绪 → 带 `DISPLAY=:0` 拉起 kiosk 浏览器打开 `/screen` →
主程序退出时一并关闭浏览器。SSH 启动 `main.py --mode web` 即可,板上无需键盘。

配置(config.json 顶层,默认关闭):

```json
"SCREEN_PANEL": {
  "ENABLED": true,
  "BROWSER": "chromium-browser",
  "URL": "",          // 留空 = http://localhost:{port}/screen
  "DISPLAY": ":0",    // SSH 会话没有 DISPLAY,必须显式指定
  "EXTRA_ARGS": []
}
```

实现要点:浏览器用独立 `--user-data-dir=/tmp/aiagent_screen_kiosk`(避免并入已有
实例导致 --kiosk 失效);异常退出只留日志不自动重启(防崩溃循环);
chromium 不在时改 `BROWSER`(firefox/cog,见 §5 降级)。

## 5. 真机压测方案(用户在板上执行)

### 5.1 测什么

1. **基线**:只跑 `main.py --mode web`,记录 CPU/内存。
2. **加 kiosk**:打开 `/screen`(可先用现有 `/` 页面近似),静置 + 表情连续切换两种状态。
3. 重点看:chromium 总 CPU%、内存 RSS、以及 **aiagent 进程是否被挤压**(TTS/STT 卡顿)。

### 5.2 怎么测

```bash
# 持续观察,按进程聚合
top -d 2 -o %CPU
# 或一次性快照
ps -eo pid,comm,%cpu,%mem,rss --sort=-%cpu | head -20
# 温度(若是树莓派类)
cat /sys/class/thermal/thermal_zone0/temp
```

GIF 动画压力专项:在 `/screen` 页面做一个隐藏的调试参数 `?stress=1`,
每 2s 自动轮换一个表情 GIF,跑 5 分钟看 CPU 是否持续高位。

### 5.3 判定与降级阶梯

| 结果 | 动作 |
|---|---|
| chromium 稳态 CPU < ~25%,语音链路无感知劣化 | 方案 A 定稿,GIF 照用 |
| GIF 解码占用偏高 | 降级 1:表情改静态 PNG(GIF 抽首帧),只在切换时换图 |
| chromium 本身太重 | 降级 2:换 `cog`/`epiphany` 等轻量 Wayland 浏览器 |
| 浏览器路线整体不行 | 降级 3:再议 PyQt 轻量窗口直连 `/ws`(方案 B 变体,改动大,最后手段) |

## 6. 验证清单(板上)

- [ ] 平板与屏幕同时在线,状态/文本/表情两端同步更新
- [ ] 屏幕按住说话可正常触发录音、松开结束
- [ ] 屏幕打断按钮在 TTS 播放中生效
- [ ] TTS 不重复出声(屏幕端静音)
- [ ] §5 压测数据合格
- [ ] 断网/服务重启后页面自动重连(复用 app.js 的重连逻辑)
