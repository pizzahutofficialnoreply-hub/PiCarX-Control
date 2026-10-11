"""MPU-6050 high-rate acceleration logger for PiCar-X impact experiments.

Uses ±8 g accelerometer range and samples in a background thread. The
calibrated delta is relative to the stationary mounting orientation; it is
not a substitute for a laboratory shock sensor.
"""
from __future__ import annotations

import math
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any


class MPU6050Service:
    ADDRESS = 0x68
    ACCEL_SENSITIVITY = 4096.0  # LSB/g at ±8 g
    GYRO_SENSITIVITY = 131.0    # LSB/(degree/s) at ±250 deg/s
    SAMPLE_INTERVAL = 0.01      # target 100 Hz
    HISTORY_SECONDS = 10.0
    PEAK_THRESHOLD_G = 0.15

    def __init__(self) -> None:
        self._bus = None
        self._lock = threading.RLock()
        self._i2c_lock = threading.Lock()
        self._attempted = False
        self._thread: threading.Thread | None = None
        self._running = False
        self._error: str | None = None
        self._available = False
        self._bias: tuple[float, float, float] | None = None
        self._samples: deque[dict[str, Any]] = deque(maxlen=1500)
        self._peak: dict[str, Any] | None = None
        self._last_peak_time = 0.0
        self._last_dynamic_g = 0.0

    @staticmethod
    def _signed_word(bus: Any, register: int) -> int:
        high = bus.read_byte_data(MPU6050Service.ADDRESS, register)
        low = bus.read_byte_data(MPU6050Service.ADDRESS, register + 1)
        value = (high << 8) | low
        return value - 65536 if value >= 32768 else value

    def start(self) -> bool:
        with self._lock:
            if self._running and self._bus is not None:
                return True
            if self._attempted and self._error:
                return False
            self._attempted = True
            try:
                import smbus
                bus = smbus.SMBus(1)
                bus.write_byte_data(self.ADDRESS, 0x6B, 0x00)  # wake
                bus.write_byte_data(self.ADDRESS, 0x1A, 0x03)  # DLPF ~44 Hz
                bus.write_byte_data(self.ADDRESS, 0x19, 0x09)  # ~100 Hz
                bus.write_byte_data(self.ADDRESS, 0x1B, 0x00)  # gyro ±250 deg/s
                bus.write_byte_data(self.ADDRESS, 0x1C, 0x10)  # accel ±8 g
                time.sleep(0.05)
                self._bus = bus
                self._running = True
                self._available = True
                self._error = None
                self._thread = threading.Thread(target=self._sample_loop, daemon=True, name="mpu6050-sampler")
                self._thread.start()
                return True
            except Exception as exc:
                self._bus = None
                self._running = False
                self._available = False
                self._error = f"MPU-6050 초기화 실패: {exc}"
                return False

    def _read_sample(self) -> dict[str, Any]:
        bus = self._bus
        if bus is None:
            raise RuntimeError("I²C 버스가 초기화되지 않았습니다.")
        with self._i2c_lock:
            ax = self._signed_word(bus, 0x3B) / self.ACCEL_SENSITIVITY
            ay = self._signed_word(bus, 0x3D) / self.ACCEL_SENSITIVITY
            az = self._signed_word(bus, 0x3F) / self.ACCEL_SENSITIVITY
            gx = self._signed_word(bus, 0x43) / self.GYRO_SENSITIVITY
            gy = self._signed_word(bus, 0x45) / self.GYRO_SENSITIVITY
            gz = self._signed_word(bus, 0x47) / self.GYRO_SENSITIVITY
        total_g = math.sqrt(ax * ax + ay * ay + az * az)
        with self._lock:
            bias = self._bias
        if bias is None:
            dx = dy = dz = dynamic_g = None
        else:
            dx, dy, dz = ax - bias[0], ay - bias[1], az - bias[2]
            dynamic_g = math.sqrt(dx * dx + dy * dy + dz * dz)
        now = time.monotonic()
        sample = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "elapsed_s": now,
            "ax_g": round(ax, 4), "ay_g": round(ay, 4), "az_g": round(az, 4),
            "gx_dps": round(gx, 2), "gy_dps": round(gy, 2), "gz_dps": round(gz, 2),
            "total_g": round(total_g, 4),
            "delta_x_g": round(dx, 4) if dx is not None else None,
            "delta_y_g": round(dy, 4) if dy is not None else None,
            "delta_z_g": round(dz, 4) if dz is not None else None,
            "dynamic_g": round(dynamic_g, 4) if dynamic_g is not None else None,
            "dynamic_m_s2": round(dynamic_g * 9.80665, 3) if dynamic_g is not None else None,
            "saturated_possible": total_g >= 7.5,
        }
        with self._lock:
            self._last_dynamic_g = dynamic_g or 0.0
            if dynamic_g is not None and dynamic_g >= self.PEAK_THRESHOLD_G:
                # Suppress repeated peak reports within 150 ms; keep the strongest event.
                if self._peak is None or dynamic_g > self._peak.get("dynamic_g", 0):
                    # Keep the strongest detected peak since the last reset/calibration.
                    self._peak = dict(sample)
                    self._last_peak_time = now
            self._samples.append(sample)
            cutoff = now - self.HISTORY_SECONDS
            while self._samples and self._samples[0]["elapsed_s"] < cutoff:
                self._samples.popleft()
        return sample

    def _sample_loop(self) -> None:
        next_tick = time.monotonic()
        while self._running:
            try:
                self._read_sample()
            except Exception as exc:
                with self._lock:
                    self._error = f"MPU-6050 읽기 실패: {exc}"
                time.sleep(0.2)
            next_tick += self.SAMPLE_INTERVAL
            delay = next_tick - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                # Do not try to catch up indefinitely after a delayed I²C read.
                next_tick = time.monotonic()

    def calibrate(self) -> dict[str, Any]:
        if not self.start():
            return self.status()
        readings: list[tuple[float, float, float]] = []
        # User should keep the car stationary on a level surface during this step.
        for _ in range(60):
            try:
                bus = self._bus
                if bus is None:
                    raise RuntimeError("I²C 버스가 초기화되지 않았습니다.")
                with self._i2c_lock:
                    readings.append((
                        self._signed_word(bus, 0x3B) / self.ACCEL_SENSITIVITY,
                        self._signed_word(bus, 0x3D) / self.ACCEL_SENSITIVITY,
                        self._signed_word(bus, 0x3F) / self.ACCEL_SENSITIVITY,
                    ))
            except Exception as exc:
                with self._lock:
                    self._error = f"보정 실패: {exc}"
                return self.status()
            time.sleep(0.01)
        bias = tuple(sum(row[i] for row in readings) / len(readings) for i in range(3))
        with self._lock:
            self._bias = bias
            self._peak = None
            self._samples.clear()
            self._error = None
        return self.status()

    def reset_peak(self) -> dict[str, Any]:
        with self._lock:
            self._peak = None
            self._last_peak_time = 0.0
        return self.status()

    def close(self) -> None:
        with self._lock:
            self._running = False
            thread = self._thread
            bus = self._bus
            self._bus = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=0.5)
        if bus is not None:
            try:
                bus.close()
            except Exception:
                pass

    def status(self) -> dict[str, Any]:
        if not self._running and self._bus is None and not self._error:
            self.start()
        with self._lock:
            samples = list(self._samples)
            latest = dict(samples[-1]) if samples else None
            return {
                "mode": "hardware",
                "available": self._available,
                "calibrated": self._bias is not None,
                "bias_g": list(self._bias) if self._bias is not None else None,
                "latest": latest,
                "samples": samples[-250:],
                "peak": dict(self._peak) if self._peak else None,
                "sample_rate_target_hz": 100,
                "range_g": 8,
                "error": self._error,
            }


mpu6050_service = MPU6050Service()
