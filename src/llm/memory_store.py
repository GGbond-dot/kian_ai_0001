from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.utils.config_manager import ConfigManager
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class MemoryStore:
    """轻量级持久记忆。

    目标分两层：
    1. 保存最近对话，解决重启后完全失忆的问题。
    2. 提取少量长期事实，注入 system prompt，让模型能记住用户资料/偏好。
    """

    FILE_NAME = "user_memory.json"

    def __init__(self, config: Optional[ConfigManager] = None):
        self.config = config or ConfigManager.get_instance()
        self.enabled = bool(self.config.get_config("MEMORY.enabled", True))
        self.max_recent_messages = int(
            self.config.get_config("MEMORY.max_recent_messages", 24)
        )
        self.max_explicit_memories = int(
            self.config.get_config("MEMORY.max_explicit_memories", 12)
        )
        self.summary_enabled = bool(
            self.config.get_config("MEMORY.summary_enabled", True)
        )
        self.summary_history_limit = int(
            self.config.get_config("MEMORY.summary_history_limit", 12)
        )
        self.file_path = Path(self.config.config_dir) / self.FILE_NAME
        self.data: Dict[str, Any] = self._default_data()
        self._load()

    def _default_data(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "updated_at": 0.0,
            "conversation_summary": "",
            "summary_history": [],
            "profile": {},
            "preferences": {
                "likes": [],
                "dislikes": [],
            },
            "explicit_memories": [],
            "recent_history": [],
        }

    def _load(self) -> None:
        if not self.enabled:
            return
        if not self.file_path.exists():
            return
        try:
            payload = json.loads(self.file_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                self.data = self._merge_dict(self._default_data(), payload)
        except Exception as exc:
            logger.warning("记忆文件加载失败，使用空记忆: %s", exc)

    def _save(self) -> None:
        if not self.enabled:
            return
        try:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            self.data["updated_at"] = time.time()
            self.file_path.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("记忆文件保存失败: %s", exc)

    def _merge_dict(self, base: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
        result = dict(base)
        for key, value in incoming.items():
            if (
                key in result
                and isinstance(result[key], dict)
                and isinstance(value, dict)
            ):
                result[key] = self._merge_dict(result[key], value)
            else:
                result[key] = value
        return result

    def load_recent_history(self, *, limit: Optional[int] = None) -> List[Dict[str, str]]:
        if not self.enabled:
            return []
        messages: List[Dict[str, str]] = []
        for item in self.data.get("recent_history", []):
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip()
            content = str(item.get("content") or "").strip()
            if role not in {"user", "assistant"} or not content:
                continue
            messages.append({"role": role, "content": content})
        if limit is not None and limit > 0:
            return messages[-limit:]
        return messages

    def persist_recent_history(self, messages: List[Dict[str, Any]]) -> None:
        if not self.enabled:
            return
        filtered: List[Dict[str, str]] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "").strip()
            content = str(message.get("content") or "").strip()
            if role not in {"user", "assistant"} or not content:
                continue
            filtered.append({"role": role, "content": content})
        self.data["recent_history"] = filtered[-self.max_recent_messages :]
        self._save()

    def clear_recent_history(self) -> None:
        self.data["recent_history"] = []
        self._save()

    def clear_all(self) -> None:
        self.data = self._default_data()
        self._save()

    def remember_user_text(self, user_text: str) -> None:
        if not self.enabled:
            return
        text = self._normalize_text(user_text)
        if not text:
            return

        changed = False

        name = self._match_first(
            text,
            (
                r"(?:我叫|叫我)([^\s，。,.!！？?]{1,20})",
                r"我的名字是([^\s，。,.!！？?]{1,20})",
            ),
        )
        if name:
            changed |= self._set_profile_value("name", name)

        identity = self._match_first(
            text,
            (
                r"我是([^\s，。,.!！？?]{1,20})",
                r"我是一名([^\s，。,.!！？?]{1,20})",
                r"我是一位([^\s，。,.!！？?]{1,20})",
            ),
        )
        if identity and identity not in {"男的", "女的"}:
            changed |= self._set_profile_value("identity", identity)

        location = self._match_first(
            text,
            (
                r"我住在([^，。,.!！？?\n]{1,30})",
                r"我在([^，。,.!！？?\n]{1,30})(?:上班|工作|读书|学习)",
            ),
        )
        if location:
            changed |= self._set_profile_value("location", location)

        like = self._match_first(
            text,
            (
                r"(?:我喜欢|我爱)([^，。,.!！？?\n]{1,30})",
                r"我平时喜欢([^，。,.!！？?\n]{1,30})",
            ),
        )
        if like:
            changed |= self._append_unique("preferences.likes", like)

        dislike = self._match_first(
            text,
            (
                r"(?:我不喜欢|我讨厌)([^，。,.!！？?\n]{1,30})",
                r"我不爱([^，。,.!！？?\n]{1,30})",
            ),
        )
        if dislike:
            changed |= self._append_unique("preferences.dislikes", dislike)

        explicit = self._match_first(
            text,
            (
                r"(?:记住|帮我记住|请记住)([^。！？!\n]{1,80})",
            ),
        )
        if explicit:
            changed |= self._append_explicit_memory(explicit)

        if changed:
            logger.info("记忆已更新: %s", self.file_path)
            self._save()

    def get_conversation_summary(self) -> str:
        return str(self.data.get("conversation_summary") or "").strip()

    def update_conversation_summary(self, summary: str) -> None:
        if not self.enabled or not self.summary_enabled:
            return
        summary = self._normalize_summary(summary)
        if not summary:
            return
        self.data["conversation_summary"] = summary
        summary_history = self.data.setdefault("summary_history", [])
        summary_history.append(
            {
                "ts": time.time(),
                "summary": summary,
            }
        )
        if len(summary_history) > self.summary_history_limit:
            self.data["summary_history"] = summary_history[-self.summary_history_limit :]
        self._save()

    def build_prompt_block(self) -> str:
        if not self.enabled:
            return ""

        sections: List[str] = []
        lines: List[str] = []
        conversation_summary = self.get_conversation_summary()
        profile = self.data.get("profile") or {}
        preferences = self.data.get("preferences") or {}
        explicit_memories = self.data.get("explicit_memories") or []

        if conversation_summary:
            sections.append(
                "以下是你和当前用户过往对话的滚动记忆摘要，只在相关时自然用上，不要整段复读：\n"
                + conversation_summary
            )

        name = str(profile.get("name") or "").strip()
        identity = str(profile.get("identity") or "").strip()
        location = str(profile.get("location") or "").strip()
        likes = [str(item).strip() for item in preferences.get("likes", []) if str(item).strip()]
        dislikes = [
            str(item).strip()
            for item in preferences.get("dislikes", [])
            if str(item).strip()
        ]

        if name:
            lines.append(f"- 用户名字：{name}")
        if identity:
            lines.append(f"- 用户身份：{identity}")
        if location:
            lines.append(f"- 用户所在地/常驻地：{location}")
        if likes:
            lines.append(f"- 用户喜欢：{'、'.join(likes[-5:])}")
        if dislikes:
            lines.append(f"- 用户不喜欢：{'、'.join(dislikes[-5:])}")

        cleaned_explicit = [
            str(item).strip()
            for item in explicit_memories[-5:]
            if str(item).strip()
        ]
        for memory in cleaned_explicit:
            lines.append(f"- 额外记忆：{memory}")

        if lines:
            sections.append(
                "以下是你对当前用户的长期事实记忆，只在相关时自然使用，不要生硬逐条复述：\n"
                + "\n".join(lines)
            )

        if not sections:
            return ""
        return "\n\n".join(sections)

    def build_fallback_conversation_summary(
        self,
        messages: List[Dict[str, Any]],
        *,
        max_pairs: int = 6,
    ) -> str:
        compact_messages: List[Dict[str, str]] = []
        for item in messages:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip()
            content = self._normalize_text(str(item.get("content") or ""))
            if role not in {"user", "assistant"} or not content:
                continue
            compact_messages.append({"role": role, "content": content})

        if not compact_messages:
            return self.get_conversation_summary()

        pairs: List[str] = []
        user_text = ""
        for item in compact_messages[-max_pairs * 2 :]:
            role = item["role"]
            content = item["content"]
            if role == "user":
                user_text = content[:80]
                continue
            assistant_text = content[:80]
            if user_text:
                pairs.append(f"- 用户提到：{user_text}；助手回应：{assistant_text}")
                user_text = ""
            else:
                pairs.append(f"- 助手回应：{assistant_text}")

        previous = self.get_conversation_summary()
        if previous:
            return self._normalize_summary(previous + "\n" + "\n".join(pairs[-max_pairs:]))
        return self._normalize_summary("\n".join(pairs[-max_pairs:]))

    def _normalize_text(self, text: str) -> str:
        return re.sub(r"\s+", " ", (text or "").strip())

    def _normalize_summary(self, summary: str) -> str:
        summary = re.sub(r"\n{3,}", "\n\n", (summary or "").strip())
        return summary[:1200].strip()

    def _match_first(self, text: str, patterns) -> Optional[str]:
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if not match:
                continue
            value = match.group(1).strip(" ，。,.!！？?")
            if value:
                return value
        return None

    def _set_profile_value(self, key: str, value: str) -> bool:
        profile = self.data.setdefault("profile", {})
        value = value.strip()
        if not value or profile.get(key) == value:
            return False
        profile[key] = value
        return True

    def _append_unique(self, path: str, value: str) -> bool:
        value = value.strip()
        if not value:
            return False
        current: Any = self.data
        *parts, last = path.split(".")
        for part in parts:
            current = current.setdefault(part, {})
        bucket = current.setdefault(last, [])
        normalized_bucket = [str(item).strip() for item in bucket]
        if value in normalized_bucket:
            return False
        bucket.append(value)
        return True

    def _append_explicit_memory(self, value: str) -> bool:
        value = value.strip()
        if not value:
            return False
        bucket = self.data.setdefault("explicit_memories", [])
        if value in bucket:
            return False
        bucket.append(value)
        if len(bucket) > self.max_explicit_memories:
            self.data["explicit_memories"] = bucket[-self.max_explicit_memories :]
        return True
