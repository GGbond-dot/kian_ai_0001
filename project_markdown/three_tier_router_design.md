# 三层路由 v2 — Tier 0 执行主力 + Tier 2 兜底执行

> **日期**: 2026-06-12
> **状态**: 已实现（本文随代码同步落地）
> **取代**: v1 的"Tier 2 不当工具兜底"约定（2026-05-02）。v2 经讨论明确：Tier 2 **就是**模糊指令的兜底执行层，配套修了假执行和确认断链问题。
> **关联**: [`slam_grasp_region_design.md`](slam_grasp_region_design.md)（抓取链路）、`SLAM_WEB_VIEWER_DESIGN.md`

---

## 一、设计原则

> 明确口令走 Tier 0 秒响应；模糊指令 Tier 2 理解后**真执行**（清晰直接调，模糊先确认）；
> 主动播报走事件通道，不进路由。

| 层 | 职责 | 延时 |
|---|---|---|
| Tier 0 | 主力执行：固定口令 → 直调 MCP 工具，0 LLM | ~百毫秒 |
| Tier 1 | 小递闲聊（mimo flash，无工具），越界说"我做不了"踢 Tier 2 | ~1s |
| Tier 2 | 完整工具 + ReAct（≤10 轮）：模糊指令兜底执行、自由问答、异常诊断 | 数秒 |
| 粘性路由 | Tier 2 以问句收尾（等确认）→ 下一句直送 Tier 2 | — |
| 事件通道 | 扫到 QR / 放物完成 → `trigger_proactive_response` 主动播报 | 实时 |

## 二、路由顺序（`local_agent_protocol._run_pipeline`）

```
STT 文本
 ├─ 1. Tier 0：INTENT_KEYWORDS 命中 → 直调工具，return
 ├─ 2. 粘性路由：_tier2_sticky 置位 → 直送 Tier 2，return（明确口令仍优先，见 1）
 ├─ 3. Tier 2 直达：tier2_keywords 命中 → Tier 2，return
 ├─ 4. Tier 1：闲聊；前 30 字扫到 fallback 短语 → 落到 5
 └─ 5. Tier 2 兜底
```

## 三、Tier 0 词表（`INTENT_KEYWORDS`）

支持两种格式：裸 list（仅关键词）或 dict `{"keywords": [...], "ack": "...", "args": {...}}`。
`ack` 非空 = 命令型（先播 ack 再后台执行）；`ack` 空 = 查询型（等工具结果播报，**失败原因会被念出来**）。

| 工具 | 关键词 | 说明 |
|---|---|---|
| drone.takeoff / land / hover | 起飞/降落/悬停… | UInt8 → /drone_command |
| drone.status / mapping.view | 任务状态 / 看地图 | 查询型 |
| drone.dispatch_selected_goal | 去抓取 / 去抓 / 执行框选任务 / 下发框选目标 | args 透传 goal_type=1 |
| vision.dispatch_place | 确认放置 / 放下去 / 确认下发 / 执行放置 | 用扫码预存的放物点 |

## 四、Tier 2 兜底执行的三件配套（v2 核心）

1. **禁假执行**（修 5 月发现的"嘴上说已执行实际没调工具"）：`LLM.system_prompt` 写死
   "必须真实调用工具，严禁未调用就声称已执行；失败如实播报"。
2. **模糊先确认**：意图不清晰时 LLM 先复述理解、以问号结尾等操作员确认，确认后再调工具。
3. **粘性路由**（修确认断链）：没有它，"是的/确认"这种回答不命中任何关键词会掉进 Tier 1
   （无工具，流程死掉）。实现：`_run_tier2_full` 末尾检测回复以 `？/?` 收尾 → 置
   `_tier2_sticky`；下一句在 Tier 0 之后、tier2-direct 之前消费该 flag 直送 Tier 2，用完即清。

`tier2_keywords`（执行词 + 查询词）：抓取/去拿/去这里/放置/放货/送货/下发/框选/检测/识别/规划/为什么/怎么回事/日志。

## 五、goal_selection_store 来源校验（防覆盖误发）

store 单槽存最新目标点，有两个写入方，条目带 `source` 标记：

| source | 写入方 | 合法读取方 |
|---|---|---|
| `slam_web` | Web 框选（web_server 校验时打标） | `dispatch_selected_goal` |
| `vision_qr` | 扫码 `_pre_plan_place`（goods_location.yaml 查表坐标） | `vision.dispatch_place` |

读取方校验来源，不匹配直接 RuntimeError（语音播报原因），杜绝"框选点被扫码点覆盖后抓空气 /
货放到框选位置"两类事故。

## 六、视觉送物链路备忘

- 二维码只编码 ID（如 `MED-001`），放置坐标提前登记在 `config/goods_location.yaml`
  （**模板已建，坐标占位待实测**），扫码 = 查表，不是从码里读地址。
- 检测循环（YOLO+QR+标注推流）独立于路由实时跑；扫到有效 QR 自动预存放物点 + 主动播报，
  无 LLM 延时。Tier 2 只出现在人开口问/确认的环节。

## 七、Tier 2 深度任务（展示"有深度的 AI"，非实时）

三层路由的初衷是响应快，但快的层调不了 MCP。Tier 2 的展示价值在**非实时综合推理**任务上，
全部基于现成只读工具，LLM 多轮串调 + 归纳，零执行风险：

| 场景 | 触发话术 | LLM 行为 |
|---|---|---|
| 任务复盘汇报 | "汇报一下这次任务" | 串调 drone.status(limit 可调) + planner_status + get_detection，综合成口头总结（指令/货物/路径/异常） |
| 起飞前检查 | "做一下起飞前检查" | planner_status 核查规划器/地图/odom + get_detection 验视觉链路，逐项播报，未就绪明确指出 |
| 失败诊断 | "为什么失败了" | 调状态工具分析原因，一两句解释 + 补救建议（演示可故意不框选触发失败） |

实现：`tier2_keywords` 加 汇报/复盘/总结/检查；`LLM.system_prompt` 写明三个场景的工具链；
`drone.status` 加 `limit` 参数（默认 10，复盘可取 100）。

> 多步任务编排（"先抓 A 再送 B 再返航"）有展示价值但执行风险高，**待后续专门讨论**，本期不做。

## 八、待办

- [ ] 板上 `config.json` 同步本文所有配置项（INTENT_KEYWORDS 新词条、tier2_keywords、
      LLM / LLM_FAST system_prompt）
- [ ] `goods_location.yaml` 填实测放置区坐标，按 key 生成二维码
- [ ] 实机验证粘性路由（Tier 2 问句 → "是的" → 工具真调）
- [ ] 实机验证三个深度任务的话术与工具链（汇报/检查/诊断）
- [ ] 讨论多步任务编排方案（确认链、抢断策略、失败回滚）
