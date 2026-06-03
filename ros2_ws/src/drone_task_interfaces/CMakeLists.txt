cmake_minimum_required(VERSION 3.8)
project(drone_task_interfaces)

find_package(ament_cmake REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(nav_msgs REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(std_msgs REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/GoalWithType.msg"
  "msg/GlobalPathWithGoal.msg"
  DEPENDENCIES geometry_msgs nav_msgs std_msgs
)

ament_export_dependencies(rosidl_default_runtime)
ament_package()
