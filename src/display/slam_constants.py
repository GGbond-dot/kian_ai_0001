"""
SLAM Web Viewer — 配置常量集中点.

所有跟 SLAM 端约定相关、需要跟队友确认的参数都放在这里。
确认后改这一个文件即可，不要散落到业务代码中。
"""

# ===================== ROS Topic 名称 =====================
# TODO(slam-team): 确认 topic 名称是否就是 /a/* 前缀（来自 dcl_fast_lio_mid360.rviz）
SLAM_TOPIC_MAP = "/a/Laser_map"          # PointCloud2 — 累积地图
SLAM_TOPIC_SCAN = "/a/cloud_registered"  # PointCloud2 — 当前帧 registered 点云
SLAM_TOPIC_ODOM = "/a/Odometry"          # nav_msgs/Odometry — 当前位姿
SLAM_TOPIC_PATH = "/a/path"              # nav_msgs/Path — 历史轨迹

# ===================== 坐标系 =====================
# TODO(slam-team): 确认 FAST-LIO 的 fixed_frame 是 camera_init 还是 map
SLAM_FIXED_FRAME = "camera_init"

# ===================== 降采样参数 =====================
SLAM_MAP_VOXEL_SIZE = 0.05   # 累积地图：5cm
SLAM_SCAN_VOXEL_SIZE = 0.10  # 实时 scan：10cm

# ===================== 推送频率 (Hz) =====================
# 实际由 ROS topic 自然驱动；这些是节流上限
SLAM_MAP_MAX_HZ = 1.0
SLAM_SCAN_MAX_HZ = 5.0
SLAM_ODOM_MAX_HZ = 10.0

# ===================== Path 抽稀 =====================
SLAM_PATH_DECIMATE = 10  # 每 N 个 pose 取一个

# ===================== 二进制协议 channel id =====================
CHAN_MAP = 0x01
CHAN_SCAN = 0x02
CHAN_ODOM = 0x03
CHAN_PATH = 0x04

# ===================== Stub 模式 =====================
# 当 rclpy 不可用（PC 本地无 ROS）或显式开启时，推假数据用于前端联调
# 通过环境变量 AIAGENT_SLAM_STUB=1 强制开启
SLAM_STUB_FALLBACK_WHEN_NO_ROS = True
