"""영상 추적은 스트림 입력이 구현되기 전까지 비활성 상태입니다."""
from .base import UnavailableController


class CameraTrackingController(UnavailableController):
    def __init__(self):
        super().__init__("camera_tracking", "Camera Tracking", ("camera_stream", "object_detections"))
