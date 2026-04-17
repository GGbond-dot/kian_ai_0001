"""
LLM Agent 模块
实现 ReAct 风格的 Tool-use Agent 循环：
  1. 接收用户输入
  2. 调用 LLM（携带工具定义）
  3. 若 LLM 返回 tool_calls → 执行工具 → 将结果放回 messages → 再次调用 LLM
  4. 重复步骤 2-3 直到 LLM 返回纯文本回复
  5. 返回最终文字

System prompt 100% 来自 config.json[LLM.system_prompt]，无任何隐藏注入。
"""
import asyncio
import json
from typing import Any, Callable, Dict, List, Optional

from src.llm.llm_client import LLMClient
from src.llm.memory_store import MemoryStore
from src.utils.config_manager import ConfigManager
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

# 最大工具调用轮数（防止无限循环）
MAX_TOOL_CALL_ROUNDS = 10


class LLMAgent:
    """
    自主 Tool-use Agent。

    对话历史完全透明：self.conversation_history 是标准 OpenAI messages 列表，
    不含任何隐藏字段。System prompt 仅在每次请求时于第一条消息注入（来自配置文件）。
    """

    def __init__(self, llm_client: Optional[LLMClient] = None):
        config = ConfigManager.get_instance()
        self._system_prompt: str = config.get_config(
            "LLM.system_prompt",
            "你是一个有用的AI助手，可以帮助用户完成各种任务。",
        )
        self._max_history_turns: int = int(
            config.get_config("LLM.max_history_turns", 20)
        )
        self._summary_max_chars: int = int(
            config.get_config("MEMORY.summary_max_chars", 400)
        )
        self._summary_source_messages: int = int(
            config.get_config("MEMORY.summary_source_messages", 16)
        )
        self.llm_client = llm_client or LLMClient()
        self.memory_store = MemoryStore(config)
        # 对话历史（仅 user/assistant/tool 消息，system 消息在请求时动态插入）
        self.conversation_history: List[Dict[str, Any]] = self.memory_store.load_recent_history(
            limit=self._max_history_turns * 2
        )
        self._previous_response_id: Optional[str] = None
        self._memory_summary_task: Optional[asyncio.Task] = None
        self._memory_summary_generation: int = 0
        if self.conversation_history:
            logger.info("已加载持久对话记忆: %s 条", len(self.conversation_history))

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------
    def reset(self):
        """清空对话历史（保留 system prompt 配置，但不在历史中存储）。"""
        self.conversation_history.clear()
        self._previous_response_id = None
        self.memory_store.clear_recent_history()
        logger.info("Agent 对话历史已清空")

    def get_history(self) -> List[Dict[str, Any]]:
        """返回当前对话历史的副本（不含 system 消息）。"""
        return list(self.conversation_history)

    async def run(
        self,
        user_input: str,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_executor: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
    ) -> str:
        """
        执行完整 Agent 循环，返回最终文字回复。

        Args:
            user_input:    用户输入文本
            tools:         OpenAI function calling 格式的工具列表
                           （由 McpServer.get_openai_tools() 提供）
            tool_executor: 异步可调用对象，签名 async (tool_name, arguments) -> Any
                           （由 McpServer.execute_tool() 提供）
        Returns:
            LLM 最终文字回复（str）
        """
        if self.llm_client.uses_responses_api():
            return await self._run_with_responses_api(
                user_input=user_input,
                tools=tools,
                tool_executor=tool_executor,
            )

        # 追加用户消息
        self._append_history(
            {"role": "user", "content": user_input},
            learn_from_user=True,
        )

        for round_idx in range(MAX_TOOL_CALL_ROUNDS):
            # 组装带 system prompt 的完整 messages
            messages = self._build_messages()

            try:
                response = await self.llm_client.chat_completion(
                    messages=messages,
                    tools=tools if tools else None,
                )
            except Exception as e:
                logger.error(f"LLM 请求失败（第 {round_idx + 1} 轮）：{e}")
                error_msg = f"抱歉，LLM 请求出现错误：{e}"
                self._append_history(
                    {"role": "assistant", "content": error_msg}
                )
                return error_msg

            choice = response.choices[0]
            message = choice.message

            # ── 无工具调用：返回纯文本 ──────────────────────────────
            if not message.tool_calls:
                reply = message.content or ""
                self._append_history(
                    {"role": "assistant", "content": reply}
                )
                logger.info(
                    f"Agent 完成（共 {round_idx + 1} 轮），"
                    f"回复长度={len(reply)}"
                )
                return reply

            # ── 有工具调用：执行工具后继续 ──────────────────────────
            # 先将 assistant 的 tool_calls 消息加入历史
            self._append_history(
                {
                    "role": "assistant",
                    "content": message.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in message.tool_calls
                    ],
                },
                persist=False,
            )

            # 并行执行所有工具调用
            tool_results = await self._execute_tool_calls(
                message.tool_calls, tool_executor
            )

            # 将所有工具结果追加到历史
            for tool_call, result_str in zip(message.tool_calls, tool_results):
                self._append_history(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result_str,
                    },
                    persist=False,
                )

            logger.debug(
                f"第 {round_idx + 1} 轮工具调用完成，"
                f"调用数={len(message.tool_calls)}"
            )
            # 继续下一轮 LLM 请求

        # 超过最大轮数
        fallback = "抱歉，工具调用轮次超出限制，无法完成请求。"
        self._append_history({"role": "assistant", "content": fallback})
        return fallback

    async def _run_with_responses_api(
        self,
        user_input: str,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_executor: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
    ) -> str:
        self._append_history(
            {"role": "user", "content": user_input},
            learn_from_user=True,
        )

        previous_response_id = self._previous_response_id
        input_items: List[Dict[str, Any]] = [{"role": "user", "content": user_input}]

        for round_idx in range(MAX_TOOL_CALL_ROUNDS):
            try:
                response = await self.llm_client.chat_completion(
                    input_items=input_items,
                    previous_response_id=previous_response_id,
                    system_prompt=self._build_system_prompt()
                    if previous_response_id is None
                    else None,
                    tools=tools if tools else None,
                )
            except Exception as e:
                logger.error(f"LLM 请求失败（第 {round_idx + 1} 轮）：{e}")
                error_msg = f"抱歉，LLM 请求出现错误：{e}"
                self._append_history(
                    {"role": "assistant", "content": error_msg}
                )
                return error_msg

            previous_response_id = response.id
            choice = response.choices[0]
            message = choice.message

            if not message.tool_calls:
                reply = message.content or ""
                self._append_history(
                    {"role": "assistant", "content": reply}
                )
                self._previous_response_id = previous_response_id
                logger.info(
                    f"Agent 完成（共 {round_idx + 1} 轮，responses API），"
                    f"回复长度={len(reply)}"
                )
                return reply

            self._append_history(
                {
                    "role": "assistant",
                    "content": message.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in message.tool_calls
                    ],
                },
                persist=False,
            )

            tool_results = await self._execute_tool_calls(
                message.tool_calls, tool_executor
            )

            input_items = []
            for tool_call, result_str in zip(message.tool_calls, tool_results):
                self._append_history(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result_str,
                    },
                    persist=False,
                )
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": tool_call.id,
                        "output": result_str,
                    }
                )

            logger.debug(
                f"第 {round_idx + 1} 轮工具调用完成（responses API），"
                f"调用数={len(message.tool_calls)}"
            )

        fallback = "抱歉，工具调用轮次超出限制，无法完成请求。"
        self._append_history({"role": "assistant", "content": fallback})
        return fallback

    # ------------------------------------------------------------------
    # 私有辅助
    # ------------------------------------------------------------------
    def _build_messages(self) -> List[Dict[str, Any]]:
        """在 conversation_history 前插入 system 消息，构成完整 messages 列表。"""
        system_msg = {"role": "system", "content": self._build_system_prompt()}
        return [system_msg] + self.conversation_history

    def _build_system_prompt(self) -> str:
        memory_prompt = self.memory_store.build_prompt_block()
        if not memory_prompt:
            return self._system_prompt
        return f"{self._system_prompt}\n\n{memory_prompt}"

    def _trim_history(self):
        """
        按 max_history_turns 截断历史。
        保留最近 max_history_turns * 2 条消息（每轮含 user + assistant）。
        """
        max_msgs = self._max_history_turns * 2
        if len(self.conversation_history) > max_msgs:
            removed = len(self.conversation_history) - max_msgs
            self.conversation_history = self.conversation_history[-max_msgs:]
            logger.debug(f"裁剪对话历史，移除 {removed} 条旧消息")

    def _append_history(
        self,
        message: Dict[str, Any],
        *,
        learn_from_user: bool = False,
        persist: bool = True,
    ) -> None:
        self.conversation_history.append(message)
        self._trim_history()
        if learn_from_user and message.get("role") == "user":
            self.memory_store.remember_user_text(str(message.get("content") or ""))
        if persist:
            self.memory_store.persist_recent_history(self.conversation_history)
            if (
                self.memory_store.summary_enabled
                and message.get("role") == "assistant"
                and not message.get("tool_calls")
            ):
                self._schedule_memory_summary_refresh()

    def remember_exchange(self, user_input: str, assistant_reply: str) -> None:
        self._append_history(
            {"role": "user", "content": user_input},
            learn_from_user=True,
            persist=False,
        )
        self._append_history({"role": "assistant", "content": assistant_reply})

    def _conversation_snapshot_for_summary(self) -> List[Dict[str, str]]:
        snapshot: List[Dict[str, str]] = []
        for message in self.conversation_history:
            if not isinstance(message, dict):
                continue
            if message.get("tool_calls"):
                continue
            role = str(message.get("role") or "").strip()
            content = str(message.get("content") or "").strip()
            if role not in {"user", "assistant"} or not content:
                continue
            snapshot.append({"role": role, "content": content})
        return snapshot[-self._summary_source_messages :]

    def _schedule_memory_summary_refresh(self) -> None:
        if not self.memory_store.enabled or not self.memory_store.summary_enabled:
            return
        snapshot = self._conversation_snapshot_for_summary()
        if len(snapshot) < 2:
            return
        self._memory_summary_generation += 1
        generation = self._memory_summary_generation
        if self._memory_summary_task and not self._memory_summary_task.done():
            self._memory_summary_task.cancel()
        try:
            self._memory_summary_task = asyncio.create_task(
                self._refresh_memory_summary(snapshot, generation),
                name="llm-memory-summary",
            )
        except RuntimeError:
            logger.debug("当前无事件循环，跳过摘要记忆刷新")

    async def _refresh_memory_summary(
        self,
        snapshot: List[Dict[str, str]],
        generation: int,
    ) -> None:
        try:
            previous_summary = self.memory_store.get_conversation_summary() or "暂无"
            transcript = "\n".join(
                f"{'用户' if item['role'] == 'user' else '助手'}: {item['content']}"
                for item in snapshot
            )
            response = await self.llm_client.chat_completion(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是对话长期记忆整理器。请把“旧摘要 + 最新对话”融合成新的滚动记忆。"
                            "要求："
                            "1. 用中文输出；"
                            "2. 保留后续聊天真正有用的信息：最近聊到的话题、用户关心点、偏好、情绪、未完成事项、你答应过的事；"
                            "3. 删除寒暄、重复套话、一次性噪音；"
                            "4. 不要写逐字稿，不要编造；"
                            f"5. 总长度尽量控制在{self._summary_max_chars}字内；"
                            "6. 直接输出记忆内容，不要加标题和解释。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"旧摘要：\n{previous_summary}\n\n"
                            f"最新对话：\n{transcript}\n\n"
                            "请输出融合后的新摘要。"
                        ),
                    },
                ],
                tools=None,
                tool_choice="none",
            )
            summary = (response.choices[0].message.content or "").strip()
            if not summary:
                summary = self.memory_store.build_fallback_conversation_summary(snapshot)
            if generation == self._memory_summary_generation and summary:
                self.memory_store.update_conversation_summary(summary)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("生成滚动对话记忆失败，回退到规则摘要: %s", exc)
            summary = self.memory_store.build_fallback_conversation_summary(snapshot)
            if generation == self._memory_summary_generation and summary:
                self.memory_store.update_conversation_summary(summary)

    async def _execute_tool_calls(
        self,
        tool_calls,
        tool_executor: Optional[Callable],
    ) -> List[str]:
        """并行执行所有 tool_calls，返回对应结果字符串列表。"""
        if not tool_executor:
            return [
                json.dumps({"error": "no tool_executor provided"})
                for _ in tool_calls
            ]

        async def _single(tc) -> str:
            try:
                name = tc.function.name
                raw_args = tc.function.arguments
                arguments = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                logger.info(f"执行工具：{name}，参数：{arguments}")
                result = await tool_executor(name, arguments)
                # result 可能是 dict/str；统一序列化为字符串
                if isinstance(result, str):
                    return result
                return json.dumps(result, ensure_ascii=False)
            except Exception as e:
                logger.error(f"工具 {tc.function.name} 执行失败：{e}", exc_info=True)
                return json.dumps({"error": str(e)})

        return list(await asyncio.gather(*[_single(tc) for tc in tool_calls]))
