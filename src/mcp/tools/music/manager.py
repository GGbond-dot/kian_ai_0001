"""音乐工具管理器.

负责音乐工具的初始化、配置和MCP工具注册
"""

from typing import Any, Dict

from src.utils.logging_config import get_logger

from .music_player import get_music_player_instance

logger = get_logger(__name__)


class MusicToolsManager:
    """
    音乐工具管理器.
    """

    def __init__(self):
        """
        初始化音乐工具管理器.
        """
        self._initialized = False
        self._music_player = None
        logger.info("[MusicManager] 音乐工具管理器初始化")

    def init_tools(self, add_tool, PropertyList, Property, PropertyType):
        """
        初始化并注册所有音乐工具.
        """
        try:
            logger.info("[MusicManager] 开始注册音乐工具")

            # 获取音乐播放器单例实例
            self._music_player = get_music_player_instance()

            # 注册搜索并播放工具
            self._register_search_and_play_tool(
                add_tool, PropertyList, Property, PropertyType
            )

            # 注册暂停 / 恢复 / 停止工具
            self._register_pause_tool(add_tool, PropertyList)
            self._register_resume_tool(add_tool, PropertyList)
            self._register_stop_tool(add_tool, PropertyList)

            # seek / get_lyrics / get_local_playlist：精简 LLM 工具列表，
            # 嵌入式平板场景下不向 LLM 暴露这些细控制。原实现保留备用。

            self._initialized = True
            logger.info("[MusicManager] 音乐工具注册完成")

        except Exception as e:
            logger.error(f"[MusicManager] 音乐工具注册失败: {e}", exc_info=True)
            raise

    def _register_search_and_play_tool(
        self, add_tool, PropertyList, Property, PropertyType
    ):
        """
        注册搜索并播放工具.
        """

        async def search_and_play_wrapper(args: Dict[str, Any]) -> str:
            song_name = args.get("song_name", "")
            result = await self._music_player.search_and_play(song_name)
            return result.get("message", "搜索播放完成")

        search_props = PropertyList([Property("song_name", PropertyType.STRING)])

        add_tool(
            (
                "music_player.search_and_play",
                "在线搜歌并播放。用户说「播放/听一下/来一首XXX」时调用，会自动停止当前歌。",
                search_props,
                search_and_play_wrapper,
            )
        )
        logger.debug("[MusicManager] 注册搜索播放工具成功")

    def _register_pause_tool(self, add_tool, PropertyList):
        """
        注册暂停工具.
        """

        async def pause_wrapper(args: Dict[str, Any]) -> str:
            result = await self._music_player.pause()
            return result.get("message", "已暂停")

        add_tool(
            (
                "music_player.pause",
                "暂停当前音乐（保持位置）。仅在用户明确说「暂停/先停一下」时调用；"
                "TTS 说话期间会自动让位，不要主动调用。",
                PropertyList(),
                pause_wrapper,
            )
        )
        logger.debug("[MusicManager] 注册暂停工具成功")

    def _register_resume_tool(self, add_tool, PropertyList):
        """
        注册恢复工具.
        """

        async def resume_wrapper(args: Dict[str, Any]) -> str:
            result = await self._music_player.resume()
            return result.get("message", "已恢复播放")

        add_tool(
            (
                "music_player.resume",
                "恢复用户主动暂停的音乐。TTS 临时打断的会自动恢复，不要重复调用。",
                PropertyList(),
                resume_wrapper,
            )
        )
        logger.debug("[MusicManager] 注册恢复工具成功")

    def _register_stop_tool(self, add_tool, PropertyList):
        """
        注册停止工具.
        """

        async def stop_wrapper(args: Dict[str, Any]) -> str:
            result = await self._music_player.stop()
            return result.get("message", "停止播放完成")

        add_tool(
            (
                "music_player.stop",
                "完全停止音乐（重置到开头）。用户说「停止/关掉/别放了」时调用。pause 只是临时暂停。",
                PropertyList(),
                stop_wrapper,
            )
        )
        logger.debug("[MusicManager] 注册停止工具成功")

    def _register_seek_tool(self, add_tool, PropertyList, Property, PropertyType):
        """
        注册跳转工具.
        """

        async def seek_wrapper(args: Dict[str, Any]) -> str:
            position = args.get("position", 0)
            result = await self._music_player.seek(float(position))
            return result.get("message", "跳转完成")

        seek_props = PropertyList(
            [Property("position", PropertyType.INTEGER, min_value=0)]
        )

        add_tool(
            (
                "music_player.seek",
                "跳转到歌曲的指定位置。position 参数单位为秒（从开头计算）。"
                "用于用户要求'快进到2分钟'、'跳到副歌部分'、'回到开头'、'跳转30%'、'跳到30秒'等场景。"
                "注意：如果用户说'快进30秒'，需要先获取当前位置，再加上30秒。",
                seek_props,
                seek_wrapper,
            )
        )
        logger.debug("[MusicManager] 注册跳转工具成功")

    def _register_get_lyrics_tool(self, add_tool, PropertyList):
        """
        注册获取歌词工具.
        """

        async def get_lyrics_wrapper(args: Dict[str, Any]) -> str:
            result = await self._music_player.get_lyrics()
            if result.get("status") == "success":
                lyrics = result.get("lyrics", [])
                return "歌词内容:\n" + "\n".join(lyrics)
            else:
                return result.get("message", "获取歌词失败")

        add_tool(
            (
                "music_player.get_lyrics",
                "获取当前播放歌曲的歌词。返回完整歌词及时间戳。"
                "用于用户询问'这首歌的歌词是什么'、'帮我看看歌词'、'歌词里唱的什么'等场景。",
                PropertyList(),
                get_lyrics_wrapper,
            )
        )
        logger.debug("[MusicManager] 注册获取歌词工具成功")

    def _register_get_local_playlist_tool(
        self, add_tool, PropertyList, Property, PropertyType
    ):
        """
        注册获取本地歌单工具.
        """

        async def get_local_playlist_wrapper(args: Dict[str, Any]) -> str:
            force_refresh = args.get("force_refresh", False)
            result = await self._music_player.get_local_playlist(force_refresh)

            if result.get("status") == "success":
                playlist = result.get("playlist", [])
                total_count = result.get("total_count", 0)

                if playlist:
                    playlist_text = f"本地音乐歌单 (共{total_count}首):\n"
                    playlist_text += "\n".join(playlist)
                    return playlist_text
                else:
                    return "本地缓存中没有音乐文件"
            else:
                return result.get("message", "获取本地歌单失败")

        refresh_props = PropertyList(
            [Property("force_refresh", PropertyType.BOOLEAN, default_value=False)]
        )

        add_tool(
            (
                "music_player.get_local_playlist",
                "获取本地音乐歌单。显示所有已下载并缓存的歌曲。"
                "返回格式：'歌名 - 歌手'，例如'菊花台 - 周杰伦'。"
                "用于用户询问'我有哪些歌'、'本地歌曲列表'、'缓存了什么音乐'等场景。"
                "注意：播放列表中的歌曲时，只需使用歌名调用 search_and_play，"
                "例如列表显示'菊花台 - 周杰伦'，调用 search_and_play(song_name='菊花台') 即可。",
                refresh_props,
                get_local_playlist_wrapper,
            )
        )
        logger.debug("[MusicManager] 注册获取本地歌单工具成功")

    def _format_time(self, seconds: float) -> str:
        """
        将秒数格式化为 mm:ss 格式.
        """
        minutes = int(seconds) // 60
        seconds = int(seconds) % 60
        return f"{minutes:02d}:{seconds:02d}"

    def is_initialized(self) -> bool:
        """
        检查管理器是否已初始化.
        """
        return self._initialized


# 全局管理器实例
_music_tools_manager = None


def get_music_tools_manager() -> MusicToolsManager:
    """
    获取音乐工具管理器单例.
    """
    global _music_tools_manager
    if _music_tools_manager is None:
        _music_tools_manager = MusicToolsManager()
        logger.debug("[MusicManager] 创建音乐工具管理器实例")
    return _music_tools_manager
