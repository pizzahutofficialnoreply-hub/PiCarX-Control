"""Adapters for the installed SunFounder PiCar-X API.

The adapter only invokes methods confirmed by the deployed device inventory.
"""
import importlib
import importlib.util
import inspect
import sys
import time
from typing import Protocol

MAX_PWM = 100
MAX_STEERING = 30
CAMERA_ANGLE_LIMITS = (-30, 30)  # Conservative application limit; hardware is not probed.
# 주행 중 방향키로 살짝씩 돌리는 카메라 시야 범위(보정값과 별개, 저장하지 않음)
CAMERA_VIEW_LIMITS = {"pan": (-45, 45), "tilt": (-25, 35)}
OUTPUT_MAPPINGS = ("library", "duty")

CONFIRMED_API = (
    "forward", "backward", "stop", "set_motor_speed", "set_dir_servo_angle",
    "set_cam_pan_angle", "set_cam_tilt_angle", "get_distance",
    "get_grayscale_data", "get_cliff_status",
)
MOTOR_API = ("forward", "backward", "stop", "set_motor_speed")
STEERING_API = ("set_dir_servo_angle",)
CAMERA_SERVO_API = ("set_cam_pan_angle", "set_cam_tilt_angle")

class HardwareAdapter(Protocol):
    mode: str
    connected: bool
    connection_error: str | None
    steering_center: int
    pan_center: int
    tilt_center: int
    def stop(self) -> None: ...
    def drive(self, direction: str, speed: int, steering: int, mapping: str = "library", trim: float = 1.0) -> None: ...
    def set_camera_view(self, pan: int, tilt: int) -> None: ...
    def read_battery(self) -> float | None: ...
    def read_grayscale(self) -> list[float] | None: ...
    def read_distance(self) -> float | None: ...
    def drive_experiment(self, mode: str, value: float, direction: str = "forward", steering: float = 0.0) -> None: ...
    def test_steering(self, direction: str, angle: int) -> None: ...
    def set_calibration(self, steering_center: int, pan_center: int, tilt_center: int) -> None: ...
    def close(self) -> None: ...
    def diagnostics(self) -> dict: ...
    def read_sensors(self) -> dict: ...

def _installed_module_info() -> tuple[bool | None, str | None]:
    """Find the package without importing it or initializing any hardware."""
    try:
        spec = importlib.util.find_spec("picarx")
    except (ImportError, ValueError, ModuleNotFoundError):
        return None, None
    return (spec is not None, spec.origin if spec else None)

def _method_map(target, names: tuple[str, ...]) -> dict[str, bool]:
    return {name: callable(getattr(target, name, None)) for name in names}

class MockAdapter:
    """Development adapter; never imports or calls GPIO or motor drivers."""
    mode = "mock"
    connected = True
    connection_error = None
    def __init__(self) -> None:
        self.motion = "stopped"
        self.steering_center = self.pan_center = self.tilt_center = 0
        self.library_installed, self.library_path = _installed_module_info()
        self.last_command: dict | None = None
        self.camera_view = {"pan": 0, "tilt": 0}
    def stop(self) -> None:
        self.motion = "stopped"
        self.last_command = {"direction": "stopped", "at": time.time()}
    def drive(self, direction: str, speed: int, steering: int, mapping: str = "library", trim: float = 1.0) -> None:
        self.motion = direction
        self.last_command = describe_command(direction, "library" if mapping not in OUTPUT_MAPPINGS else mapping, speed, trim, steering, None)
    def drive_experiment(self, mode: str, value: float, direction: str = "forward", steering: float = 0.0, trim: float = 1.0) -> None:
        self.motion = direction
        self.last_command = describe_command(direction, "duty" if mode == "duty" else "library", value, trim, steering, None)
    def set_camera_view(self, pan: int, tilt: int) -> None:
        self.camera_view = {"pan": pan, "tilt": tilt}
    def read_battery(self) -> float | None:
        return None
    def read_grayscale(self) -> list[float] | None:
        return [320.0, 340.0, 315.0]
    def read_distance(self) -> float | None:
        return None
    def test_steering(self, direction: str, angle: int) -> None:
        pass
    def set_calibration(self, steering_center: int, pan_center: int, tilt_center: int) -> None:
        self.steering_center, self.pan_center, self.tilt_center = steering_center, pan_center, tilt_center
    def diagnostics(self) -> dict:
        return {
            "library_installed": self.library_installed,
            "library_path": self.library_path,
            "class_path": None,
            "library_version": None,
            "api_methods": None,
            "api_compatible": None,
            "driver_initialized": None,
            "motor_control_available": None,
            "steering_servo_available": None,
            "camera_servo_api_available": None,
            "sensor_api_available": None,
            "camera_device_available": None,
            "camera_device_note": "Not probed; mock mode does not inspect camera hardware.",
            "python_version": sys.version.split()[0],
            "last_error": None,
        }
    def read_sensors(self) -> dict:
        # 개발 화면 확인용 가상값이며 실제 장치 측정값이 아닙니다.
        return {"mode": "mock", "available": True, "distance_cm": 42.0,
                "grayscale": [320, 340, 315], "cliff_status": False,
                "error": None}
    def close(self) -> None:
        self.stop()

