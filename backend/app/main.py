"""FastAPI routes and fail-safe control service for PiCar-X."""
import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from .hardware import MockAdapter, PiCarXAdapter, UnavailableAdapter
from .models import AutonomyStart, CalibrationUpdate, DriveCommand, SteeringTest
from .autonomy.registry import controller_catalog
from .services.event_log import read_events, record_event
from .camera import camera_service
from .mpu6050 import mpu6050_service
from .experiment import router as experiment_router, bind_control

WATCHDOG_SECONDS = 0.5
SETTINGS_PATH = Path(__file__).resolve().parent.parent / "data" / "settings.json"

class ControlService:
    """Serialize motor operations and enforce command expiry server-side."""
    def __init__(self, mode: str | None = None):
        selected_mode = (mode or os.getenv("PICARX_MODE", "mock")).lower()
        if selected_mode == "mock":
            self.adapter = MockAdapter()
        elif selected_mode == "hardware":
            try:
                self.adapter = PiCarXAdapter()
            except Exception as exc:
                self.adapter = UnavailableAdapter(str(exc))
        else:
            raise RuntimeError("PICARX_MODE must be 'mock' or 'hardware'.")
        record_event("startup", "Control service initialized", details={"mode": self.adapter.mode, "connected": self.adapter.connected})
        if not self.adapter.connected and self.adapter.connection_error:
            record_event("hardware", "PiCar-X initialization unavailable", level="ERROR", details={"error": self.adapter.connection_error})

        saved = self._load_settings()
        self.steering_center = int(saved.get("steering_center", self.adapter.steering_center))
        self.pan_center = int(saved.get("pan_center", self.adapter.pan_center))
        self.tilt_center = int(saved.get("tilt_center", self.adapter.tilt_center))
        if self.adapter.connected:
            try:
                self.adapter.set_calibration(self.steering_center, self.pan_center, self.tilt_center)
            except Exception as exc:
                try:
                    self.adapter.stop()
                except Exception:
                    pass
                self.adapter.connected = False
                self.adapter.connection_error = f"Initial calibration failed: {exc}"
        self.motion = "stopped"
        self.speed = 0
        self.steering = 0
        self.updated_at: float | None = None
        self.fault: str | None = self.adapter.connection_error
        self.lock = asyncio.Lock()
        self.stop_generation = 0
        if self.adapter.connected:
            try:
                self.stop_now()
            except Exception as exc:
                # Keep status/health APIs alive, but prevent motion after a failed startup stop.
                self.fault = str(exc)
                self.adapter.connected = False
                self.adapter.connection_error = f"Startup stop failed: {exc}"
                record_event("safety", "Startup stop failed", level="ERROR", details={"error": str(exc)})
        else:
            # No initialized device exists to stop; preserve its original diagnostic error.
            self.motion, self.speed, self.steering = "stopped", 0, 0
            self.updated_at = None

    @staticmethod
    def _load_settings() -> dict:
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def _write_settings(self) -> None:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        temp_path = SETTINGS_PATH.with_suffix(".tmp")
        temp_path.write_text(json.dumps({
            "steering_center": self.steering_center,
            "pan_center": self.pan_center,
            "tilt_center": self.tilt_center,
        }, indent=2), encoding="utf-8")
        temp_path.replace(SETTINGS_PATH)

    async def save_calibration(self, update: CalibrationUpdate) -> None:
        self.stop_generation += 1
        async with self.lock:
            self.stop_now()
            await self._save_calibration_locked(update)

    async def _save_calibration_locked(self, update: CalibrationUpdate) -> None:
        new_steering = self.steering_center if update.steering_center is None else update.steering_center
        new_pan = self.pan_center if update.pan_center is None else update.pan_center
        new_tilt = self.tilt_center if update.tilt_center is None else update.tilt_center
        # Apply supported API calls first; only persist after successful hardware operation.
        self.adapter.set_calibration(new_steering, new_pan, new_tilt)
        self.steering_center, self.pan_center, self.tilt_center = new_steering, new_pan, new_tilt
        self._write_settings()

    def stop_now(self) -> None:
        self.motion, self.speed, self.steering = "stopped", 0, 0
        self.updated_at = None
        try:
            self.adapter.stop()
            if self.adapter.connection_error:
                self.fault = self.adapter.connection_error
        except Exception as exc:
            self.fault = str(exc)
            self.adapter.connected = False
            self.adapter.connection_error = f"Motor stop failed: {exc}"
            raise

    async def stop(self, reason: str = "user") -> None:
        # Invalidate older requests before they can acquire the shared hardware lock.
        self.stop_generation += 1
        async with self.lock:
            try:
                self.stop_now()
                record_event("motion", "Stop command confirmed", details={"mode": self.adapter.mode, "reason": reason})
            except Exception as exc:
                record_event("safety", "Stop command failed", level="ERROR", details={"error": str(exc), "reason": reason})
                raise

    async def drive(self, command: DriveCommand) -> None:
        request_generation = self.stop_generation
        async with self.lock:
            if request_generation != self.stop_generation:
                return
            if not self.adapter.connected:
                raise RuntimeError(self.adapter.connection_error or "PiCar-X is not connected.")
            try:
                self.adapter.drive(command.direction, command.speed, command.steering)
                self.motion = command.direction
                self.speed = command.speed
                self.steering = command.steering
                self.updated_at = time.monotonic()
                self.fault = None
            except Exception as exc:
                self.fault = str(exc)
                record_event("motion", "Drive command failed; stop will be attempted", level="ERROR",
                             details={"direction": command.direction, "pwm": command.speed, "steering": command.steering, "error": str(exc)})
                try:
                    self.stop_now()
                except Exception:
                    pass
                raise

    async def experiment_drive(self, mode: str, value: float, generation: int) -> bool:
        """실험용 직진 명령. 자동 측정 주행이 0.1초마다 호출해 watchdog을 갱신합니다.

        정지 명령으로 generation이 바뀌면 즉시 False를 반환해 주행을 끝냅니다.
        """
        async with self.lock:
            if generation != self.stop_generation:
                return False
            if not self.adapter.connected:
                raise RuntimeError(self.adapter.connection_error or "PiCar-X is not connected.")
            try:
                self.adapter.drive_experiment(mode, value)
                self.motion, self.speed, self.steering = "forward", int(round(value)), 0
                self.updated_at = time.monotonic()
                self.fault = None
                return True
            except Exception as exc:
                self.fault = str(exc)
                record_event("experiment", "Experiment drive failed; stop will be attempted", level="ERROR",
                             details={"mode": mode, "value": value, "error": str(exc)})
                try:
                    self.stop_now()
                except Exception:
                    pass
                raise

    async def test_steering(self, test: SteeringTest) -> None:
        self.stop_generation += 1
        async with self.lock:
            if not self.adapter.connected:
                raise RuntimeError(self.adapter.connection_error or "PiCar-X is not connected.")
            self.stop_now()
            self.adapter.test_steering(test.direction, test.angle)
            self.steering = -test.angle if test.direction == "left" else test.angle if test.direction == "right" else 0

    async def watchdog(self) -> None:
        while True:
            await asyncio.sleep(0.1)
            if self.updated_at is not None and time.monotonic() - self.updated_at > WATCHDOG_SECONDS:
                try:
                    await self.stop(reason="watchdog")
                except Exception:
                    record_event("safety", "Watchdog stop failed", level="ERROR")
                    pass

    def status(self) -> dict:
        age = int((time.monotonic() - self.updated_at) * 1000) if self.updated_at is not None else None
        return {
            "mode": self.adapter.mode,
            "connected": self.adapter.connected,
            "connection_error": self.adapter.connection_error,
            "motion": self.motion,
            "speed": self.speed,
            "steering": self.steering,
            "calibration": self.steering_center,
            "pan_calibration": self.pan_center,
            "tilt_calibration": self.tilt_center,
            "last_command_age_ms": age,
            "watchdog_ms": int(WATCHDOG_SECONDS * 1000),
            "fault": self.fault,
        }

    def diagnostics(self) -> dict:
        return {
            "server_connected": True,
            "mode": self.adapter.mode,
            "hardware_connected": self.adapter.connected if self.adapter.mode == "hardware" else None,
            **self.adapter.diagnostics(),
            "last_error": self.fault or self.adapter.connection_error,
        }

    async def sensors(self) -> dict:
        if not self.adapter.connected:
            raise RuntimeError(self.adapter.connection_error or "PiCar-X is not connected.")
        try:
            # 센서 I/O가 멈추더라도 주행 watchdog와 API 이벤트 루프를 막지 않습니다.
            return await asyncio.to_thread(self.adapter.read_sensors)
        except Exception as exc:
            self.fault = f"Sensor read failed: {exc}"
            record_event("sensor", "Sensor read failed", level="ERROR", details={"error": str(exc)})
            raise

    def autonomy_capabilities(self) -> dict:
        diagnostics = self.adapter.diagnostics()
        api_methods = diagnostics.get("api_methods") or {}
        physical = self.adapter.mode == "hardware" and self.adapter.connected
        return {
            "mode": self.adapter.mode,
            "hardware_connected": self.adapter.connected if self.adapter.mode == "hardware" else None,
            "motor": bool(physical and diagnostics.get("motor_control_available")),
            "steering": bool(physical and diagnostics.get("steering_servo_available")),
            "distance_sensor": bool(physical and api_methods.get("get_distance")),
            "grayscale_sensor": bool(physical and api_methods.get("get_grayscale_data")),
            "cliff_sensor": bool(physical and api_methods.get("get_cliff_status")),
            "camera_servo": bool(physical and diagnostics.get("camera_servo_api_available")),
            "camera_stream": False, "speed_sensor": False, "gps": False,
            "line_following": False, "obstacle_avoidance": False,
            "object_following": False, "camera_tracking": False, "ai_autonomy": False,
        }

