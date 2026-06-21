"""
开发板随机屏幕 kiosk 启动器.

`--mode web` 启动时,由 WebDisplay 同步拉起本机浏览器全屏打开 /screen 面板,
免去 SSH 场景下还要接键盘到板子手动开浏览器。

配置 (config.json 顶层 SCREEN_PANEL,键大小写均可):
  enabled     是否启用,默认 false
  browser     浏览器可执行名,默认 firefox(chromium 系也支持,按名字自动选参数)
  url         打开地址,留空则 http://localhost:{port}/screen
  display     X DISPLAY,默认 ":0"(SSH 会话没有该环境变量,必须显式指定)
  extra_args  追加给浏览器的额外参数列表
"""

import asyncio
import os
from typing import Optional

from src.utils.config_manager import ConfigManager
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

# 等服务端口就绪的上限,超时仍未就绪则放弃拉起浏览器
_WAIT_PORT_TIMEOUT_S = 30.0


class ScreenKiosk:
    """管理板上 kiosk 浏览器子进程的生命周期."""

    def __init__(self, port: int):
        section = ConfigManager.get_instance().get_config("SCREEN_PANEL", {}) or {}
        # 项目里各配置段键名大小写不统一(VISION 小写/SYSTEM_OPTIONS 大写),两种都认
        get = lambda key, default: section.get(key.lower(), section.get(key.upper(), default))

        self.enabled: bool = bool(get("enabled", False))
        self.browser: str = str(get("browser", "firefox"))
        self.url: str = str(get("url", "") or f"http://localhost:{port}/screen")
        self.display: str = str(get("display", ":0"))
        extra = get("extra_args", [])
        self.extra_args: list[str] = [str(a) for a in extra] if extra else []

        self._port = port
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        """异步拉起浏览器(等 web 服务端口就绪后再启动,不阻塞调用方)."""
        if not self.enabled:
            logger.info(
                "ScreenKiosk: SCREEN_PANEL.ENABLED 未开启,不拉起屏幕面板"
                "(在 config/config.json 顶层加 SCREEN_PANEL 段)"
            )
            return
        logger.info(
            "ScreenKiosk: 等端口 %s 就绪后拉起 %s → %s",
            self._port, self.browser, self.url,
        )
        self._task = asyncio.get_running_loop().create_task(self._launch())

    async def _launch(self) -> None:
        if not await self._wait_port():
            logger.error(
                "等待 web 端口 %s 就绪超时,放弃拉起 kiosk 浏览器", self._port
            )
            return

        env = dict(os.environ)
        # SSH 会话没有 DISPLAY,指向板上本地屏幕
        env.setdefault("DISPLAY", self.display)

        # 系统级关息屏(X11);Wayland 下无效,靠页面 Wake Lock 兜底
        await self._disable_screen_blank(env)

        cmd = self._build_cmd()
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *cmd,
                env=env,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError:
            logger.error("找不到浏览器 %s,跳过屏幕面板(可在 SCREEN_PANEL.BROWSER 改)", self.browser)
            return
        except Exception as e:
            logger.error("拉起 kiosk 浏览器失败: %s", e)
            return

        logger.info("kiosk 浏览器已启动: %s (pid=%s)", self.url, self._proc.pid)
        code = await self._proc.wait()
        # 正常退出走 stop();此处只在异常退出时留痕,不自动重启(避免崩溃循环)
        if code not in (0, -15):
            logger.warning("kiosk 浏览器异常退出 code=%s,不自动重启", code)
        self._proc = None

    def _build_cmd(self) -> list[str]:
        """按浏览器家族拼 kiosk 启动参数(chromium 系与 firefox 参数不通用)."""
        name = os.path.basename(self.browser).lower()
        if "firefox" in name:
            # 注意:若板上已有 firefox 实例,会并入该实例开新窗口,本子进程立即退出
            return [self.browser, "--kiosk", *self.extra_args, self.url]
        return [
            self.browser,
            "--kiosk",
            "--noerrdialogs",
            "--disable-infobars",
            "--autoplay-policy=no-user-gesture-required",
            # 独立 profile,避免并入板上已有的浏览器实例导致 --kiosk 失效
            "--user-data-dir=/tmp/aiagent_screen_kiosk",
            *self.extra_args,
            self.url,
        ]

    async def _disable_screen_blank(self, env: dict) -> None:
        """关闭 X 屏保与 DPMS,让屏幕常亮(失败不影响后续拉浏览器)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "xset", "s", "off", "s", "noblank", "-dpms",
                env=env,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            code = await proc.wait()
            if code == 0:
                logger.info("已通过 xset 关闭息屏/DPMS,屏幕常亮")
            else:
                logger.warning(
                    "xset 关息屏失败 code=%s(Wayland 会话下属正常,靠页面 Wake Lock)",
                    code,
                )
        except FileNotFoundError:
            logger.warning("未找到 xset,跳过系统级息屏关闭(靠页面 Wake Lock)")
        except Exception as e:
            logger.warning("关闭息屏失败: %s", e)

    async def _wait_port(self) -> bool:
        deadline = asyncio.get_running_loop().time() + _WAIT_PORT_TIMEOUT_S
        while asyncio.get_running_loop().time() < deadline:
            try:
                _, writer = await asyncio.open_connection("127.0.0.1", self._port)
                writer.close()
                await writer.wait_closed()
                return True
            except OSError:
                await asyncio.sleep(0.5)
        return False

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
        proc = self._proc
        self._proc = None
        if proc is None or proc.returncode is not None:
            return
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()
        except ProcessLookupError:
            pass
        logger.info("kiosk 浏览器已关闭")
