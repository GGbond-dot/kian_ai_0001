# SLAM 禁飞区前端绘制 P1 实施记录

> **日期**: 2026-05-20
> **范围**: P1 已实现前端绘制与本地持久化;P2 已搭后端 API + ROS bridge 占位框架。飞控消息契约未确认前不实际 ROS publish。
> **前置**: `slam_base_map_and_nfz_design.md` 中 P0 H1-H10 已通过验收。

---

## P1 已落地

- `/slam` 顶栏增加 `禁飞区` 按钮。
- 点击按钮后进入禁飞区面板和俯视浏览模式:
  - 相机强制切到俯视
  - OrbitControls 禁用旋转,单指/左键改为平移,双指保留缩放+平移
  - 可先平移/缩放地图到目标区域
  - 点 `开始框选` 后,下一次 pointerdown / move / up 拖拽才生成矩形
  - 框选完成后自动回到俯视浏览模式
- 选区以半透明红色矩形 + 红色边框显示。
- 右侧面板列出禁飞区:
  - 名称可编辑
  - 可删除单个禁飞区
  - 可清空全部禁飞区
  - `zMin` / `zMax` 高度滑条可编辑
- 数据持久化在浏览器 `localStorage`:
  - key: `aiagent.slam.noFlyZones.v1`

---

## 数据结构

```json
{
  "id": "timestamp-random",
  "name": "禁飞区 1",
  "minX": 0.0,
  "maxX": 1.0,
  "minY": 0.0,
  "maxY": 1.0,
  "zMin": 0.0,
  "zMax": 3.0
}
```

---

## P2 框架已落地

- 前端面板增加 `下发` 按钮。
- `POST /api/noflyzone` 接收当前 localStorage 禁飞区。
- `GET /api/noflyzone` 返回后端最近一次收到的禁飞区。
- 后端校验并归一化 `minX/maxX/minY/maxY/zMin/zMax`。
- `src/ros/nofly_zone_bridge.py` 已作为 ROS 下发边界占位。
- 当前不会实际 publish 到 `/a/no_fly_zones`,等待飞控确认消息类型和字段。

## 明确未做

- 不做实际 ROS publisher 创建与消息发布
- 不定义 `/a/no_fly_zones` 消息格式
- 不做飞机端违规检测
- 不做 UI 高亮违规区
- 不做多飞机分层

这些属于 `slam_base_map_and_nfz_design.md` 的 §I.P2 / §I.P3。