control: ControlService

@asynccontextmanager
async def lifespan(app: FastAPI):
    global control
    control = ControlService()
    watcher = asyncio.create_task(control.watchdog())
    try:
        yield
    finally:
        watcher.cancel()
        try:
            await watcher
        except asyncio.CancelledError:
            pass
        try:
            await control.stop(reason="server_shutdown")
        finally:
            mpu6050_service.close()
            control.adapter.close()

app = FastAPI(title="PiCar X Control API", version="0.2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(experiment_router)
bind_control(lambda: control)

@app.get("/api/health")
async def health():
    return {"ok": True, "mode": control.adapter.mode, "connected": control.adapter.connected,
            "connection_error": control.adapter.connection_error}

@app.get("/api/status")
async def status():
    return control.status()

@app.get("/api/diagnostics")
async def diagnostics():
    return control.diagnostics()

@app.get("/api/sensors")
async def sensors():
    try:
        return await control.sensors()
    except Exception as exc:
        record_event("sensor", "Sensor API request failed", level="ERROR", details={"error": str(exc)})
        raise HTTPException(503, f"Sensor read failed: {exc}")

@app.post("/api/drive")
async def drive(command: DriveCommand):
    try:
        await control.drive(command)
    except Exception as exc:
        record_event("motion", "Drive API request failed", level="ERROR", details={"error": str(exc)})
        raise HTTPException(503, f"Drive command failed: {exc}")
    return control.status()

@app.post("/api/stop")
async def stop():
    try:
        await control.stop(reason="api_stop")
    except Exception as exc:
        record_event("safety", "Stop API request failed", level="ERROR", details={"error": str(exc)})
        raise HTTPException(503, f"Stop failed: {exc}")
    return control.status()

@app.post("/api/settings/steering-test")
async def steering_test(test: SteeringTest):
    try:
        await control.test_steering(test)
    except Exception as exc:
        record_event("motion", "Steering test failed", level="ERROR", details={"error": str(exc)})
        raise HTTPException(503, f"Steering test failed: {exc}")
    return control.status()

@app.put("/api/settings/calibration")
async def calibration(update: CalibrationUpdate):
    if update.steering_center is None and update.pan_center is None and update.tilt_center is None:
        raise HTTPException(422, "Provide at least one calibration value.")
    try:
        await control.save_calibration(update)
    except Exception as exc:
        record_event("settings", "Calibration update failed", level="ERROR", details={"error": str(exc)})
        raise HTTPException(503, f"Calibration failed: {exc}")
    return {"steering_center": control.steering_center, "pan_center": control.pan_center,
            "tilt_center": control.tilt_center, "applied_to_hardware": control.adapter.mode == "hardware" and control.adapter.connected}

@app.get("/api/logs")
@app.get("/api/autonomy/logs")
async def logs(limit: int = 100):
    return {"events": read_events(limit)}

@app.get("/api/autonomy/modes")
async def autonomy_modes():
    return {"active_mode": "manual", "modes": controller_catalog()}

@app.get("/api/autonomy/status")
async def autonomy_status():
    return {"active_mode": "manual", "running": False, "available": control.adapter.connected,
            "hardware_mode": control.adapter.mode == "hardware", "state": "manual_ready" if control.adapter.connected else "hardware_unavailable"}

@app.get("/api/autonomy/capabilities")
async def autonomy_capabilities():
    return control.autonomy_capabilities()

@app.post("/api/autonomy/start")
async def autonomy_start(command: AutonomyStart):
    if command.mode == "manual":
        return await autonomy_status()
    record_event("autonomy", "Unavailable controller start requested", level="WARNING", details={"mode": command.mode})
    raise HTTPException(409, {"code": "controller_unavailable", "mode": command.mode,
                              "message": "This controller is not implemented or required hardware input is unavailable."})

@app.post("/api/autonomy/stop")
async def autonomy_stop():
    try:
        await control.stop(reason="autonomy_stop")
    except Exception as exc:
        record_event("autonomy", "Autonomy stop failed", level="ERROR", details={"error": str(exc)})
        raise HTTPException(503, f"Autonomy stop failed: {exc}")
    return {"active_mode": "manual", "running": False, "state": "stopped", **control.status()}

@app.websocket("/api/ws")
async def websocket_status(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            await websocket.send_json(control.status())
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=0.2)
            except asyncio.TimeoutError:
                pass
    except WebSocketDisconnect:
        pass
    finally:
        # WebSocket disappearance (phone closes browser/loses Wi-Fi) requests a backend stop.
        try:
            await control.stop(reason="websocket_disconnect")
        except Exception:
            pass


@app.get("/api/impact/status")
async def impact_status():
    if control.adapter.mode != "hardware":
        return {"mode": "mock", "available": False, "calibrated": False, "latest": None,
                "samples": [], "peak": None, "sample_rate_target_hz": 1000, "range_g": 8,
                "error": "시뮬레이션 모드에서는 실제 MPU-6050 측정값을 제공하지 않습니다."}
    return await asyncio.to_thread(mpu6050_service.status)


@app.post("/api/impact/calibrate")
async def impact_calibrate():
    if control.adapter.mode != "hardware" or not control.adapter.connected:
        raise HTTPException(409, "실제 하드웨어 모드에서만 가속도 센서를 보정할 수 있습니다.")
    result = await asyncio.to_thread(mpu6050_service.calibrate)
    if not result.get("available") or not result.get("calibrated"):
        raise HTTPException(503, result.get("error") or "MPU-6050 보정에 실패했습니다.")
    return result


@app.post("/api/impact/start")
async def impact_start():
    if control.adapter.mode != "hardware" or not control.adapter.connected:
        raise HTTPException(409, "실제 하드웨어 모드에서만 측정을 시작할 수 있습니다.")
    result = await asyncio.to_thread(mpu6050_service.start_recording)
    if not result.get("available") or not result.get("calibrated") or result.get("error"):
        raise HTTPException(409, result.get("error") or "MPU-6050 보정 후 측정을 시작하세요.")
    return result


@app.post("/api/impact/stop")
async def impact_stop():
    if control.adapter.mode != "hardware":
        raise HTTPException(409, "시뮬레이션 모드에서는 측정을 정지할 수 없습니다.")
    return await asyncio.to_thread(mpu6050_service.stop_recording)


@app.get("/api/impact/recording-data")
async def impact_recording_data():
    if control.adapter.mode != "hardware":
        raise HTTPException(409, "시뮬레이션 모드에서는 실제 측정 데이터를 내보낼 수 없습니다.")
    return await asyncio.to_thread(mpu6050_service.recording_data)


@app.post("/api/impact/reset-peak")
async def impact_reset_peak():
    if control.adapter.mode != "hardware":
        raise HTTPException(409, "시뮬레이션 모드에서는 피크를 초기화할 수 없습니다.")
    return await asyncio.to_thread(mpu6050_service.reset_peak)

@app.get("/api/camera/status")
def camera_status() -> dict:
    return camera_service.status()


@app.get("/api/camera/stream")
def camera_stream() -> StreamingResponse:
    return StreamingResponse(
        camera_service.stream(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )

# 프론트엔드가 빌드된 경우 같은 FastAPI 프로세스가 UI를 정적으로 제공합니다.
# 개발 중 dist가 없으면 기존 API 전용 실행 흐름을 유지합니다.
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if (FRONTEND_DIST / "index.html").is_file():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
