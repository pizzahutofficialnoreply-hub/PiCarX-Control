"""그레이스케일 기반 라인 추적기 자리표시자. 아직 주행 명령을 생성하지 않습니다."""
from .base import UnavailableController


class LineFollowingController(UnavailableController):
    def __init__(self):
        super().__init__("line_following", "Line Following", ("grayscale",))
