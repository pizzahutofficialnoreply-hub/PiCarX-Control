"""PiCar-X camera streaming service using Picamera2."""

import io
import threading
import time
from typing import Generator, Optional


class CameraService:
    """Manages the Raspberry Pi camera independently from vehicle control."""

    def __init__(self) -> None:
        self._camera = None
        self._lock = threading.Lock()
        self._started = False
        self._available: Optional[bool] = None
        self._error: Optional[str] = None

    def start(self) -> bool:
        """Start the camera if it is available."""
        with self._lock:
            if self._started and self._camera is not None:
                return True

            try:
                from picamera2 import Picamera2

                camera = Picamera2()

                config = camera.create_video_configuration(
                    main={
                        "size": (640, 480),
                        "format": "RGB888",
                    },
                    buffer_count=4,
                )

                camera.configure(config)
                camera.start()

                # Give the sensor a moment to settle.
                time.sleep(0.2)

                self._camera = camera
                self._started = True
                self._available = True
                self._error = None

                return True

            except Exception as exc:
                self._camera = None
                self._started = False
                self._available = False
                self._error = str(exc)

                return False

    def stop(self) -> None:
        """Stop the camera."""
        with self._lock:
            if self._camera is not None:
                try:
                    self._camera.stop()
                except Exception:
                    pass

                try:
                    self._camera.close()
                except Exception:
                    pass

            self._camera = None
            self._started = False

    def capture_jpeg(self) -> Optional[bytes]:
        """Capture one JPEG frame."""
        if not self.start():
            return None

        with self._lock:
            if self._camera is None:
                return None

            try:
                output = io.BytesIO()
                self._camera.capture_file(output, format="jpeg")
                return output.getvalue()

            except Exception as exc:
                self._error = str(exc)
                return None

    def stream(self) -> Generator[bytes, None, None]:
        """Generate an MJPEG stream."""
        while True:
            frame = self.capture_jpeg()

            if frame is None:
                time.sleep(0.5)
                continue

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: "
                + str(len(frame)).encode("ascii")
                + b"\r\n\r\n"
                + frame
                + b"\r\n"
            )

            # Approximately 20 FPS.
            time.sleep(0.05)

    def status(self) -> dict:
        """Return camera status information."""
        return {
            "available": self._available,
            "started": self._started,
            "error": self._error,
            "resolution": [640, 480],
            "format": "MJPEG",
        }


camera_service = CameraService()