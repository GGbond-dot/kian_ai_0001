"""
LLM Client 模块
轻量封装 OpenAI async client，支持所有兼容 OpenAI API 格式的 LLM 端点
（如 Ollama、vLLM、llama.cpp server、DeepSeek、本地 OpenAI-compatible 服务等）。
此文件无任何隐藏系统提示或注入逻辑，所有参数完全透明。
"""
import json
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.utils.config_manager import ConfigManager
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


def _rough_token_count(text: str) -> int:
    """
    粗估 token 数：经验值 qwen 系列约 3-3.5 字符/token（CJK 字符权重高于 ASCII 单词）。
    用于诊断埋点，不是计费用，误差可接受。
    """
    if not text:
        return 0
    return max(1, len(text) // 3 + 1)


def _estimate_messages_tokens(messages: Optional[List[Dict[str, Any]]]) -> int:
    if not messages:
        return 0
    total = 0
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            total += _rough_token_count(content)
        elif isinstance(content, list):
            total += _rough_token_count(json.dumps(content, ensure_ascii=False))
        for tc in (m.get("tool_calls") or []):
            total += _rough_token_count(json.dumps(tc, ensure_ascii=False))
        # role/key 开销极小，忽略
    return total


def _estimate_tools_tokens(tools: Optional[List[Dict[str, Any]]]) -> int:
    if not tools:
        return 0
    return _rough_token_count(json.dumps(tools, ensure_ascii=False))


@dataclass
class _CompatFunction:
    name: str
    arguments: str


@dataclass
class _CompatToolCall:
    id: str
    function: _CompatFunction


@dataclass
class _CompatMessage:
    content: str
    tool_calls: List[_CompatToolCall]


@dataclass
class _CompatChoice:
    message: _CompatMessage


@dataclass
class _CompatResponse:
    id: str
    choices: List[_CompatChoice]
    raw_response: Any


class LLMClient:
    """
    异步 LLM 客户端，封装 openai.AsyncOpenAI。

    所有对话参数（messages、tools、temperature 等）完全由调用方控制，
    此类不做任何提示词注入或记忆操作。
    """

    def __init__(self, config_section: str = "LLM"):
        config = ConfigManager.get_instance()
        self._config_section = config_section
        self._base_url: str = config.get_config(
            f"{config_section}.base_url", "http://localhost:11434/v1"
        )
        self._api_key: str = config.get_config(f"{config_section}.api_key", "ollama")
        self._model: str = config.get_config(f"{config_section}.model", "qwen2.5:7b")
        self._max_tokens: int = int(config.get_config(f"{config_section}.max_tokens", 2048))
        self._temperature: float = float(config.get_config(f"{config_section}.temperature", 0.7))
        self._client = None  # 懒加载

    @property
    def model(self) -> str:
        return self._model

    def _normalized_base_url(self) -> str:
        base_url = (self._base_url or "").strip().rstrip("/")
        if base_url.endswith("/responses"):
            base_url = base_url[: -len("/responses")]
        return base_url

    def uses_responses_api(self) -> bool:
        base_url = self._normalized_base_url()
        return (
            "ark.cn-beijing.volces.com/api/v3" in base_url
            and self._model.startswith("doubao-seed-")
        )

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise RuntimeError("openai 未安装，请执行：pip install openai")

        self._client = AsyncOpenAI(
            base_url=self._normalized_base_url(),
            api_key=self._api_key,
        )
        logger.info(
            f"LLMClient 初始化：base_url={self._normalized_base_url()}, model={self._model}"
        )
        return self._client

    def _messages_to_responses_input(
        self, messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for message in messages:
            role = message.get("role")
            content = message.get("content") or ""

            if role in {"system", "user"}:
                items.append({"role": role, "content": content})
                continue

            if role == "assistant":
                tool_calls = message.get("tool_calls") or []
                if tool_calls:
                    for tool_call in tool_calls:
                        function = tool_call.get("function", {})
                        items.append(
                            {
                                "type": "function_call",
                                "call_id": tool_call.get("id"),
                                "name": function.get("name"),
                                "arguments": function.get("arguments", "{}"),
                            }
                        )
                elif content:
                    items.append({"role": "assistant", "content": content})
                continue

            if role == "tool":
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": message.get("tool_call_id"),
                        "output": content,
                    }
                )

        return items

    def _responses_to_compat(self, response: Any) -> _CompatResponse:
        text_parts: List[str] = []
        tool_calls: List[_CompatToolCall] = []

        for item in getattr(response, "output", []) or []:
            item_type = getattr(item, "type", None)
            if item_type == "message":
                for content_item in getattr(item, "content", []) or []:
                    if getattr(content_item, "type", None) == "output_text":
                        text_parts.append(getattr(content_item, "text", ""))
            elif item_type == "function_call":
                tool_calls.append(
                    _CompatToolCall(
                        id=getattr(item, "call_id", ""),
                        function=_CompatFunction(
                            name=getattr(item, "name", ""),
                            arguments=getattr(item, "arguments", "{}"),
                        ),
                    )
                )

        message = _CompatMessage(content="".join(text_parts).strip(), tool_calls=tool_calls)
        return _CompatResponse(
            id=getattr(response, "id", ""),
            choices=[_CompatChoice(message=message)],
            raw_response=response,
        )

    def _tools_to_responses_tools(
        self, tools: Optional[List[Dict[str, Any]]]
    ) -> Optional[List[Dict[str, Any]]]:
        if not tools:
            return tools

        converted: List[Dict[str, Any]] = []
        for tool in tools:
            if tool.get("type") != "function":
                converted.append(tool)
                continue

            function = tool.get("function", {})
            converted.append(
                {
                    "type": "function",
                    "name": function.get("name"),
                    "description": function.get("description", ""),
                    "parameters": function.get("parameters", {"type": "object", "properties": {}}),
                }
            )
        return converted

    def _tool_choice_to_responses_tool_choice(self, tool_choice: Any) -> Any:
        if not isinstance(tool_choice, dict):
            return tool_choice

        if tool_choice.get("type") != "function":
            return tool_choice

        function = tool_choice.get("function", {})
        return {"type": "function", "name": function.get("name")}

    async def _responses_completion(
        self,
        input_items: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: str = "auto",
        previous_response_id: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ) -> _CompatResponse:
        client = self._get_client()

        kwargs: Dict[str, Any] = {
            "model": self._model,
            "input": input_items,
            "max_output_tokens": self._max_tokens,
            "temperature": self._temperature,
        }
        if tools:
            kwargs["tools"] = self._tools_to_responses_tools(tools)
            kwargs["tool_choice"] = self._tool_choice_to_responses_tool_choice(tool_choice)
        if previous_response_id:
            kwargs["previous_response_id"] = previous_response_id
        if system_prompt:
            kwargs["instructions"] = system_prompt

        logger.debug(
            f"LLM Responses 请求：model={self._model}, "
            f"input_items={len(input_items)}, tools={len(tools) if tools else 0}, "
            f"previous_response_id={previous_response_id or '<none>'}"
        )

        response = await client.responses.create(**kwargs)
        return self._responses_to_compat(response)

    async def chat_completion(
        self,
        messages: Optional[List[Dict[str, Any]]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: str = "auto",
        stream: bool = False,
        input_items: Optional[List[Dict[str, Any]]] = None,
        previous_response_id: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ):
        """
        发送 chat completion 请求，返回 ChatCompletion 对象。

        Args:
            messages:    完整消息列表（包含 system/user/assistant/tool 等角色）
            tools:       OpenAI function calling 格式的工具定义列表，None 表示不启用工具
            tool_choice: "auto"（默认）| "none" | {"type":"function","function":{"name":...}}
            stream:      是否流式输出（当前 Agent 循环使用非流式）
        Returns:
            openai.types.chat.ChatCompletion
        """
        if self.uses_responses_api():
            # responses API 路径暂不支持流式（事件类型不同，需另行适配）
            if input_items is None:
                input_items = self._messages_to_responses_input(messages or [])
            return await self._responses_completion(
                input_items=input_items,
                tools=tools,
                tool_choice=tool_choice,
                previous_response_id=previous_response_id,
                system_prompt=system_prompt,
            )

        client = self._get_client()

        kwargs: Dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice
        if stream:
            kwargs["stream"] = True

        # ── prompt 体量埋点 ──
        msg_tok = _estimate_messages_tokens(messages)
        tool_tok = _estimate_tools_tokens(tools)
        n_tools = len(tools) if tools else 0
        n_msgs = len(messages) if messages else 0
        logger.info(
            "[LLM/prompt] 估 token: msg=%d (%d 条) tool=%d (%d 个) total≈%d, model=%s, stream=%s",
            msg_tok, n_msgs, tool_tok, n_tools, msg_tok + tool_tok, self._model, stream,
        )

        # stream=True 时返回 openai.AsyncStream[ChatCompletionChunk]，调用方需 async for
        # stream=False 时返回完整 ChatCompletion 对象
        t0 = time.perf_counter()
        response = await client.chat.completions.create(**kwargs)
        ttfb_ms = (time.perf_counter() - t0) * 1000
        # stream=True 下：返回时已收到 HTTP headers，等于 TTFB（首字节）
        # stream=False 下：返回时全部内容已收完
        logger.info(
            "[LLM/timing] POST→响应对象 %.0fms (stream=%s, total≈%d token)",
            ttfb_ms, stream, msg_tok + tool_tok,
        )
        return response
