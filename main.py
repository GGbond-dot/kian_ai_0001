import argparse
import asyncio
import os
import signal
import sys

# ── Re-exec guard ───────────────────────────────────────────────────────────────
# LD_LIBRARY_PATH must be set BEFORE the process starts (ld.so reads it at startup).
# Setting it via os.environ inside Python does NOT affect already-running dlopen.
# We re-exec the process with the full ROS 2 + local workspace environment so that
# typesupport .so files (drone_task_interfaces etc.) can be loaded by rclpy.
_RUNTIME_SENTINEL = "AIAGENT_ROS_ENV_READY"

if os.environ.get(_RUNTIME_SENTINEL) != "1":
    _project_dir = os.path.dirname(os.path.abspath(__file__))
    _py_ver = f"python{sys.version_info.major}.{sys.version_info.minor}"

    _env = dict(os.environ)
    _env[_RUNTIME_SENTINEL] = "1"
    _env.setdefault("ROS_DOMAIN_ID", "10")
    _env.setdefault("RMW_IMPLEMENTATION", "rmw_fastrtps_cpp")
    _env.pop("CYCLONEDDS_URI", None)

    # ROS 2 Humble system installation
    _ros_distro = "/opt/ros/humble"
    if os.path.isdir(_ros_distro):
        for _key, _sub in [
            ("LD_LIBRARY_PATH", "lib"),
            ("PATH", "bin"),
        ]:
            _path = os.path.join(_ros_distro, _sub)
            if os.path.isdir(_path):
                _prev = _env.get(_key, "")
                _env[_key] = os.pathsep.join([p for p in [_path, _prev] if p])
        for _py_sub in [
            os.path.join("local", "lib", _py_ver, "dist-packages"),
            os.path.join("lib", _py_ver, "dist-packages"),
        ]:
            _py_path = os.path.join(_ros_distro, _py_sub)
            if os.path.isdir(_py_path):
                _prev = _env.get("PYTHONPATH", "")
                _env["PYTHONPATH"] = os.pathsep.join([p for p in [_py_path, _prev] if p])
        _prev = _env.get("AMENT_PREFIX_PATH", "")
        _env["AMENT_PREFIX_PATH"] = os.pathsep.join([p for p in [_ros_distro, _prev] if p])

    # Local ros2_ws/install workspace (drone_task_interfaces etc.)
    _ros2_ws = os.path.join(_project_dir, "ros2_ws", "install")
    if os.path.isdir(_ros2_ws):
        for _entry in os.listdir(_ros2_ws):
            _pkg_root = os.path.join(_ros2_ws, _entry)
            if not os.path.isdir(_pkg_root):
                continue
            # Python dist-packages
            _pkg_py = os.path.join(_pkg_root, "local", "lib", _py_ver, "dist-packages")
            if os.path.isdir(_pkg_py):
                _prev = _env.get("PYTHONPATH", "")
                _env["PYTHONPATH"] = os.pathsep.join([p for p in [_pkg_py, _prev] if p])
            # .so files inside dist-packages/<pkg>/ (typesupport)
            _pkg_so = os.path.join(_pkg_root, "local", "lib", _py_ver, "dist-packages", _entry)
            if os.path.isdir(_pkg_so):
                _prev = _env.get("LD_LIBRARY_PATH", "")
                _env["LD_LIBRARY_PATH"] = os.pathsep.join([p for p in [_pkg_so, _prev] if p])
            # Shared libraries in lib/
            _pkg_lib = os.path.join(_pkg_root, "lib")
            if os.path.isdir(_pkg_lib):
                _prev = _env.get("LD_LIBRARY_PATH", "")
                _env["LD_LIBRARY_PATH"] = os.pathsep.join([p for p in [_pkg_lib, _prev] if p])
        # AMENT_PREFIX_PATH for workspace-level discovery
        _prev = _env.get("AMENT_PREFIX_PATH", "")
        _env["AMENT_PREFIX_PATH"] = os.pathsep.join([p for p in [_ros2_ws, _prev] if p])

    os.execve(sys.executable, [sys.executable] + sys.argv, _env)

# ── End re-exec guard ───────────────────────────────────────────────────────────

# Post-re-exec: inject local workspace Python packages into sys.path
# (PYTHONPATH from the re-exec already covers this, but sys.path insertion is a
# belt-and-suspenders safeguard for subprocess / embedded-interpreter edge cases.)
_ros2_ws_install = os.path.join(os.path.dirname(__file__), "ros2_ws", "install")
if os.path.isdir(_ros2_ws_install):
    for _entry in os.listdir(_ros2_ws_install):
        _pkg_root = os.path.join(_ros2_ws_install, _entry)
        _pkg_python = os.path.join(
            _pkg_root, "local", "lib",
            f"python{sys.version_info.major}.{sys.version_info.minor}", "dist-packages",
        )
        if os.path.isdir(_pkg_python) and _pkg_python not in sys.path:
            sys.path.insert(0, _pkg_python)

from src.application import Application
from src.utils.logging_config import get_logger, setup_logging

logger = get_logger(__name__)


