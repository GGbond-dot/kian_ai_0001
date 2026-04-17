# 编码规划：新增「查看建图效果」语音指令工具

> 目标读者：Codex 编码代理
> 完成后由人工接入现有 Agent 流程验证。

## 一、功能目标

当操作员对终端机器人说「查看建图效果」「看建图」「看地图」「打开 rviz」等指令时，Agent 应调用一个新的 MCP 工具，在本机启动下列命令以查看 FAST-LIO MID360 建图结果：

```bash
rviz2 -d ./dcl_fast_lio_mid360.rviz
```

要求：
- 非阻塞（fire-and-forget），Agent 主循环不能卡住。
- 回复风格保持与现有一致的极简：调用成功后语音只说「已打开建图视图」这一句，不超过 8 个字，不要 emoji、不要 PID、不要任务 ID、不要额外描述。
- 重复触发时不应开出多个 rviz 窗口。

## 二、现有架构依赖（请先读完再动手）

- MCP 工具注册入口：`src/mcp/tools/robot_dispatch/manager.py`
- 工具实现：`src/mcp/tools/robot_dispatch/tools.py`
- LLM 系统提示词：`config/config.json` → `LLM.system_prompt`
- 已有工具样式参考：`drone_takeoff` / `drone_land`
  - 它们的套路是 `subprocess.Popen(..., start_new_session=True)` 后立即返回一句话
  - 会往 `config/task_queue.jsonl` 和 `config/task_status.jsonl` 追加一条记录
  - 新工具请复用这套套路与工具函数（`_append_jsonl`、`_append_status`），不要自己新写日志层

## 三、改动清单

### 改动 1：新增 MCP 工具函数 `mapping_view`

**文件**：`src/mcp/tools/robot_dispatch/tools.py`

在文件顶部常量区（`CMD_EMERGENCY_LAND` 下方、`PROJECT_ROOT` 附近）新增：

```python
RVIZ_BIN = os.environ.get("RVIZ_BIN", "rviz2")
RVIZ_CONFIG_PATH = Path(
    os.environ.get(
        "RVIZ_MAPPING_CONFIG",
        str(PROJECT_ROOT / "dcl_fast_lio_mid360.rviz"),
    )
).expanduser()
```

在文件末尾（`query_status` 之后）新增：

```python
def _rviz_already_running(config_path: Path) -> bool:
    """检查是否已经存在一个 rviz2 进程加载了同一个配置文件。"""
    try:
        result = subprocess.run(
            ["pgrep", "-af", "rviz2"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return False
    needle_abs = str(config_path)
    needle_name = config_path.name
    for line in result.stdout.splitlines():
        if needle_abs in line or needle_name in line:
            return True
    return False


async def mapping_view(args: dict) -> str:
    """启动 rviz2 查看 FAST-LIO MID360 建图效果（fire-and-forget）。"""
    task_id = f"mapview-{int(time.time() * 1000)}"
    _logger.info(f"[建图] 启动 rviz2 查看建图效果 config={RVIZ_CONFIG_PATH}")

    if not RVIZ_CONFIG_PATH.exists():
        detail = f"未找到 RViz 配置：{RVIZ_CONFIG_PATH}"
        _append_status(task_id, "error", detail)
        return "未找到建图配置文件。"

    if _rviz_already_running(RVIZ_CONFIG_PATH):
        _append_status(task_id, "already_running", str(RVIZ_CONFIG_PATH))
        return "建图视图已打开。"

    env = os.environ.copy()
    if not env.get("DISPLAY"):
        env["DISPLAY"] = ":0"

    cmd = [RVIZ_BIN, "-d", str(RVIZ_CONFIG_PATH)]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=env,
            cwd=str(PROJECT_ROOT),
        )
    except FileNotFoundError:
        _append_status(task_id, "error", f"未找到 {RVIZ_BIN}")
        return "未安装 rviz2。"
    except Exception as exc:
        _append_status(task_id, "error", f"启动失败：{exc}")
        return "建图视图启动失败。"

    _append_jsonl(QUEUE_FILE, {
        "task_id": task_id,
        "ts": time.time(),
        "command": "mapping_view",
        "pid": proc.pid,
        "status": "launched",
    })
    _append_status(task_id, "launched", f"pid={proc.pid}")
    return "已打开建图视图。"
```

