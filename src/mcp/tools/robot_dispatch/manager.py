from .tools import (
    drone_takeoff,
    drone_land,
    drone_hover,
    drone_status,
    query_status,
    mapping_view,
    dispatch_selected_goal,
    planner_status,
    start_delivery,
    vision_get_detection,
    vision_set_camera,
    vision_dispatch_place,
)


class RobotDispatchManager:
    # 多机说明:drone_key 标识无人机(一号机=a0,二号机=b1)。
    # 用户点名某架机时必须传对应 drone_key;未点名留空 → 系统用默认机(a0)。
    # 起飞/降落/悬停:若用户没说几号机,不要自行假设,应反问操作员是哪一架。
    _DRONE_KEY_DESC = (
        "无人机标识:一号机=a0,二号机=b1。用户点名哪架机就传哪个;"
        "留空表示默认机。起降悬停若用户未点名,应先反问是哪一架,不要随意默认。"
    )

    def init_tools(self, add_tool, PropertyList, Property, PropertyType):
        add_tool((
            "drone.takeoff",
            (
                "【底层手动起飞指令】仅在操作员明确要单独让某架机起飞、不配送时使用。"
                "注意:操作员说「起飞/起飞配送/货到了配送」是要启动配送任务,应调用 "
                "drone.start_delivery,不要用本工具。"
                "本工具向对应无人机 command_topic 发送 std_msgs/UInt8 指令码 1,未点名应先反问是哪一架。"
            ),
            PropertyList([
                Property("drone_key", PropertyType.STRING, default_value=""),
            ]),
            drone_takeoff,
        ))

        add_tool((
            "drone.land",
            (
                "无人机降落指令工具。当操作员说「降落」「返航」「回来」"
                "「停止」「停下」等指令时调用。"
                "向对应无人机的 command_topic 发送 std_msgs/UInt8 指令码 2。"
                "多机时用户未点名应先反问是哪一架。"
            ),
            PropertyList([
                Property("drone_key", PropertyType.STRING, default_value=""),
            ]),
            drone_land,
        ))

        add_tool((
            "drone.hover",
            (
                "无人机悬停指令工具。当操作员说「悬停」时调用，"
                "无人机原地保持当前高度。"
                "向对应无人机的 command_topic 发送 std_msgs/UInt8 指令码 3。"
                "多机时用户未点名应先反问是哪一架。"
            ),
            PropertyList([
                Property("drone_key", PropertyType.STRING, default_value=""),
            ]),
            drone_hover,
        ))

        add_tool((
            "drone.status",
            (
                "查询最近的无人机任务记录。"
                "limit 为返回条数（默认 10），任务汇报/复盘时可取更多（如 50）。"
            ),
            PropertyList([
                Property(
                    "limit", PropertyType.INTEGER,
                    default_value=10, min_value=1, max_value=100,
                ),
            ]),
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
                "drone_key 指定哪架机执行(一号机=a0,二号机=b1),留空用默认机。"
            ),
            PropertyList([
                Property(
                    "goal_type", PropertyType.INTEGER,
                    default_value=1, min_value=0, max_value=3,
                ),
                Property("drone_key", PropertyType.STRING, default_value=""),
            ]),
            dispatch_selected_goal,
        ))

        add_tool((
            "drone.start_delivery",
            (
                "启动多机配送编排任务。当操作员在地图上画好抓取框后说「货到了配送」"
                "「货到了起飞配送」「开始配送」「有货了去送」等指令时调用。"
                "编排器自动接管:起飞→飞抓取区→识别播报→抓取→送货→区内还有货则回去再抓,"
                "全部取完返航降落(多循环)。"
                "注意:若操作员还没画抓取框就要求起飞/配送,本工具会返回提醒,不会起飞。"
                "drone_key 指定哪架机执行(一号机=a0,二号机=b1),留空用默认机。"
            ),
            PropertyList([
                Property("drone_key", PropertyType.STRING, default_value=""),
            ]),
            start_delivery,
        ))

        add_tool((
            "drone.planner_status",
            (
                "查询 Kian 全局规划器、地图、里程计和最近一次路径状态。"
                "drone_key 指定查询哪架机(一号机=a0,二号机=b1),留空返回全部无人机状态。"
            ),
            PropertyList([
                Property("drone_key", PropertyType.STRING, default_value=""),
            ]),
            planner_status,
        ))

        add_tool((
            "vision.get_detection",
            (
                "查询无人机摄像头的最新 YOLO + QR 码检测结果。"
                "返回是否检测到货物、QR 码数据、货物名称、放物坐标(place_x,place_y,place_z)。"
                "用于在到达送物地点后确认货物和放物点。"
                "drone_key 指定哪架机(一号机=a0,二号机=b1),留空用默认机。"
            ),
            PropertyList([
                Property("drone_key", PropertyType.STRING, default_value=""),
            ]),
            vision_get_detection,
        ))

        add_tool((
            "vision.set_camera",
            (
                "开/关无人机相机推流工具。当操作员说「打开摄像头」「开启视频」"
                "「开摄像头」时 enable=true；说「关闭摄像头」「关视频」时 enable=false。"
                "打开后无人机开始推流，视觉系统(YOLO+QR)和前端画中画开始工作；"
                "关闭可在平时巡航时节省 CPU。调用 /a/camera/enable (std_srvs/SetBool)。"
            ),
            PropertyList([
                Property("enable", PropertyType.BOOLEAN, default_value=True),
            ]),
            vision_set_camera,
        ))

        add_tool((
            "vision.dispatch_place",
            (
                "根据检测到的货物 QR 码下发放物地点。"
                "读取最新检测结果中的放物坐标，调用全局规划器生成路径，"
                "发布 GoalWithType(goal_type=2=place) 给无人机。"
                "必须先调用 vision.get_detection 确认检测结果后再使用。"
                "drone_key 指定哪架机(一号机=a0,二号机=b1),留空用默认机。"
            ),
            PropertyList([
                Property("drone_key", PropertyType.STRING, default_value=""),
            ]),
            vision_dispatch_place,
        ))


_manager = None


def get_robot_dispatch_manager():
    global _manager
    if _manager is None:
        _manager = RobotDispatchManager()
    return _manager
