import time

import rclpy
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.node import Node

from robot_task_interfaces.action import DispatchOrder


class DispatchActionServer(Node):
    def __init__(self) -> None:
        super().__init__("dispatch_action_server")
        self._action_server = ActionServer(
            self,
            DispatchOrder,
            "dispatch_order",
            execute_callback=self.execute_callback,
            goal_callback=self.goal_callback,
            cancel_callback=self.cancel_callback,
        )
        self.get_logger().info("dispatch_order action server ready")

    def goal_callback(self, goal_request: DispatchOrder.Goal) -> GoalResponse:
        self.get_logger().info(
            (
                "received goal: "
                f"task_id={goal_request.task_id} "
                f"item={goal_request.item} "
                f"quantity={goal_request.quantity} "
                f"route={goal_request.src_location}->{goal_request.dst_location}"
            )
        )
        if goal_request.quantity <= 0:
            self.get_logger().warning("rejecting goal because quantity <= 0")
            return GoalResponse.REJECT
        return GoalResponse.ACCEPT

    def cancel_callback(self, _goal_handle) -> CancelResponse:
        self.get_logger().info("received cancel request")
        return CancelResponse.ACCEPT

    def execute_callback(self, goal_handle):
        goal = goal_handle.request
        feedback = DispatchOrder.Feedback()
        steps = [
            ("已接单", 0.1, f"任务 {goal.task_id} 已进入队列"),
            ("前往货架", 0.35, f"正在前往 {goal.src_location} 取 {goal.item}"),
            ("取货中", 0.6, f"已拿到 {goal.item} x {goal.quantity}"),
            ("配送中", 0.85, f"正在运送到 {goal.dst_location}"),
            ("已送达", 1.0, f"已送达 {goal.dst_location}"),
        ]

        for state, progress, detail in steps:
            if goal_handle.is_cancel_requested:
                goal_handle.canceled()
                result = DispatchOrder.Result()
                result.success = False
                result.task_id = goal.task_id
                result.final_state = "已取消"
                result.detail = "任务在执行过程中被取消"
                self.get_logger().info(f"goal canceled: {goal.task_id}")
                return result

            feedback.current_state = state
            feedback.progress = progress
            feedback.detail = detail
            goal_handle.publish_feedback(feedback)
            self.get_logger().info(
                (
                    "feedback: "
                    f"task_id={goal.task_id} "
                    f"state={state} "
                    f"progress={progress:.2f}"
                )
            )
            time.sleep(1.0)

        goal_handle.succeed()
        result = DispatchOrder.Result()
        result.success = True
        result.task_id = goal.task_id
        result.final_state = "已完成"
        result.detail = f"{goal.item} x {goal.quantity} 已送到 {goal.dst_location}"
        self.get_logger().info(f"goal finished: {goal.task_id}")
        return result


def main(args=None) -> None:
    rclpy.init(args=args)
    node = DispatchActionServer()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
