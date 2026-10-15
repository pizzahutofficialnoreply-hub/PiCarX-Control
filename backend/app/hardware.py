"""Adapters for the installed SunFounder PiCar-X API.

The adapter only invokes methods confirmed by the deployed device inventory.
"""
import importlib
import importlib.util
import inspect
import sys
from typing import Protocol

MAX_PWM = 60
MAX_STEERING = 30
CAMERA_ANGLE_LIMITS = (-30, 30)  # Conservative application limit; hardware is not probed.

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
    def drive(self, direction: str, speed: int, steering: int) -> None: ...
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
    def stop(self) -> None:
        self.motion = "stopped"
    def drive(self, direction: str, speed: int, steering: int) -> None:
        self.motion = direction
    def drive_experiment(self, mode: str, value: float, direction: str = "forward", steering: float = 0.0) -> None:
        self.motion = direction
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
    def drive(self, direction: str, speed: int, steering: int) -> None:
        raise RuntimeError(f"PiCar-X is unavailable: {self.connection_error}")
    def drive_experiment(self, mode: str, value: float, direction: str = "forward", steering: float = 0.0) -> None:
        raise RuntimeError(f"PiCar-X is unavailable: {self.connection_error}")
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

    def drive(self, direction: str, speed: int, steering: int) -> None:
        car = self._require_car()
        pwm = self._clamp(speed, 0, MAX_PWM)
        # signed steering (-30..30)는 조이스틱의 연속 입력을 그대로 전달합니다.
        angle = self._clamp(steering, -MAX_STEERING, MAX_STEERING)
        if direction in ("left", "right", "forward"):
            if direction == "left": angle = -abs(angle)
            if direction == "right": angle = abs(angle)
            self._set_steering(angle)
            car.forward(pwm)
        elif direction == "backward":
            self._set_steering(angle)
            car.backward(pwm)
        else:
            raise ValueError(f"Unsupported direction: {direction}")

    def drive_experiment(self, mode: str, value: float, direction: str = "forward", steering: float = 0.0) -> None:
        """방지턱 실험용 직진·후진(자동 측정 주행과 복귀).

        - ``pwm``  : 라이브러리 값 0~100을 그대로 ``forward()``에 전달합니다.
          라이브러리가 내부에서 ``speed/2 + 50``으로 바꾸므로 실제 듀티비는 약 50~100%입니다.
        - ``duty`` : 방향만 ``forward(1)``로 설정한 뒤 좌·우 뒷바퀴 PWM 핀에
          듀티비(%)를 직접 지정합니다. 저속 구간까지 써서 속도 범위를 넓힙니다.
          듀티가 너무 낮으면 모터가 출발하지 못할 수 있습니다.
        """
        car = self._require_car()
        output = self._clamp(round(value), 0, 100)
        if direction not in ("forward", "backward"):
            raise ValueError(f"Unsupported experiment direction: {direction}")
        move = car.forward if direction == "forward" else car.backward
        # 자이로 직진 보정값(작은 각도)을 앱 조향 중심값과 함께 적용합니다.
        self._set_steering(self._clamp(round(steering), -MAX_STEERING, MAX_STEERING))
        if mode == "pwm":
            move(output)
            return
        if mode != "duty":
            raise ValueError(f"Unsupported experiment output mode: {mode}")
        pins = getattr(car, "motor_speed_pins", None)
        if not pins or not all(callable(getattr(pin, "pulse_width_percent", None)) for pin in pins):
            raise RuntimeError("설치된 picarx 라이브러리에서 motor_speed_pins를 찾지 못해 직접 듀티 모드를 쓸 수 없습니다. PWM 모드를 사용하세요.")
        move(1)  # 방향 핀과 보정 방향을 라이브러리 방식대로 설정
        for pin in pins:
            pin.pulse_width_percent(output)

    def test_steering(self, direction: str, angle: int) -> None:
        car = self._require_car()
        sign = -1 if direction == "left" else 1 if direction == "right" else 0
        car.stop()
        self._set_steering(sign * self._clamp(angle, 0, MAX_STEERING))

    def set_calibration(self, steering_center: int, pan_center: int, tilt_center: int) -> None:
        car = self._require_car()
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