约束：
- 回复字符串必须严格是这 4 种之一，不要改写、不要拼接动态字段：
  `"已打开建图视图。"` / `"建图视图已打开。"` / `"未找到建图配置文件。"` / `"未安装 rviz2。"` / `"建图视图启动失败。"`
- 不要 `await proc.wait()`，不要 `proc.communicate()`，必须 fire-and-forget。
- 不要捕获异常后静默 return 成功串。

### 改动 2：注册 MCP 工具

**文件**：`src/mcp/tools/robot_dispatch/manager.py`

1. 修改顶部 import：

```python
from .tools import drone_takeoff, drone_land, drone_status, query_status, mapping_view
```

2. 在 `init_tools` 方法的末尾（`drone.status` 注册之后）追加：

```python
add_tool((
    "mapping.view",
    (
        "查看建图效果工具。当操作员说「查看建图效果」「看建图」「看地图」"
        "「打开 rviz」「显示地图」「看一下地图」等指令时调用。"
        "本工具会启动 rviz2 并加载 dcl_fast_lio_mid360.rviz 配置，"
        "用于查看 FAST-LIO MID360 的实时建图结果。无参数。"
    ),
    PropertyList([]),
    mapping_view,
))
```

### 改动 3：更新 LLM 系统提示词

**文件**：`config/config.json` → `LLM.system_prompt`

现状字段（节选）：

```
"system_prompt": "你是多无人机协同物流系统的智能终端助手。职责：1) 接收操作员指令，控制无人机编队；2) 汇报无人机状态；3) 听到「开始起飞」「系统启动」「执行任务」等指令时调用起飞工具；4) 听到「降落」「返航」等指令时调用降落工具。【回复风格】极简，只说关键结果，不超过一句话。例如工具执行成功后只回「已下达起飞指令」，不要复述任务ID、目标编号、详细参数等。不要加表情、不要加标点修饰。"
```

要求改为（职责列表里追加第 5 条，回复风格段不变）：

```
"system_prompt": "你是多无人机协同物流系统的智能终端助手。职责：1) 接收操作员指令，控制无人机编队；2) 汇报无人机状态；3) 听到「开始起飞」「系统启动」「执行任务」等指令时调用起飞工具；4) 听到「降落」「返航」等指令时调用降落工具；5) 听到「查看建图效果」「看建图」「看地图」「打开rviz」等指令时调用 mapping.view 工具，调用成功后只回「已打开建图视图」。【回复风格】极简，只说关键结果，不超过一句话。例如工具执行成功后只回「已下达起飞指令」，不要复述任务ID、目标编号、详细参数等。不要加表情、不要加标点修饰。"
```

注意：
- `config.json` 里中文字符串里禁止使用半角双引号 `"`，必须用 `「」` 包裹词组，否则 JSON 解析会炸。
- 改完用 `python3 -m json.tool config/config.json >/dev/null` 自检一遍。

### 改动 4：不需要新建 rviz 配置文件

`dcl_fast_lio_mid360.rviz` 由用户自行放置在项目根目录；代码里默认读 `PROJECT_ROOT / "dcl_fast_lio_mid360.rviz"`，可用环境变量 `RVIZ_MAPPING_CONFIG` 覆盖路径，用 `RVIZ_BIN` 覆盖二进制（例如今后要走 docker-humble 容器内的 rviz2 时）。Codex 不需要生成这个文件。

## 四、验收标准（人工验证）

