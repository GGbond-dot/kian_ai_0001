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
