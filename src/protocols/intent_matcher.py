"""
Tier 0 关键词意图直达匹配器。

输入 STT 文本，扫描 config.INTENT_KEYWORDS 中的关键词，
命中 → 返回 {tool, args, ack}，pipeline 直接调 MCP 工具，跳过 LLM。
未命中 → None，pipeline 继续走 Tier 1 / Tier 2。
"""
from dataclasses import dataclass
from typing import Optional

from src.utils.config_manager import ConfigManager
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class IntentHit:
    tool: str          # MCP 工具名（带点，如 drone.takeoff）
    args: dict         # 调用参数
    ack: str           # 命令型 ack 文案；查询型为空字符串（让调用方说工具结果）
    matched: str       # 命中的关键词（仅日志用）


def match_intent(text: str) -> Optional[IntentHit]:
    if not text:
        return None
    config = ConfigManager.get_instance()
    intent_map = config.get_config("INTENT_KEYWORDS", {}) or {}
    if not intent_map:
        return None

    norm = text.strip().lower()
    for tool_name, spec in intent_map.items():
        if not isinstance(spec, dict):
            continue
        for kw in spec.get("keywords", []) or []:
            if kw and kw.lower() in norm:
                hit = IntentHit(
                    tool=tool_name,
                    args={},
                    ack=spec.get("ack", "") or "",
                    matched=kw,
                )
                logger.info(
                    "[Tier0] 命中关键词 '%s' → 工具 %s ack=%r",
                    kw, tool_name, hit.ack,
                )
                return hit
    return None


def match_tier2_direct(text: str) -> bool:
    """Tier 2 直达：高级语义关键词命中 → 跳过 Tier 1 直接走完整工具。"""
    if not text:
        return False
    config = ConfigManager.get_instance()
    keywords = config.get_config("ROUTER.tier2_keywords", []) or []
    if not keywords:
        return False
    norm = text.lower()
    for kw in keywords:
        if kw and kw.lower() in norm:
            logger.info("[Tier2-direct] 命中关键词 '%s'，跳过 Tier 1", kw)
            return True
    return False
