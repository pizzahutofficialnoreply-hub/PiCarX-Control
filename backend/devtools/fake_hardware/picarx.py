"""가짜 picarx 패키지(개발·시험 전용). 실제 하드웨어를 건드리지 않습니다."""
import fake_state as fs

__version__ = "fake-2.0"


class _Pin:
    def pulse_width_percent(self, value):
        fs.update()
        with fs.LOCK:
            fs.state["duty"] = float(value)


class Picarx:
    def __init__(self):
        self.motor_speed_pins = [_Pin(), _Pin()]

    def _drive(self, motion, speed):
        fs.update()
        with fs.LOCK:
            fs.state["motion"] = motion if speed else "stopped"
            fs.state["duty"] = (int(abs(speed) / 2) + 50) if speed else 0.0

    def forward(self, speed):
        self._drive("forward", speed)

    def backward(self, speed):
        self._drive("backward", speed)

    def stop(self):
        self._drive("stopped", 0)

    def set_motor_speed(self, motor, speed):
        pass

    def set_dir_servo_angle(self, angle):
        fs.update()
        with fs.LOCK:
            fs.state["steering"] = float(angle)

    def set_cam_pan_angle(self, angle):
        pass

    def set_cam_tilt_angle(self, angle):
        pass

    def get_distance(self):
        return round(80 - fs.state["x"] * 100, 1)

    def get_grayscale_data(self):
        return [320, 340, 315]

    def get_cliff_status(self, grayscale):
        return False
