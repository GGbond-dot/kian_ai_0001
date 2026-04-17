from .tools import drone_takeoff, drone_land, drone_status, query_status, mapping_view


class RobotDispatchManager:
    def init_tools(self, add_tool, PropertyList, Property, PropertyType):
        add_tool((
            "drone.takeoff",
            (
                "无人机起飞指令工具。当操作员说「开始起飞」「起飞」「系统启动」「执行任务」「出发」等指令时调用。"
                "向 ROS2 topic /drone_command 发送 std_msgs/UInt8 指令码 1，持续发布 30 秒，"
                "无人机开发板订阅该 topic 接收指令。"
            ),
            PropertyList([]),
            drone_takeoff,
        ))

        add_tool((
            "drone.land",
            (
                "无人机降落或紧急停止指令工具。当操作员说「降落」「返航」「紧急降落」「停止任务」「回来」等指令时调用。"
                "向 ROS2 topic /drone_command 发送 std_msgs/UInt8 指令码 2（普通降落）或 3（紧急降落）。"
                "参数 emergency：true 表示立即原地降落，false 表示正常返航降落。"
            ),
            PropertyList([
                Property("emergency", PropertyType.STRING, default_value="false"),
            ]),
            drone_land,
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
                "查看建图效果工具。当操作员说「查看建图效果」「看建图」「看地图」"
                "「打开 rviz」「显示地图」「看一下地图」等指令时调用。"
                "本工具会启动 rviz2 并加载 dcl_fast_lio_mid360.rviz 配置，"
                "用于查看 FAST-LIO MID360 的实时建图结果。无参数。"
            ),
            PropertyList([]),
            mapping_view,
        ))


_manager = None


def get_robot_dispatch_manager():
    global _manager
    if _manager is None:
        _manager = RobotDispatchManager()
    return _manager
