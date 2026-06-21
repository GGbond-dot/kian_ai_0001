"""多无人机配置解析。

优先解析 DRONES 列表;为空/缺失时从旧 GLOBAL_PLANNER 生成单机兼容配置,
保证删掉 DRONES 后老单机流程照常运行。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# GLOBAL_PLANNER 中属于"规划器参数"的键,会被每架机继承(可被 DRONES 条目级覆盖)。
# 注意:namespace / drone_id / goal_topic 不在此列,它们是每架机各自的标识。
_PLANNER_PARAM_KEYS = (
    "pcd_path", "planning_z", "resolution", "inflation_radius",
    "enable_ray_clearing", "clearing_ttl", "ray_step", "hit_keep_radius",
    "map_margin", "obstacle_min_z", "obstacle_max_z", "path_min_spacing",
    "world_frame", "completion_threshold",
)


@dataclass
class DroneConfig:
    """单架无人机的配置。key 是系统内部 / MCP 工具使用的唯一标识(如 a0、b1)。"""

    key: str
    label: str
    namespace: str
    drone_id: str
    command_topic: str
    goal_topic: str
    enabled: bool = True
    planner_params: dict[str, Any] = field(default_factory=dict)

    def planner_config(self) -> dict[str, Any]:
        """构造喂给 KianGlobalPlanner 的 config dict(规划参数 + 本机 topic 标识)。"""
        cfg = dict(self.planner_params)
        cfg["namespace"] = self.namespace
        cfg["drone_id"] = self.drone_id
        cfg["goal_topic"] = self.goal_topic
        cfg["drone_key"] = self.key
        cfg["enabled"] = self.enabled
        return cfg


def _planner_params(source: dict[str, Any]) -> dict[str, Any]:
    return {k: source[k] for k in _PLANNER_PARAM_KEYS if k in source}


def load_drone_configs(config_manager) -> list[DroneConfig]:
    """从配置加载无人机列表。

    优先读 DRONES;为空/缺失时从 GLOBAL_PLANNER 生成单机兼容配置(key = f"{namespace}{drone_id}")。
    """
    global_planner = config_manager.get_config("GLOBAL_PLANNER", {}) or {}
    shared = _planner_params(global_planner)
    drones_raw = config_manager.get_config("DRONES", []) or []

    configs: list[DroneConfig] = []
    if drones_raw:
        for entry in drones_raw:
            namespace = str(entry.get("namespace", "a")).strip("/")
            drone_id = str(entry.get("drone_id", "0"))
            key = str(entry.get("key") or f"{namespace}{drone_id}")
            # 每架机参数 = 共享规划参数 ← 条目级覆盖
            params = dict(shared)
            params.update(_planner_params(entry))
            configs.append(DroneConfig(
                key=key,
                label=str(entry.get("label", key)),
                namespace=namespace,
                drone_id=drone_id,
                command_topic=str(entry.get("command_topic", f"/{namespace}/drone_command")),
                goal_topic=str(
                    entry.get("goal_topic")
                    or global_planner.get("goal_topic", "/goal_with_type")
                ),
                enabled=bool(entry.get("enabled", True)),
                planner_params=params,
            ))
        return configs

    # ── 单机兼容:从 GLOBAL_PLANNER 生成 ──
    namespace = str(global_planner.get("namespace", "a")).strip("/")
    drone_id = str(global_planner.get("drone_id", "0"))
    configs.append(DroneConfig(
        key=f"{namespace}{drone_id}",
        label="默认机",
        namespace=namespace,
        drone_id=drone_id,
        command_topic="/drone_command",  # 单机历史 topic,不带 namespace,保持兼容
        goal_topic=str(global_planner.get("goal_topic", "/goal_with_type")),
        enabled=bool(global_planner.get("enabled", True)),
        planner_params=dict(shared),
    ))
    return configs
