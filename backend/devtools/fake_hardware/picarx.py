"""가짜 picarx 패키지(개발·시험 전용). 실제 하드웨어를 건드리지 않습니다."""
import fake_state as fs

__version__ = "fake-2.0"


class _Pin:
    def __init__(self):
        self._value = 0.0

    def pulse_width_percent(self, value=None):
        if value is None:
            return self._value
        self._value = float(value)
        fs.update()
        with fs.LOCK:
            fs.state["duty"] = float(value)


class _DirPin:
    def __init__(self):
        self._v = 0

    def value(self, v=None):
        if v is None:
            return self._v
        self._v = int(v)


class Picarx:
    def __init__(self):
        self.motor_speed_pins = [_Pin(), _Pin()]
        self.motor_direction_pins = [_DirPin(), _DirPin()]
        self.cali_speed_value = [0, 0]
        self.cali_dir_value = [1, -1]

    def _drive(self, motion, speed):
        fs.update()
        duty = (int(abs(speed) / 2) + 50) if speed else 0.0
        for pin in self.motor_speed_pins:
            pin._value = duty
        sign = 1 if motion == "forward" else -1
        for pin, cali in zip(self.motor_direction_pins, (1, -1)):
            pin._v = 1 if sign * cali < 0 else 0
        with fs.LOCK:
            fs.state["motion"] = motion if speed else "stopped"
            fs.state["duty"] = duty

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
        fs.update()
        return [round(x) for x in fs.grayscale()]

    def get_cliff_status(self, grayscale):
        return False
