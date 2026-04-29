"""
MCP Server Implementation for Python
Reference: https://modelcontextprotocol.io/specification/2024-11-05
"""

import asyncio
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from src.constants.system import SystemConstants
from src.utils.logging_config import get_logger
from src.mcp.tools.robot_dispatch.manager import get_robot_dispatch_manager

logger = get_logger(__name__)

# 返回值类型
ReturnValue = Union[bool, int, str]


class PropertyType(Enum):
    """
    属性类型枚举.
    """

    BOOLEAN = "boolean"
    INTEGER = "integer"
    STRING = "string"


@dataclass
class Property:
    """
    MCP工具属性定义.
    """

    name: str
    type: PropertyType
    default_value: Optional[Any] = None
    min_value: Optional[int] = None
    max_value: Optional[int] = None

    @property
    def has_default_value(self) -> bool:
        return self.default_value is not None

    @property
    def has_range(self) -> bool:
        return self.min_value is not None and self.max_value is not None

    def value(self, value: Any) -> Any:
        """
        验证并返回值.
        """
        if self.type == PropertyType.INTEGER and self.has_range:
            if value < self.min_value:
                raise ValueError(
                    f"Value {value} is below minimum allowed: " f"{self.min_value}"
                )
            if value > self.max_value:
                raise ValueError(
                    f"Value {value} exceeds maximum allowed: " f"{self.max_value}"
                )
        return value

    def to_json(self) -> Dict[str, Any]:
        """
        转换为JSON格式.
        """
        result = {"type": self.type.value}

        if self.has_default_value:
            result["default"] = self.default_value

        if self.type == PropertyType.INTEGER:
            if self.min_value is not None:
                result["minimum"] = self.min_value
            if self.max_value is not None:
                result["maximum"] = self.max_value

        return result


