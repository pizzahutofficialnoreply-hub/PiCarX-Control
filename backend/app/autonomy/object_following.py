"""객체 추적은 현재 카메라 영상 입력이 없어 사용할 수 없습니다."""
from .base import UnavailableController


class ObjectFollowingController(UnavailableController):
    def __init__(self):
        super().__init__("object_following", "Object Following", ("camera_stream", "object_detections"))