- [ ] 运行 `source .venv/bin/activate && python main.py --mode cli --protocol local`
- [ ] 对终端说「查看建图效果」，本机弹出一个 rviz2 窗口，标题栏/状态栏能看到 `dcl_fast_lio_mid360.rviz`
- [ ] 语音回复只有一句「已打开建图视图」，TTS 不超过 1 秒，不说 PID、不说路径
- [ ] 连说两次「查看建图效果」，不会开出第二个 rviz 窗口，第二次语音回复「建图视图已打开」
- [ ] 把配置文件临时重命名后再次触发，Agent 不崩溃，语音回复「未找到建图配置文件」
- [ ] `config/task_queue.jsonl` 新增一行 `"command": "mapping_view"`，`config/task_status.jsonl` 对应一行 `launched` 状态
- [ ] 原有 `drone.takeoff` / `drone.land` / `drone.status` 行为不受影响

## 五、注意事项 / 边界条件

1. **DISPLAY**：rviz2 是 GUI 程序，代码里已对空 DISPLAY 回退到 `:0`。如果今后把 Agent 做成 systemd service 启动，会没有 DISPLAY，需要 service 文件里显式 `Environment=DISPLAY=:0` 并 `xhost +SI:localuser:<user>`。本次不实现，只需在代码注释里保留这个回退即可。

2. **工作目录**：`subprocess.Popen` 传了 `cwd=PROJECT_ROOT`，这是因为 `rviz2 -d ./dcl_fast_lio_mid360.rviz` 里用的是相对路径 —— 不能依赖用户当前 shell 所在目录。

3. **ROS 2 版本兼容**：本机是 Ubuntu 24.04 + Jazzy，建图节点可能跑在 Humble 容器或飞机侧。如果 Jazzy 原生 rviz2 看不到 Humble 的话题，后续需要把 rviz2 挪进 `docker_humble_bridge.py` 容器里用 X11 forwarding 启动。**本次不要改动 `docker_humble_bridge.py`**，先把本机路径跑通即可。

4. **fire-and-forget 要点**：
   - `start_new_session=True` 必须加，让 rviz2 脱离 Agent 的进程组，Agent 退出时 rviz2 不会跟着死
   - `stdout/stderr=DEVNULL` 必须加，避免 Agent stdin/stdout 被 rviz2 的日志刷爆
   - 不能用 `subprocess.run`

5. **幂等检测 `_rviz_already_running`**：走 `pgrep -af rviz2`，匹配进程命令行里是否包含配置文件名/全路径。没有 pgrep 时（理论上不会发生）直接返回 False，按正常启动走，不阻塞主流程。

## 六、不要做的事（硬性边界）

- 不要新增单元测试、pytest、mock；项目里目前没测试体系。
- 不要重构 `drone_takeoff` / `drone_land` / `drone_status` 现有逻辑。
- 不要修改 `docker_humble_bridge.py` / `ros2_int32_publisher.py` / `scripts/` 下其它脚本。
- 不要动 TTS、VAD、LLM 主循环、`local_agent_protocol.py`。
- 不要给语音回复加 emoji、表情、markdown、PID、路径等任何修饰。
- 不要新建 README 或别的文档。
- 不要改 `config.json` 里除 `LLM.system_prompt` 之外的任何字段。
- 不要把 rviz2 启动改成同步等待 / 阻塞模式。

## 七、改动文件清单（给 Codex 快速对齐）

| 文件 | 改动性质 |
|---|---|
| `src/mcp/tools/robot_dispatch/tools.py` | 新增常量、新增 `_rviz_already_running`、新增 `mapping_view` |
| `src/mcp/tools/robot_dispatch/manager.py` | import 里追加 `mapping_view`、`init_tools` 里追加 `mapping.view` 注册 |
| `config/config.json` | 仅修改 `LLM.system_prompt` 字段 |

就这三个文件，其它不要碰。