@dataclass
class PropertyList:
    """
    属性列表.
    """

    properties: List[Property] = field(default_factory=list)

    def __init__(self, properties: Optional[List[Property]] = None):
        """
        初始化属性列表.
        """
        self.properties = properties or []

    def add_property(self, prop: Property):
        self.properties.append(prop)

    def __getitem__(self, name: str) -> Property:
        for prop in self.properties:
            if prop.name == name:
                return prop
        raise KeyError(f"Property not found: {name}")

    def get_required(self) -> List[str]:
        """
        获取必需的属性名称列表.
        """
        return [p.name for p in self.properties if not p.has_default_value]

    def to_json(self) -> Dict[str, Any]:
        """
        转换为JSON格式.
        """
        return {prop.name: prop.to_json() for prop in self.properties}

    def parse_arguments(self, arguments: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        解析并验证参数.
        """
        result = {}

        for prop in self.properties:
            if arguments and prop.name in arguments:
                value = arguments[prop.name]
                # 类型检查
                if prop.type == PropertyType.BOOLEAN and isinstance(value, bool):
                    result[prop.name] = value
                elif prop.type == PropertyType.INTEGER and isinstance(
                    value, (int, float)
                ):
                    result[prop.name] = prop.value(int(value))
                elif prop.type == PropertyType.STRING and isinstance(value, str):
                    result[prop.name] = value
                else:
                    raise ValueError(f"Invalid type for property {prop.name}")
            elif prop.has_default_value:
                result[prop.name] = prop.default_value
            else:
                raise ValueError(f"Missing required argument: {prop.name}")

        return result


@dataclass
class McpTool:
    """
    MCP工具定义.
    """

    name: str
    description: str
    properties: PropertyList
    callback: Callable[[Dict[str, Any]], ReturnValue]

    def to_json(self) -> Dict[str, Any]:
        """
        转换为JSON格式.
        """
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": self.properties.to_json(),
                "required": self.properties.get_required(),
            },
        }

    async def call(self, arguments: Dict[str, Any]) -> str:
        """
        调用工具.
        """
        try:
            # 解析参数
            parsed_args = self.properties.parse_arguments(arguments)

            # 调用回调函数
            if asyncio.iscoroutinefunction(self.callback):
                result = await self.callback(parsed_args)
            else:
                result = self.callback(parsed_args)

            # 格式化返回值
            if isinstance(result, bool):
                text = "true" if result else "false"
            elif isinstance(result, int):
                text = str(result)
            else:
                text = str(result)

            return json.dumps(
                {"content": [{"type": "text", "text": text}], "isError": False}
            )

        except Exception as e:
            logger.error(f"Error calling tool {self.name}: {e}", exc_info=True)
            return json.dumps(
                {"content": [{"type": "text", "text": str(e)}], "isError": True}
            )


class McpServer:
    """
    MCP服务器实现.
    """

    _instance = None

    @classmethod
    def get_instance(cls):
        """
        获取单例实例.
        """
        if cls._instance is None:
            cls._instance = McpServer()
        return cls._instance

    def __init__(self):
        self.tools: List[McpTool] = []
        self._send_callback: Optional[Callable] = None
        self._camera = None

    def set_send_callback(self, callback: Callable):
        """
        设置发送消息的回调函数.
        """
        self._send_callback = callback

    def add_tool(self, tool: Union[McpTool, Tuple[str, str, PropertyList, Callable]]):
        """
        添加工具.
        """
        if isinstance(tool, tuple):
            # 从参数创建McpTool
            name, description, properties, callback = tool
            tool = McpTool(name, description, properties, callback)

        # 检查是否已存在
        if any(t.name == tool.name for t in self.tools):
            logger.warning(f"Tool {tool.name} already added")
            return

        logger.info(f"Add tool: {tool.name}")
        self.tools.append(tool)

    def add_common_tools(self):
        """
        注册 LLM 可见的工具。

        当前定位：物流系统终端机器人，只暴露核心控制工具。
        其他工具（calendar/timer/music/web/camera/screenshot/bazi/system.application）
        源码保留在 src/mcp/tools/ 下，需要时把对应分组加到
        config.json -> LLM.optional_tool_groups 列表里就能恢复注册。

        可选分组与对应 manager:
            calendar / timer / music / web
        """
        from src.utils.config_manager import ConfigManager

        # 备份原有工具列表
        original_tools = self.tools.copy()
        self.tools.clear()

        # ── 核心：系统设备 + 无人机调度（永远开） ──
        from src.mcp.tools.system import get_system_tools_manager

        get_system_tools_manager().init_tools(
            self.add_tool, PropertyList, Property, PropertyType
        )
        get_robot_dispatch_manager().init_tools(
            self.add_tool, PropertyList, Property, PropertyType
        )

        # ── 可选分组：根据 config 决定是否注册 ──
        config = ConfigManager.get_instance()
        enabled_groups = set(
            config.get_config("LLM.optional_tool_groups", []) or []
        )

        if "calendar" in enabled_groups:
            from src.mcp.tools.calendar import get_calendar_manager
            get_calendar_manager().init_tools(
                self.add_tool, PropertyList, Property, PropertyType
            )

        if "timer" in enabled_groups:
            from src.mcp.tools.timer import get_timer_manager
            get_timer_manager().init_tools(
                self.add_tool, PropertyList, Property, PropertyType
            )

        if "music" in enabled_groups:
            from src.mcp.tools.music import get_music_tools_manager
            get_music_tools_manager().init_tools(
                self.add_tool, PropertyList, Property, PropertyType
            )

        if "web" in enabled_groups:
            from src.mcp.tools.web import get_web_manager
            get_web_manager().init_tools(
                self.add_tool, PropertyList, Property, PropertyType
            )

        # camera / screenshot / bazi / system.application 已下线，
        # 需要时去这个文件里手动恢复。

        # 恢复原有工具
        self.tools.extend(original_tools)

    async def parse_message(self, message: Union[str, Dict[str, Any]]):
        """
        解析MCP消息.
        """
        try:
            if isinstance(message, str):
                data = json.loads(message)
            else:
                data = message

            logger.info(
                f"[MCP] 解析消息: {json.dumps(data, ensure_ascii=False, indent=2)}"
            )

            # 检查JSONRPC版本
            if data.get("jsonrpc") != "2.0":
                logger.error(f"Invalid JSONRPC version: {data.get('jsonrpc')}")
                return

            method = data.get("method")
            if not method:
                logger.error("Missing method")
                return

            # 忽略通知
            if method.startswith("notifications"):
                logger.info(f"[MCP] 忽略通知消息: {method}")
                return

            params = data.get("params", {})
            id = data.get("id")

            if id is None:
                logger.error(f"Invalid id for method: {method}")
                return

            logger.info(f"[MCP] 处理方法: {method}, ID: {id}, 参数: {params}")

            # 处理不同的方法
            if method == "initialize":
                await self._handle_initialize(id, params)
            elif method == "tools/list":
                await self._handle_tools_list(id, params)
            elif method == "tools/call":
                await self._handle_tool_call(id, params)
            else:
                logger.error(f"Method not implemented: {method}")
                await self._reply_error(id, f"Method not implemented: {method}")

        except Exception as e:
            logger.error(f"Error parsing MCP message: {e}", exc_info=True)
            if "id" in locals():
                await self._reply_error(id, str(e))

    async def _handle_initialize(self, id: int, params: Dict[str, Any]):
        """
        处理初始化请求.
        """
        # 解析capabilities
        capabilities = params.get("capabilities", {})
        await self._parse_capabilities(capabilities)

        # 返回服务器信息
        result = {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {
                "name": SystemConstants.APP_NAME,
                "version": SystemConstants.APP_VERSION,
            },
        }

        await self._reply_result(id, result)

    async def _handle_tools_list(self, id: int, params: Dict[str, Any]):
        """
        处理工具列表请求.
        """
        cursor = params.get("cursor", "")
        max_payload_size = 8000

        tools_json = []
        total_size = 0
        found_cursor = not cursor
        next_cursor = ""

        for tool in self.tools:
            # 如果还没找到起始位置，继续搜索
            if not found_cursor:
                if tool.name == cursor:
                    found_cursor = True
                else:
                    continue

            # 检查大小
            tool_json = tool.to_json()
            tool_size = len(json.dumps(tool_json))

            if total_size + tool_size + 100 > max_payload_size:
                next_cursor = tool.name
                break

            tools_json.append(tool_json)
            total_size += tool_size

        result = {"tools": tools_json}
        if next_cursor:
            result["nextCursor"] = next_cursor

        await self._reply_result(id, result)

    async def _handle_tool_call(self, id: int, params: Dict[str, Any]):
        """
        处理工具调用请求.
        """
        logger.info(f"[MCP] 收到工具调用请求! ID={id}, 参数={params}")

        tool_name = params.get("name")
        if not tool_name:
            await self._reply_error(id, "Missing tool name")
            return

        logger.info(f"[MCP] 尝试调用工具: {tool_name}")

        # 查找工具
        tool = None
        for t in self.tools:
            if t.name == tool_name:
                tool = t
                break

        if not tool:
            await self._reply_error(id, f"Unknown tool: {tool_name}")
            return

        # 获取参数
        arguments = params.get("arguments", {})
        # 兼容：有些客户端会把 arguments 作为 JSON 字符串发过来
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except Exception:
                logger.warning(f"[MCP] arguments is not valid JSON string: {arguments}")
                arguments = {}



        logger.info(f"[MCP] 开始执行工具 {tool_name}, 参数: {arguments}")

        # 异步调用工具
        try:
            result = await tool.call(arguments)
            logger.info(f"[MCP] 工具 {tool_name} 执行成功，结果: {result}")
            await self._reply_result(id, json.loads(result))
        except Exception as e:
            logger.error(f"[MCP] 工具 {tool_name} 执行失败: {e}", exc_info=True)
            await self._reply_error(id, str(e))

    async def _parse_capabilities(self, capabilities):
        """
        解析capabilities.
        """
        vision = capabilities.get("vision", {})
        if vision and isinstance(vision, dict):
            url = vision.get("url")
            token = vision.get("token")
            if url:
                from src.mcp.tools.camera import get_camera_instance

                camera = get_camera_instance()
                if hasattr(camera, "set_explain_url"):
                    camera.set_explain_url(url)
                if token and hasattr(camera, "set_explain_token"):
                    camera.set_explain_token(token)
                logger.info(f"Vision service configured with URL: {url}")

    async def _reply_result(self, id: int, result: Any):
        """
        发送成功响应.
        """
        payload = {"jsonrpc": "2.0", "id": id, "result": result}

        result_len = len(json.dumps(result))
        logger.info(f"[MCP] 发送成功响应: ID={id}, 结果长度={result_len}")

        if self._send_callback:
            await self._send_callback(json.dumps(payload))
        else:
            logger.error("[MCP] 发送回调未设置!")

    async def _reply_error(self, id: int, message: str):
        """
        发送错误响应.
        """
        payload = {"jsonrpc": "2.0", "id": id, "error": {"message": message}}

        logger.error(f"[MCP] 发送错误响应: ID={id}, 错误={message}")

        if self._send_callback:
            await self._send_callback(json.dumps(payload))

    # ------------------------------------------------------------------
    # OpenAI function calling 支持（供 LocalAgentProtocol / LLMAgent 使用）
    # ------------------------------------------------------------------

    def get_openai_tools(self) -> List[Dict[str, Any]]:
        """
        将所有已注册的 MCP 工具转换为 OpenAI function calling 格式。

        返回值可直接作为 openai.chat.completions.create(tools=...) 的参数。

        PropertyType 到 JSON Schema type 映射：
            BOOLEAN → "boolean"
            INTEGER → "integer"
            STRING  → "string"
        """
        _type_map = {
            PropertyType.BOOLEAN: "boolean",
            PropertyType.INTEGER: "integer",
            PropertyType.STRING: "string",
        }

        import re

        def _sanitize_name(raw: str) -> str:
            """将工具名转为符合 OpenAI 规范的标识符（^[a-zA-Z0-9_-]+$）。
            把所有不合规字符（包括点号）替换为下划线。
            """
            return re.sub(r'[^a-zA-Z0-9_-]', '_', raw)

        result = []
        for tool in self.tools:
            properties: Dict[str, Any] = {}
            for prop in tool.properties.properties:
                prop_schema: Dict[str, Any] = {
                    "type": _type_map.get(prop.type, "string"),
                }
                if prop.type == PropertyType.INTEGER:
                    if prop.min_value is not None:
                        prop_schema["minimum"] = prop.min_value
                    if prop.max_value is not None:
                        prop_schema["maximum"] = prop.max_value
                if prop.has_default_value:
                    prop_schema["default"] = prop.default_value
                prop_schema["description"] = prop.name
                properties[prop.name] = prop_schema

            required = tool.properties.get_required()
            safe_name = _sanitize_name(tool.name)

            openai_tool = {
                "type": "function",
                "function": {
                    "name": safe_name,
                    "description": tool.description,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                },
            }
            result.append(openai_tool)

        names = [t["function"]["name"] for t in result]
        logger.info(f"[工具列表] 共 {len(result)} 个工具传给 LLM：{names}")
        return result

    async def execute_tool(self, name: str, arguments: Dict[str, Any]) -> str:
        """
        直接按工具名执行工具，绕过 JSON-RPC 层，供 LLMAgent 使用。

        支持两种名称格式：
        - 原始名称（含点号，如 'music_player.search_and_play'）
        - 经 get_openai_tools() 转义后的名称（如 'music_player_search_and_play'）

        Args:
            name:      工具名称（原始或转义格式均可）
            arguments: 工具参数（dict）

        Returns:
            工具输出字符串（JSON 格式，与 McpTool.call() 返回一致）

        Raises:
            KeyError: 工具不存在
        """
        import re

        def _sanitize(raw: str) -> str:
            return re.sub(r'[^a-zA-Z0-9_-]', '_', raw)

        # 先精确匹配原始名
        for tool in self.tools:
            if tool.name == name:
                logger.info(f"[Agent] 执行工具：{tool.name}，参数：{arguments}")
                return await tool.call(arguments)

        # 再按转义名模糊匹配（LLM 返回的是 sanitized 名）
        for tool in self.tools:
            if _sanitize(tool.name) == name:
                logger.info(f"[Agent] 执行工具（映射）：{tool.name}，参数：{arguments}")
                return await tool.call(arguments)

        raise KeyError(f"MCP 工具不存在：{name}（已尝试原始名和转义名匹配）")
