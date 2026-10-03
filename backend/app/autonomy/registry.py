"""자율주행 모드/기능 표를 한 곳에서 만듭니다."""
from typing import Any
from .autonomous import AutonomousController
from .camera_tracking import CameraTrackingController
from .line_following import LineFollowingController
from .manual import ManualController
from .object_following import ObjectFollowingController
from .obstacle_avoidance import ObstacleAvoidanceController


def controller_catalog() -> list[dict[str, Any]]:
    controllers = [ManualController(), LineFollowingController(), ObstacleAvoidanceController(),
                   ObjectFollowingController(), CameraTrackingController(), AutonomousController()]
    return [{**controller.get_status(), "capabilities": controller.get_capabilities()} for controller in controllers]