class UnavailableAdapter:
    """Safe hardware-mode placeholder when the driver cannot be imported."""
    mode = "hardware"
    connected = False
    def __init__(self, reason: str, library_installed: bool | None = None, library_path: str | None = None):
        self.connection_error = reason
        self.library_installed, self.library_path = library_installed, library_path
        self.steering_center = self.pan_center = self.tilt_center = 0
    def stop(self) -> None:
        return
    last_command = None
    def drive(self, direction: str, speed: int, steering: int, mapping: str = "library", trim: float = 1.0) -> None:
        raise RuntimeError(f"PiCar-X is unavailable: {self.connection_error}")
    def drive_experiment(self, mode: str, value: float, direction: str = "forward", steering: float = 0.0, trim: float = 1.0) -> None:
        raise RuntimeError(f"PiCar-X is unavailable: {self.connection_error}")
    def set_camera_view(self, pan: int, tilt: int) -> None:
        raise RuntimeError(f"PiCar-X is unavailable: {self.connection_error}")
    def read_battery(self) -> float | None:
        return None
    def read_grayscale(self) -> list[float] | None:
        raise RuntimeError(self.connection_error or "PiCar-X is unavailable.")
    def read_distance(self) -> float | None:
        return None
    def test_steering(self, direction: str, angle: int) -> None:
        raise RuntimeError(f"PiCar-X is unavailable: {self.connection_error}")
    def set_calibration(self, steering_center: int, pan_center: int, tilt_center: int) -> None:
        raise RuntimeError(f"PiCar-X is unavailable: {self.connection_error}")
    def diagnostics(self) -> dict:
        return {
            "library_installed": self.library_installed,
            "library_path": self.library_path,
            "class_path": None,
            "library_version": None,
            "api_methods": None,
            "api_compatible": False,
            "driver_initialized": False,
            "motor_control_available": False,
            "steering_servo_available": False,
            "camera_servo_api_available": None,
            "sensor_api_available": None,
            "camera_device_available": None,
            "camera_device_note": "Unknown; camera device was not probed.",
            "python_version": sys.version.split()[0],
            "last_error": self.connection_error,
        }
    def read_sensors(self) -> dict:
        raise RuntimeError(self.connection_error or "PiCar-X is unavailable.")
    def close(self) -> None:
        return

