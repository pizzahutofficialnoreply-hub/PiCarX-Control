"""여러 자율주행 기능을 조합하는 상위 컨트롤러 자리표시자."""
from .base import UnavailableController


class AutonomousController(UnavailableController):
    def __init__(self):
        super().__init__("autonomous", "Autonomous", ("sensors", "safety_layer"))
