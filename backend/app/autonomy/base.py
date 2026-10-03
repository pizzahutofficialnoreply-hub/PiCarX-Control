"""자율주행 컨트롤러 공통 인터페이스와 안전한 미지원 응답."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MotionCommand:
    """알고리즘 출력은 하드웨어를 직접 조작하지 않고 공통 주행 명령으로 표현합니다."""
    direction: str
    pwm: int
    steering: int
    source: str = "manual"


class AutonomyController(ABC):
    name = "unknown"
    display_name = "Unknown"
    sensor_requirements: tuple[str, ...] = ()

    @abstractmethod
    def get_status(self) -> dict[str, Any]: ...

    def start(self) -> dict[str, Any]:
        return {**self.get_status(), "started": False}

    def stop(self) -> dict[str, Any]:
        return {**self.get_status(), "stopped": True}

    def update(self, sensor_input: dict[str, Any]) -> MotionCommand | None:
        """구현된 제어기만 센서 입력을 명령 후보로 변환합니다."""
        return None

    def get_sensor_requirements(self) -> list[str]:
        return list(self.sensor_requirements)

    def get_capabilities(self) -> dict[str, Any]:
        return {"available": False, "implemented": False, "sensor_requirements": self.get_sensor_requirements()}


class UnavailableController(AutonomyController):
    """기능을 꾸며내지 않고 준비 상태와 필요한 입력만 알립니다."""
    def __init__(self, name: str, display_name: str, requirements: tuple[str, ...]):
        self.name = name
        self.display_name = display_name
        self.sensor_requirements = requirements

    def get_status(self) -> dict[str, Any]:
        return {"mode": self.name, "name": self.display_name, "state": "unavailable", "available": False, "running": False,
                "reason": "controller_not_implemented", "sensor_requirements": self.get_sensor_requirements()}