class PiCarXAdapter:
    """Lazy adapter for the API methods confirmed on the Raspberry Pi."""
    mode = "hardware"

    def __init__(self) -> None:
        self.connected = False
        self.connection_error: str | None = None
        self.driver_initialized = False
        self.api_methods: dict[str, bool] | None = None
        self.api_compatible = False
        self.motor_control_available = False
        self.steering_servo_available = False
        self.camera_servo_api_available = False
        self.sensor_api_available: dict[str, bool] | None = None
        self.library_version: str | None = None
        self.class_path: str | None = None
        self.library_installed, self.library_path = _installed_module_info()
        self.steering_center = self.pan_center = self.tilt_center = 0
        self._car = None
        self.last_command: dict | None = None
        self.camera_view = {"pan": 0, "tilt": 0}
        self._battery_reader = None
        try:
            if not self.library_installed:
                raise ModuleNotFoundError("Python package 'picarx' is not installed in this interpreter.")
            module = importlib.import_module("picarx")
            car_class = getattr(module, "Picarx", None)
            if car_class is None:
                raise RuntimeError("picarx.Picarx class is not exported by the installed package.")
            self.library_path = getattr(module, "__file__", self.library_path)
            self.class_path = inspect.getfile(car_class)
            self.library_version = getattr(module, "__version__", None)
            self.api_methods = _method_map(car_class, CONFIRMED_API)
            self.motor_control_available = all(self.api_methods[name] for name in MOTOR_API)
            self.steering_servo_available = all(self.api_methods[name] for name in STEERING_API)
            self.camera_servo_api_available = all(self.api_methods[name] for name in CAMERA_SERVO_API)
            self.sensor_api_available = {name: self.api_methods[name] for name in ("get_distance", "get_grayscale_data", "get_cliff_status")}
            self.api_compatible = self.motor_control_available and self.steering_servo_available
            if not self.api_compatible:
                missing = [name for name in MOTOR_API + STEERING_API if not self.api_methods[name]]
                raise RuntimeError("Installed Picarx class is missing control methods: " + ", ".join(missing))

            # Construct only after checking the methods. Construction initializes GPIO and servos.
            self._car = car_class()
            self.driver_initialized = True
            self.connected = True
            self.stop()
        except Exception as exc:
            self.connection_error = f"PiCar-X initialization failed: {exc}"
            if self._car is not None:
                try:
                    self._car.stop()
                except Exception:
                    pass
            self.connected = False

    @staticmethod
    def _clamp(value: int, low: int, high: int) -> int:
        return max(low, min(high, int(value)))

    def _require_car(self):
        if not self.connected or self._car is None:
            raise RuntimeError(self.connection_error or "PiCar-X driver is not initialized.")
        return self._car

    def _set_steering(self, angle: int) -> None:
        car = self._require_car()
        # Include app calibration, then constrain the value before calling the servo API.
        car.set_dir_servo_angle(self._clamp(angle + self.steering_center, -MAX_STEERING, MAX_STEERING))

    def stop(self) -> None:
        car = self._require_car()
        car.stop()
        self._set_steering(0)
        self._last_motor_key = None
        self._last_angle = None
        self.last_command = {"direction": "stopped", "at": time.time()}

    def _apply_output(self, direction: str, mapping: str, value: float, steering: float, trim: float,
                      equal_wheels: bool = False) -> None:
        """전진·후진 모두 같은 경로로 모터 출력을 적용합니다(방향만 다름).

        - ``library``: 0~100을 ``forward()/backward()``에 전달. 라이브러리가 ``value/2 + 50``(%)로 바꿉니다.
        - ``duty``   : 방향 핀만 라이브러리로 정한 뒤 좌·우 PWM 핀에 듀티비(%)를 직접 씁니다.
        ``trim``은 방향별 출력 보정 배율(전·후진 속도 차이를 맞출 때)입니다.
        """
        car = self._require_car()
        if direction not in ("forward", "backward"):
            raise ValueError(f"Unsupported direction: {direction}")
        if mapping not in OUTPUT_MAPPINGS:
            raise ValueError(f"Unsupported output mapping: {mapping}")
        output = self._clamp(round(max(0.0, value) * max(0.1, trim)), 0, MAX_PWM)
        move = car.forward if direction == "forward" else car.backward
        angle = self._clamp(round(steering), -MAX_STEERING, MAX_STEERING)
        tl, tr = getattr(self, "wheel_trim", (1.0, 1.0)) or (1.0, 1.0)
        # 좌우 배율은 반올림하지 않고 넘깁니다(라이브러리가 듀티로 바꿀 때 정수화). 1% 단위 미세 조정이 사라지지 않게.
        left = round(min(float(MAX_PWM), output * tl), 2)
        right = round(min(float(MAX_PWM), output * tr), 2)
        motor_key = (direction, mapping, output, equal_wheels, left, right)
        if equal_wheels and motor_key == getattr(self, "_last_motor_key", None):
            # 측정 중에는 같은 출력을 다시 쓰지 않습니다(바퀴 출력 고정). 조향만 바뀌었으면 서보만 움직입니다.
            if angle != getattr(self, "_last_angle", None):
                self._set_steering(angle)
                self._last_angle = angle
                if self.last_command:
                    self.last_command["steering"] = float(angle)
            return
        self._set_steering(angle)
        self._last_angle = angle
        set_speed = getattr(car, "set_motor_speed", None)
        sign = 1 if direction == "forward" else -1
        if mapping == "library" and callable(set_speed):
            # 라이브러리 forward()/backward()와 같은 계산을 직접 하되, 좌·우 바퀴 배율(trim_left/right)을 곱합니다.
            # 모터 1 = 왼쪽(전진 +), 모터 2 = 오른쪽(전진 −).
            l_out, r_out = float(left), float(right)
            if not equal_wheels and angle != 0:
                # 수동 주행 회전: 라이브러리처럼 조향각만큼 한쪽 바퀴 출력을 줄임(최대 30°)
                scale = (100 - min(abs(angle), 30)) / 100.0
                if (angle > 0) == (direction == "forward"):
                    l_out *= scale
                else:
                    r_out *= scale
            pins = getattr(car, "motor_speed_pins", None)
            if (tl != 1.0 or tr != 1.0) and pins and len(pins) >= 2 and all(callable(getattr(p, "pulse_width_percent", None)) for p in pins):
                # 좌우 배율을 쓰면 라이브러리처럼 듀티 = 출력/2 + 50(%)로 계산하되 정수로 자르지 않고 핀에 직접 씁니다
                # (정수화하면 출력 2 단위마다 1%씩만 바뀌어 1~3% 미세 조정이 사라짐). 방향 핀은 라이브러리 호출로 정합니다.
                set_speed(1, sign * max(1.0, l_out))
                set_speed(2, -sign * max(1.0, r_out))
                pins[0].pulse_width_percent(round(l_out / 2 + 50, 2) if l_out > 0 else 0)
                pins[1].pulse_width_percent(round(r_out / 2 + 50, 2) if r_out > 0 else 0)
            else:
                set_speed(1, sign * l_out)
                set_speed(2, -sign * r_out)
        elif mapping == "library":
            move(output)
        else:
            pins = getattr(car, "motor_speed_pins", None)
            if not pins or not all(callable(getattr(pin, "pulse_width_percent", None)) for pin in pins):
                raise RuntimeError("설치된 picarx 라이브러리에서 motor_speed_pins를 찾지 못해 직접 듀티 모드를 쓸 수 없습니다. 라이브러리 PWM 모드를 사용하세요.")
            move(1 if output > 0 else 0)  # 방향 핀과 보정 방향을 라이브러리 방식대로 설정
            pins[0].pulse_width_percent(left)
            pins[1].pulse_width_percent(right)
        self._last_motor_key = motor_key
        self.last_command = describe_command(direction, mapping, value, trim, steering, car)

    def drive(self, direction: str, speed: int, steering: int, mapping: str = "library", trim: float = 1.0) -> None:
        # signed steering (-30..30)는 조이스틱의 연속 입력을 그대로 전달합니다.
        angle = self._clamp(steering, -MAX_STEERING, MAX_STEERING)
        if direction == "left":
            angle, direction = -abs(angle), "forward"
        elif direction == "right":
            angle, direction = abs(angle), "forward"
        if direction not in ("forward", "backward"):
            raise ValueError(f"Unsupported direction: {direction}")
        self._last_motor_key = None  # 수동 주행은 매번 그대로 보냅니다(회전 시 좌우 차등 유지).
        self._apply_output(direction, mapping, self._clamp(speed, 0, MAX_PWM), angle, trim)

    def drive_experiment(self, mode: str, value: float, direction: str = "forward", steering: float = 0.0, trim: float = 1.0) -> None:
        """방지턱 실험용 직진·후진(자동 측정 주행과 복귀). ``pwm`` = 라이브러리, ``duty`` = 직접 듀티."""
        self._apply_output(direction, "duty" if mode == "duty" else "library", value, steering, trim, equal_wheels=True)

    def set_camera_view(self, pan: int, tilt: int) -> None:
        """주행 중 카메라 시야 조절. 모터를 멈추지 않고 저장하지도 않습니다(보정 중심값 기준)."""
        car = self._require_car()
        pan = self._clamp(pan, *CAMERA_VIEW_LIMITS["pan"])
        tilt = self._clamp(tilt, *CAMERA_VIEW_LIMITS["tilt"])
        car.set_cam_pan_angle(self._clamp(pan + self.pan_center, -90, 90))
        car.set_cam_tilt_angle(self._clamp(tilt + self.tilt_center, -35, 65))
        self.camera_view = {"pan": pan, "tilt": tilt}

    def read_battery(self) -> float | None:
        if self._battery_reader is None:
            try:
                from robot_hat.utils import get_battery_voltage  # type: ignore
                self._battery_reader = get_battery_voltage
            except Exception:
                self._battery_reader = False
        if not self._battery_reader:
            return None
        try:
            return round(float(self._battery_reader()), 3)
        except Exception:
            return None

    def read_grayscale(self) -> list[float] | None:
        car = self._require_car()
        if not callable(getattr(car, "get_grayscale_data", None)):
            return None
        return [float(x) for x in car.get_grayscale_data()]

    def read_distance(self) -> float | None:
        """앞쪽 초음파 센서 거리(cm). 실패하면(라이브러리가 -1, -2 등을 돌려줌) None."""
        car = self._require_car()
        if not callable(getattr(car, "get_distance", None)):
            return None
        try:
            d = float(car.get_distance())
        except Exception:
            return None
        return d if d > 0 else None

    def test_steering(self, direction: str, angle: int) -> None:
        car = self._require_car()
        sign = -1 if direction == "left" else 1 if direction == "right" else 0
        car.stop()
        self._set_steering(sign * self._clamp(angle, 0, MAX_STEERING))

    def set_calibration(self, steering_center: int, pan_center: int, tilt_center: int) -> None:
        car = self._require_car()
        self.camera_view = {"pan": 0, "tilt": 0}
        steering = self._clamp(steering_center, -20, 20)
        pan = self._clamp(pan_center, *CAMERA_ANGLE_LIMITS)
        tilt = self._clamp(tilt_center, *CAMERA_ANGLE_LIMITS)
        self.steering_center, self.pan_center, self.tilt_center = steering, pan, tilt
        self._set_steering(0)
        car.set_cam_pan_angle(pan)
        car.set_cam_tilt_angle(tilt)

    def diagnostics(self) -> dict:
        return {
            "library_installed": self.library_installed,
            "library_path": self.library_path,
            "class_path": self.class_path,
            "library_version": self.library_version,
            "api_methods": self.api_methods,
            "api_compatible": self.api_compatible,
            "driver_initialized": self.driver_initialized,
            "motor_control_available": self.motor_control_available,
            "steering_servo_available": self.steering_servo_available,
            "camera_servo_api_available": self.camera_servo_api_available,
            "sensor_api_available": self.sensor_api_available,
            "camera_device_available": None,
            "camera_device_note": "Unknown; the confirmed API controls camera servos but does not probe the camera device.",
            "python_version": sys.version.split()[0],
            "last_error": self.connection_error,
        }

    def read_sensors(self) -> dict:
        car = self._require_car()
        # 라이브러리 호출은 별도 스레드에서 실행해 FastAPI 이벤트 루프를 막지 않습니다.
        methods = self.sensor_api_available or _method_map(car, ("get_distance", "get_grayscale_data", "get_cliff_status"))
        values = {}
        for method, key in (("get_distance", "distance_cm"), ("get_grayscale_data", "grayscale")):
            values[key] = getattr(car, method)() if methods.get(method) else None
        grayscale = values.get("grayscale")
        # 설치된 실제 API의 서명은 get_cliff_status(grayscale)입니다.
        values["cliff_status"] = car.get_cliff_status(grayscale) if methods.get("get_cliff_status") and grayscale is not None else None
        return {"mode": "hardware", "available": any(methods.values()), "api_methods": methods, **values, "error": None}

    def close(self) -> None:
        # Only call a stop method confirmed in the installed API list.
        if self._car is not None:
            self._car.stop()
            self.connected = False


