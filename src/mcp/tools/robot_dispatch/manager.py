from .tools import (
    drone_takeoff,
    drone_land,
    drone_hover,
    drone_status,
    query_status,
    mapping_view,
    dispatch_selected_goal,
    planner_status,
    vision_get_detection,
    vision_dispatch_place,
)


class RobotDispatchManager:
    def init_tools(self, add_tool, PropertyList, Property, PropertyType):
        add_tool((
            "drone.takeoff",
            (
                "无人机起飞指令工具。当操作员说「起飞」「开始起飞」「系统启动」"
                "「执行任务」「出发」「飞机起飞」等指令时调用。"
                "向 ROS2 topic /drone_command 发送 std_msgs/UInt8 指令码 1。"
            ),
            PropertyList([]),
            drone_takeoff,
        ))

        add_tool((
            "drone.land",
            (
                "无人机降落指令工具。当操作员说「降落」「返航」「回来」"
                "「停止」「停下」等指令时调用。"
                "向 ROS2 topic /drone_command 发送 std_msgs/UInt8 指令码 2。无参数。"
            ),
            PropertyList([]),
            drone_land,
        ))

        add_tool((
            "drone.hover",
            (
                "无人机悬停指令工具。当操作员说「悬停」时调用，"
                "无人机原地保持当前高度。"
                "向 ROS2 topic /drone_command 发送 std_msgs/UInt8 指令码 3。无参数。"
            ),
            PropertyList([]),
            drone_hover,
        ))

        add_tool((
            "drone.status",
            "查询最近的无人机任务记录。",
            PropertyList([]),
            drone_status,
        ))

        add_tool((
            "mapping.view",
            (
                "查看建图效果工具。当操作员说「看地图」「看建图」「显示地图」"
                "「打开地图」等指令时调用。建图已由系统自动推送到平板屏幕，"
                "本工具仅返回提示文案，不启动外部进程。"
            ),
            PropertyList([]),
            mapping_view,
        ))

        add_tool((
            "drone.dispatch_selected_goal",
            (
                "下发已框选目标工具。当操作员在地图上框选好目标后说「去抓取」「去这里」"
                "「执行框选任务」「下发目标」「去拿」等指令时调用。"
                "把 Web 地图已框选的中心点交给终端 A* 全局规划并发布给无人机。"
                "goal_type：0=普通导航 1=抓取 2=放置 3=降落，默认 1（抓取）。"
            ),
            PropertyList([
                Property(
                    "goal_type", PropertyType.INTEGER,
                    default_value=1, min_value=0, max_value=3,
                ),
            ]),
            dispatch_selected_goal,
        ))

        add_tool((
            "drone.planner_status",
            "查询 Kian 全局规划器、地图、里程计和最近一次路径状态。",
            PropertyList([]),
            planner_status,
        ))

        add_tool((
            "vision.get_detection",
            (
                "查询无人机摄像头的最新 YOLO + QR 码检测结果。"
                "返回是否检测到货物、QR 码数据、货物名称、放物坐标(place_x,place_y,place_z)。"
                "用于在到达送物地点后确认货物和放物点。"
            ),
            PropertyList([]),
            vision_get_detection,
        ))

        add_tool((
            "vision.dispatch_place",
            (
                "根据检测到的货物 QR 码下发放物地点。"
                "读取最新检测结果中的放物坐标，调用全局规划器生成路径，"
                "发布 GoalWithType(goal_type=2=place) 给无人机。"
                "必须先调用 vision.get_detection 确认检测结果后再使用。"
            ),
            PropertyList([]),
            vision_dispatch_place,
        ))


_manager = None


def get_robot_dispatch_manager():
    global _manager
    if _manager is None:
        _manager = RobotDispatchManager()
    return _manager
