"""가짜 picarx 패키지(개발·시험 전용). 실제 하드웨어를 건드리지 않습니다."""
import fake_state as fs

__version__ = "fake-2.0"


class _Pin:
    def __init__(self, side=None):
        self._value = 0.0
        self.side = side

    def pulse_width_percent(self, value=None):
        if value is None:
            return self._value
        self._value = float(value)
        fs.update()
        with fs.LOCK:
            fs.state["duty"] = float(value)
            if self.side:
                fs.state["duty_" + self.side] = float(value)


class _DirPin:
    """실제 robot_hat처럼 인자 없는 value()는 핀을 입력으로 바꾼 것으로 기록합니다(앱이 하면 안 됨)."""
    def __init__(self):
        self._v = 0
        self.input_reads = 0

    def value(self, v=None):
        if v is None:
            self.input_reads += 1
            return self._v
        self._v = int(v)


class Picarx:
    def __init__(self):
        self.motor_speed_pins = [_Pin("l"), _Pin("r")]
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
            fs.state["duty_l"] = fs.state["duty_r"] = duty
            fs.state["sl"] = fs.state["sr"] = sign

    def forward(self, speed):
        self._drive("forward", speed)

    def backward(self, speed):
        self._drive("backward", speed)

    def stop(self):
        self._drive("stopped", 0)

    def set_motor_speed(self, motor, speed):
        """라이브러리와 같이 motor 1(왼쪽)은 +가 전진, motor 2(오른쪽)는 −가 전진."""
        fs.update()
        speed = max(-100, min(100, speed))
        duty = (int(abs(speed) / 2) + 50) if speed else 0.0
        self.motor_speed_pins[motor - 1]._value = duty
        forward = speed > 0 if motor == 1 else speed < 0
        self.motor_direction_pins[motor - 1]._v = (0 if forward else 1) if motor == 1 else (1 if forward else 0)
        with fs.LOCK:
            fs.state["duty_l" if motor == 1 else "duty_r"] = duty
            fs.state["sl" if motor == 1 else "sr"] = 1 if forward else -1
        if motor == 1:
            self._m1 = (forward, duty)
        else:
            m1 = getattr(self, "_m1", (forward, duty))
            with fs.LOCK:
                fs.state["motion"] = ("forward" if m1[0] else "backward") if duty or m1[1] else "stopped"
                fs.state["duty"] = (duty + m1[1]) / 2

    def set_dir_servo_angle(self, angle):
        fs.update()
        with fs.LOCK:
            fs.state["steering"] = float(angle)

    def set_cam_pan_angle(self, angle):
        pass

    def set_cam_tilt_angle(self, angle):
        pass

    def get_distance(self):
        import time as _t
        _t.sleep(0.006)  # 초음파 왕복 시간 흉내
        return fs.wall_distance_cm()

    def get_grayscale_data(self):
        fs.update()
        return [round(x) for x in fs.grayscale()]

    def get_cliff_status(self, grayscale):
        return False
