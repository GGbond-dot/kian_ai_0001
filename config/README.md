# Config Directory

`config/config.json` 是本地运行时配置，默认不进入 git，因为里面通常包含：

- LLM / VLM API key
- MQTT / WebSocket token
- 设备标识
- 本地调试状态文件

如果要在另一台机器或给下一个 AI 复现项目：

1. 复制 `config/config.example.json` 为 `config/config.json`
2. 补齐真实密钥、设备 ID 和协议配置
3. 如需完整迁移本机状态，再额外复制这些文件：
   - `config/efuse.json`
   - `config/task_queue.jsonl`
   - `config/task_status.jsonl`

如果你需要的是“完整离线备份”，不要只依赖 git。请同时运行：

```bash
bash scripts/backup_local_state.sh
```

它会把 `config/`、`models/`、`cache/`、`logs/` 和环境快照打成一个本地归档文件。

## 多无人机配置（DRONES / MULTI_DRONE）

`DRONES` 是无人机花名册，每架一条：

| 字段 | 说明 |
|---|---|
| `key` | 系统内部 / MCP 工具标识，如 `a0`(一号机)、`b1`(二号机) |
| `label` | UI 和语音播报用名称 |
| `namespace` / `drone_id` | 生成 odom / 点云 / path topic（如 `/b/drone_1_Odometry_world`） |
| `command_topic` | 起飞/降落/悬停的 UInt8 指令 topic（如 `/b/drone_command`） |
| `goal_topic` | 发布 GoalWithType 的 topic（每架一个，如 `/b/goal_with_type`） |
| `enabled` | 是否启用 |

`MULTI_DRONE`：`safety_radius`(多机防撞安全半径，米) / `reservation_ttl_sec`(路径预约兜底过期秒) /
`default_drone_key`(未点名时的默认机)。

**单机兼容**：不写 `DRONES`（或留空）时，从旧 `GLOBAL_PLANNER.namespace/drone_id`
自动生成单机配置，`command_topic` 回退 `/drone_command`，老流程不受影响。
规划器共享参数（pcd_path/resolution 等）写在 `GLOBAL_PLANNER`，各机继承、可被 `DRONES` 条目覆盖。

实现细节见 `project_markdown/MULTI_DRONE_IMPLEMENTATION.md`。
