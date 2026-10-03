"""기존 수동 주행을 공통 컨트롤러 목록에 표시합니다."""
from .base import AutonomyController


class ManualController(AutonomyController):
    name = "manual"
    display_name = "Manual"

    def get_status(self) -> dict:
        return {"mode": self.name, "name": self.display_name, "state": "ready", "available": True,
                "running": False, "reason": None, "sensor_requirements": []}

    def get_capabilities(self) -> dict:
        return {"available": True, "implemented": True, "sensor_requirements": []}
