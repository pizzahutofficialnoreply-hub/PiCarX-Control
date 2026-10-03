"""거리 센서 기반 장애물 회피기 자리표시자."""
from .base import UnavailableController


class ObstacleAvoidanceController(UnavailableController):
    def __init__(self):
        super().__init__("obstacle_avoidance", "Obstacle Avoidance", ("distance_cm",))
