from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

ROS_ROOT = Path("/opt/ros")
RUNTIME_READY_FLAG = "ROS2_RUNTIME_READY"
DISTRO_PREFERENCE = ("humble", "jazzy", "iron", "rolling", "foxy", "galactic")
DEFAULT_ROS_HOME = Path("/tmp/aiagent_ros_home")
CUSTOM_FASTDDS_PROFILE_FLAG = "ROS2_USE_CUSTOM_FASTDDS_PROFILE"


def _prepend_path(path_value: str, current_value: str) -> str:
    if not current_value:
        return path_value

    parts = [part for part in current_value.split(os.pathsep) if part]
    if path_value in parts:
        return current_value
    return os.pathsep.join([path_value] + parts)


@dataclass(frozen=True)
class Ros2Installation:
    distro: str
    root: Path
    python_site_packages: Path
    python_executable: str

    @property
    def lib_path(self) -> Path:
        return self.root / "lib"

    @property
    def bin_path(self) -> Path:
        return self.root / "bin"

    @property
    def python_version(self) -> str:
        return self.python_site_packages.parent.name.removeprefix("python")

    def build_env(self, base_env: Optional[dict] = None) -> dict:
        env = dict(base_env or os.environ)
        ros_home = Path(env.get("ROS_HOME", DEFAULT_ROS_HOME))
        ros_log_dir = Path(env.get("ROS_LOG_DIR", ros_home / "log"))
        ros_home.mkdir(parents=True, exist_ok=True)
        ros_log_dir.mkdir(parents=True, exist_ok=True)

        # Default to clean DDS discovery for this project. Users who need a
        # hand-tuned Fast DDS profile can opt back in explicitly.
        if env.get(CUSTOM_FASTDDS_PROFILE_FLAG, "").strip().lower() not in {
            "1",
            "true",
            "yes",
            "on",
        }:
            env.pop("FASTRTPS_DEFAULT_PROFILES_FILE", None)
            env.pop("FASTDDS_DEFAULT_PROFILES_FILE", None)

        env["ROS_DISTRO"] = self.distro
        env["ROS_VERSION"] = "2"
        env["ROS_HOME"] = str(ros_home)
        env["ROS_LOG_DIR"] = str(ros_log_dir)
        env["PYTHONPATH"] = _prepend_path(
            str(self.python_site_packages), env.get("PYTHONPATH", "")
        )
        env["LD_LIBRARY_PATH"] = _prepend_path(
            str(self.lib_path), env.get("LD_LIBRARY_PATH", "")
        )
        env["PATH"] = _prepend_path(str(self.bin_path), env.get("PATH", ""))
        env["AMENT_PREFIX_PATH"] = _prepend_path(
            str(self.root), env.get("AMENT_PREFIX_PATH", "")
        )
        env["CMAKE_PREFIX_PATH"] = _prepend_path(
            str(self.root), env.get("CMAKE_PREFIX_PATH", "")
        )
        env["COLCON_PREFIX_PATH"] = _prepend_path(
            str(self.root), env.get("COLCON_PREFIX_PATH", "")
        )
        return env


def list_installed_distros() -> list[str]:
    if not ROS_ROOT.exists():
        return []
    return sorted(path.name for path in ROS_ROOT.iterdir() if path.is_dir())


def _iter_distro_candidates(preferred_distro: Optional[str]) -> list[str]:
    installed = list_installed_distros()
    ordered: list[str] = []

    for candidate in (
        preferred_distro,
        os.environ.get("ROS_DISTRO"),
        *DISTRO_PREFERENCE,
        *installed,
    ):
        if not candidate or candidate not in installed or candidate in ordered:
            continue
        ordered.append(candidate)

    return ordered


def _find_site_packages(root: Path) -> Optional[Path]:
    candidates = sorted((root / "lib").glob("python*/site-packages"))
    if not candidates:
        return None

    for candidate in candidates:
        if (candidate / "rclpy").exists():
            return candidate
    return candidates[0]


def _find_python_executable(python_version: str) -> str:
    candidate_names = [
        f"python{python_version}",
        f"/usr/bin/python{python_version}",
        f"/bin/python{python_version}",
    ]

    for candidate in candidate_names:
        if os.path.isabs(candidate):
            if os.path.exists(candidate):
                return candidate
            continue

        resolved = shutil.which(candidate)
        if resolved:
            return resolved

    fallback = shutil.which("python3")
    if fallback:
        return fallback
    return sys.executable


def discover_ros_installation(
    preferred_distro: Optional[str] = None,
) -> Optional[Ros2Installation]:
    for distro in _iter_distro_candidates(preferred_distro):
        root = ROS_ROOT / distro
        site_packages = _find_site_packages(root)
        if site_packages is None:
            continue

        python_version = site_packages.parent.name.removeprefix("python")
        python_executable = _find_python_executable(python_version)
        return Ros2Installation(
            distro=distro,
            root=root,
            python_site_packages=site_packages,
            python_executable=python_executable,
        )

    return None


def ensure_ros_runtime(
    preferred_distro: Optional[str] = None, reexec: bool = False
) -> Optional[Ros2Installation]:
    install = discover_ros_installation(preferred_distro)
    if install is None:
        return None

    os.environ.update(install.build_env())

    if not reexec or os.environ.get(RUNTIME_READY_FLAG) == "1":
        return install

    current_version = f"{sys.version_info.major}.{sys.version_info.minor}"
    current_executable = os.path.realpath(sys.executable)
    target_executable = os.path.realpath(install.python_executable)

    if current_version != install.python_version or current_executable != target_executable:
        env = install.build_env()
        env[RUNTIME_READY_FLAG] = "1"
        os.execve(install.python_executable, [install.python_executable] + sys.argv, env)

    return install