def parse_args():
    """
    解析命令行参数.
    """
    parser = argparse.ArgumentParser(description="aiagent — 本地 Agent 客户端（默认）")
    parser.add_argument(
        "--mode",
        choices=["gui", "cli", "web"],
        default="gui",
        help="运行模式：gui(图形界面) 或 cli(命令行)",
    )
    parser.add_argument(
        "--protocol",
        choices=["mqtt", "websocket", "local"],
        default="local",
        help="通信协议：local（本地 LLM Agent，默认）/ websocket / mqtt",
    )
    parser.add_argument(
        "--skip-activation",
        action="store_true",
        help="跳过激活流程，直接启动应用（仅用于调试）",
    )
    parser.add_argument(
        "--tablet-tts-api-key",
        default="",
        help="平板浏览器直连 DashScope TTS 使用的临时 API key（默认使用 --route1-api-key）",
    )
    parser.add_argument(
        "--route1-api-key",
        default="",
        help="路由1/Tier1 使用的 DashScope API key：qwen-flash、STT、TTS、平板直连 TTS",
    )
    parser.add_argument(
        "--route2-api-key",
        default="",
        help="路由2/Tier2 使用的 DashScope Coding API key：qwen3-coder-next",
    )
    return parser.parse_args()


async def handle_activation(mode: str) -> bool:
    """处理设备激活流程，依赖已有事件循环.

    Args:
        mode: 运行模式，"gui"或"cli"

    Returns:
        bool: 激活是否成功
    """
    try:
        from src.core.system_initializer import SystemInitializer

        logger.info("开始设备激活流程检查...")

        system_initializer = SystemInitializer()
        # 统一使用 SystemInitializer 内的激活处理，GUI/CLI 自适应
        result = await system_initializer.handle_activation_process(mode=mode)
        success = bool(result.get("is_activated", False))
        logger.info(f"激活流程完成，结果: {success}")
        return success
    except Exception as e:
        logger.error(f"激活流程异常: {e}", exc_info=True)
        return False


async def start_app(mode: str, protocol: str, skip_activation: bool) -> int:
    """
    启动应用的统一入口（在已有事件循环中执行）.
    """
    logger.info("启动 aiagent")

    # 处理激活流程
    # local 协议不依赖小智服务器，无需激活
    if protocol == "local":
        logger.info("本地 Agent 模式：跳过设备激活流程")
    elif not skip_activation:
        activation_success = await handle_activation(mode)
        if not activation_success:
            logger.error("设备激活失败，程序退出")
            return 1
    else:
        logger.warning("跳过激活流程（调试模式）")

    # 创建并启动应用程序
    app = Application.get_instance()
    return await app.run(mode=mode, protocol=protocol)


if __name__ == "__main__":
    exit_code = 1
    try:
        args = parse_args()
        setup_logging()
        if args.route1_api_key:
            os.environ["DASHSCOPE_API_KEY"] = args.route1_api_key
            os.environ["DASHSCOPE_TABLET_TTS_API_KEY"] = (
                args.tablet_tts_api_key or args.route1_api_key
            )
            logger.info("已设置本次进程的路由1 API key")
        elif args.tablet_tts_api_key:
            os.environ["DASHSCOPE_TABLET_TTS_API_KEY"] = args.tablet_tts_api_key
            logger.info("已设置本次进程的平板 TTS API key")

        if args.route2_api_key:
            os.environ["DASHSCOPE_CODING_API_KEY"] = args.route2_api_key
            logger.info("已设置本次进程的路由2 API key")

        # 检测Wayland环境并设置Qt平台插件配置
        import os

        is_wayland = (
            os.environ.get("WAYLAND_DISPLAY")
            or os.environ.get("XDG_SESSION_TYPE") == "wayland"
        )

        if args.mode == "gui" and is_wayland:
            # 在Wayland环境下，确保Qt使用正确的平台插件
            if "QT_QPA_PLATFORM" not in os.environ:
                # 优先使用wayland插件，失败则回退到xcb（X11兼容层）
                os.environ["QT_QPA_PLATFORM"] = "wayland;xcb"
                logger.info("Wayland环境：设置QT_QPA_PLATFORM=wayland;xcb")

            # 禁用一些在Wayland下不稳定的Qt特性
            os.environ.setdefault("QT_WAYLAND_DISABLE_WINDOWDECORATION", "1")
            logger.info("Wayland环境检测完成，已应用兼容性配置")

        # 统一设置信号处理：忽略 macOS 上可能出现的 SIGTRAP，避免“trace trap”导致进程退出
        try:
            if hasattr(signal, "SIGINT"):
                # 交由 qasync/Qt 处理 Ctrl+C；保持默认或后续由 GUI 层处理
                pass
            if hasattr(signal, "SIGTERM"):
                # 允许进程收到终止信号时走正常关闭路径
                pass
            if hasattr(signal, "SIGTRAP"):
                signal.signal(signal.SIGTRAP, signal.SIG_IGN)
        except Exception:
            # 某些平台/环境不支持设置这些信号，忽略即可
            pass

        if args.mode == "gui":
            # 在GUI模式下，由main统一创建 QApplication 与 qasync 事件循环
            try:
                import qasync
                from PyQt5.QtWidgets import QApplication
            except ImportError as e:
                logger.error(f"GUI模式需要qasync和PyQt5库: {e}")
                sys.exit(1)

            qt_app = QApplication.instance() or QApplication(sys.argv)

            loop = qasync.QEventLoop(qt_app)
            asyncio.set_event_loop(loop)
            logger.info("已在main中创建qasync事件循环")

            # 确保关闭最后一个窗口不会自动退出应用，避免事件环提前停止
            try:
                qt_app.setQuitOnLastWindowClosed(False)
            except Exception:
                pass

            with loop:
                exit_code = loop.run_until_complete(
                    start_app(args.mode, args.protocol, args.skip_activation)
                )
        else:
            # CLI模式使用标准asyncio事件循环
            exit_code = asyncio.run(
                start_app(args.mode, args.protocol, args.skip_activation)
            )

    except KeyboardInterrupt:
        logger.info("程序被用户中断")
        exit_code = 0
    except Exception as e:
        logger.error(f"程序异常退出: {e}", exc_info=True)
        exit_code = 1
    finally:
        sys.exit(exit_code)