def _read_pins(car) -> dict | None:
    """보정값만 읽습니다. 모터 핀 자체는 읽지 않습니다.

    robot_hat의 ``Pin.value()``는 인자 없이 부르면 핀을 '입력'으로 바꿉니다. 방향 핀이 입력이 되면
    모터 드라이버의 방향 신호가 떠서 바퀴가 앞뒤로 떨립니다(실제로 왼쪽 바퀴 떨림의 원인이었음).
    그래서 핀 값 대신 라이브러리가 계산하는 기대 듀티(``expected_duty_percent``)만 보여 줍니다.
    """
    if car is None:
        return None
    out: dict = {}
    try:
        cali = getattr(car, "cali_speed_value", None)
        if cali is not None:
            out["cali_speed_value"] = list(cali)
        cali_dir = getattr(car, "cali_dir_value", None)
        if cali_dir is not None:
            out["cali_dir_value"] = list(cali_dir)
    except Exception:
        pass
    return out or None


def describe_command(direction: str, mapping: str, value: float, trim: float, steering: float, car) -> dict:
    """화면에 보여 줄 마지막 모터 명령. 전진과 후진의 실제 듀티가 같은지 확인하는 데 씁니다."""
    requested = max(0.0, float(value))
    applied = max(0, min(MAX_PWM, round(requested * max(0.1, trim))))
    expected = applied if mapping == "duty" else (applied // 2 + 50 if applied > 0 else 0)
    return {
        "direction": direction, "mapping": mapping, "requested": round(requested, 1), "trim": round(trim, 3),
        "applied": applied, "expected_duty_percent": expected, "steering": round(float(steering), 1),
        "pins": _read_pins(car), "at": time.time(),
    }
