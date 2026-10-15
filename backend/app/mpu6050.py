"""MPU-6050 high-rate acceleration logger for PiCar-X impact experiments.

방지턱 충돌 피크는 수 ms 동안만 지속되므로, 기존 100 Hz 대신 약 1 kHz를
목표로 읽습니다(14바이트 일괄 읽기, DLPF 약 184 Hz, ±8 g).

- 내부 표본은 가벼운 튜플 ``(t_mono, ax, ay, az, gx, gy, gz)`` 로 보관합니다.
  60초 기록(약 60,000개)도 메모리 부담 없이 저장할 수 있습니다.
- ``vertical_g`` 는 정지 보정 자세를 기준으로 한 수직 가속도입니다.
  정지 상태에서 1.0 g, 점프(자유낙하)에서 약 0 g, 충돌 순간에 1 g보다 커집니다.
  센서를 뒤집어 붙여도 보정 시 부호를 판별하므로 같은 의미가 됩니다.
- 상태 API 응답은 피크를 잃지 않도록 구간별 최댓값을 유지하는 방식으로
  다운샘플링해 보냅니다.
"""
from __future__ import annotations

import math
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

G = 9.80665
Sample = tuple[float, float, float, float, float, float, float]


def _signed(high: int, low: int) -> int:
    value = (high << 8) | low
    return value - 65536 if value >= 32768 else value


class MPU6050Service:
    ADDRESS = 0x68
    ACCEL_SENSITIVITY = 4096.0  # LSB/g at ±8 g
    GYRO_SENSITIVITY = 65.5     # LSB/(degree/s) at ±500 deg/s
    SAMPLE_INTERVAL = 0.001     # 목표 1 kHz (I²C 속도에 따라 실제값은 더 낮을 수 있음)
    HISTORY_SECONDS = 4.0       # 실시간 그래프용 최근 구간
    MAX_RECORD_SECONDS = 60.0
    PEAK_THRESHOLD_G = 0.15     # 실시간 피크 표시 기준(보정 대비 변화량)
    LIVE_POINTS = 300
    SATURATION_G = 7.5

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
        self._vertical_axis = 2      # 0=x, 1=y, 2=z (보정 시 중력 방향 축)
        self._vertical_sign = 1.0
        self._samples: deque[Sample] = deque(maxlen=int(self.HISTORY_SECONDS / self.SAMPLE_INTERVAL) + 500)
        self._recording = False
        self._record: list[Sample] = []
        self._record_started_mono: float | None = None
        self._record_started_wall: datetime | None = None
        self._peak: Sample | None = None
        self._calibration_stability_g: float | None = None
        # 단조 시간 ↔ 실제 시각 변환 기준
        self._mono0 = time.monotonic()
        self._wall0 = datetime.now(timezone.utc)

    # ------------------------------------------------------------------ I²C
    def _open_bus(self):
        try:
            import smbus2  # type: ignore
            return smbus2.SMBus(1)
        except ImportError:
            import smbus  # type: ignore
            return smbus.SMBus(1)

    def _read_raw(self) -> Sample:
        bus = self._bus
        if bus is None:
            raise RuntimeError("I²C 버스가 초기화되지 않았습니다.")
        with self._i2c_lock:
            d = bus.read_i2c_block_data(self.ADDRESS, 0x3B, 14)
        now = time.monotonic()
        a = self.ACCEL_SENSITIVITY
        gs = self.GYRO_SENSITIVITY
        return (
            now,
            _signed(d[0], d[1]) / a, _signed(d[2], d[3]) / a, _signed(d[4], d[5]) / a,
            _signed(d[8], d[9]) / gs, _signed(d[10], d[11]) / gs, _signed(d[12], d[13]) / gs,
        )

    def start(self) -> bool:
        with self._lock:
            if self._running and self._bus is not None:
                return True
            if self._attempted and self._error:
                return False
            self._attempted = True
            try:
                bus = self._open_bus()
                bus.write_byte_data(self.ADDRESS, 0x6B, 0x00)  # wake
                time.sleep(0.05)
                bus.write_byte_data(self.ADDRESS, 0x1A, 0x01)  # DLPF accel 184 Hz / 1 kHz 내부 표본
                bus.write_byte_data(self.ADDRESS, 0x19, 0x00)  # SMPLRT_DIV 0 → 1 kHz
                bus.write_byte_data(self.ADDRESS, 0x1B, 0x08)  # gyro ±500 deg/s
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

    def _sample_loop(self) -> None:
        next_tick = time.monotonic()
        max_record = int(self.MAX_RECORD_SECONDS / self.SAMPLE_INTERVAL)
        while self._running:
            try:
                sample = self._read_raw()
                with self._lock:
                    self._samples.append(sample)
                    if self._recording:
                        if len(self._record) < max_record:
                            self._record.append(sample)
                            self._update_peak(sample)
                        else:
                            self._recording = False
                            self._error = f"최대 기록 시간 {int(self.MAX_RECORD_SECONDS)}초에 도달해 기록을 멈췄습니다."
            except Exception as exc:
                with self._lock:
                    self._error = f"MPU-6050 읽기 실패: {exc}"
                time.sleep(0.2)
            next_tick += self.SAMPLE_INTERVAL
            delay = next_tick - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                # I²C 읽기가 늦어지면 따라잡으려 하지 않고 가능한 속도로 계속 읽습니다.
                next_tick = time.monotonic()

    # ------------------------------------------------------------ 변환
    def _derived(self, s: Sample) -> dict[str, Any]:
        """튜플 표본을 기존 API 형식의 dict로 변환합니다."""
        _, ax, ay, az, gx, gy, gz = s
        total = math.sqrt(ax * ax + ay * ay + az * az)
        bias = self._bias
        if bias is None:
            dx = dy = dz = dyn = vertical = None
        else:
            dx, dy, dz = ax - bias[0], ay - bias[1], az - bias[2]
            dyn = math.sqrt(dx * dx + dy * dy + dz * dz)
            vertical = 1.0 + self._vertical_sign * (dx, dy, dz)[self._vertical_axis]
        return {
            "timestamp": (self._wall0 + timedelta(seconds=s[0] - self._mono0)).isoformat(),
            "elapsed_s": round(s[0], 5),
            "ax_g": round(ax, 4), "ay_g": round(ay, 4), "az_g": round(az, 4),
            "gx_dps": round(gx, 2), "gy_dps": round(gy, 2), "gz_dps": round(gz, 2),
            "total_g": round(total, 4),
            "delta_x_g": None if dx is None else round(dx, 4),
            "delta_y_g": None if dy is None else round(dy, 4),
            "delta_z_g": None if dz is None else round(dz, 4),
            "dynamic_g": None if dyn is None else round(dyn, 4),
            "dynamic_m_s2": None if dyn is None else round(dyn * G, 3),
            "vertical_g": None if vertical is None else round(vertical, 4),
            "saturated_possible": max(abs(ax), abs(ay), abs(az)) >= self.SATURATION_G,
        }

    def _dynamic(self, s: Sample) -> float:
        bias = self._bias
        if bias is None:
            return 0.0
        return math.sqrt((s[1] - bias[0]) ** 2 + (s[2] - bias[1]) ** 2 + (s[3] - bias[2]) ** 2)

    def _vertical(self, s: Sample) -> float | None:
        bias = self._bias
        if bias is None:
            return None
        return 1.0 + self._vertical_sign * (s[1 + self._vertical_axis] - bias[self._vertical_axis])

    def _update_peak(self, s: Sample) -> None:
        value = self._dynamic(s)
        if value >= self.PEAK_THRESHOLD_G and (self._peak is None or value > self._dynamic(self._peak)):
            self._peak = s

    def _decimate(self, rows: list[Sample], points: int) -> list[Sample]:
        """구간마다 변화량이 가장 큰 표본을 남겨 피크가 사라지지 않게 줄입니다."""
        if len(rows) <= points:
            return rows
        out: list[Sample] = []
        step = len(rows) / points
        for i in range(points):
            chunk = rows[int(i * step):int((i + 1) * step)] or [rows[min(len(rows) - 1, int(i * step))]]
            out.append(max(chunk, key=lambda s: abs((self._vertical(s) or 1.0) - 1.0) + self._dynamic(s)))
        return out

    # ------------------------------------------------------------ 보정
    def calibrate(self) -> dict[str, Any]:
        if not self.start():
            return self.status()
        with self._lock:
            self._recording = False
            self._bias = None
            self._calibration_stability_g = None
            self._record = []
            self._peak = None
            self._error = None
        readings: list[tuple[float, float, float]] = []
        # 차량을 평평한 곳에 정지시킨 상태로 약 0.6초 동안 평균을 냅니다.
        for _ in range(300):
            try:
                s = self._read_raw()
                readings.append((s[1], s[2], s[3]))
            except Exception as exc:
                with self._lock:
                    self._error = f"보정 실패: {exc}"
                return self.status()
            time.sleep(0.002)
        n = len(readings)
        bias = tuple(sum(row[i] for row in readings) / n for i in range(3))
        stability = max(math.sqrt(sum((row[i] - bias[i]) ** 2 for row in readings) / n) for i in range(3))
        if stability > 0.05:
            with self._lock:
                self._error = f"보정 중 센서가 안정적이지 않습니다(표준편차 {stability:.3f} g). 차량을 고정하고 다시 보정하세요."
                self._calibration_stability_g = stability
            return self.status()
        axis = max(range(3), key=lambda i: abs(bias[i]))
        with self._lock:
            self._bias = bias  # type: ignore[assignment]
            self._vertical_axis = axis
            self._vertical_sign = 1.0 if bias[axis] >= 0 else -1.0
            self._calibration_stability_g = stability
            self._samples.clear()
            self._error = None
        return self.status()

    # ------------------------------------------------------------ 기록
    def start_recording(self) -> dict[str, Any]:
        if not self.start():
            return self.status()
        with self._lock:
            if self._bias is None:
                self._error = "먼저 정지 상태 보정을 완료하세요."
                return self.status()
            self._record = []
            self._peak = None
            self._record_started_mono = time.monotonic()
            self._record_started_wall = datetime.now(timezone.utc)
            self._recording = True
            self._error = None
        return self.status()

    def stop_recording(self) -> dict[str, Any]:
        with self._lock:
            self._recording = False
        return self.status()

    def recording_columns(self) -> dict[str, Any]:
        """분석·저장용 기록(열 형식). t_s는 기록 시작 기준 초."""
        with self._lock:
            rows = list(self._record)
            t0 = self._record_started_mono if self._record_started_mono is not None else (rows[0][0] if rows else 0.0)
            started = self._record_started_wall
        cols: dict[str, list[float]] = {k: [] for k in ("t_s", "ax_g", "ay_g", "az_g", "gx_dps", "gy_dps", "gz_dps", "vertical_g", "dynamic_g")}
        for s in rows:
            cols["t_s"].append(round(s[0] - t0, 5))
            cols["ax_g"].append(round(s[1], 4)); cols["ay_g"].append(round(s[2], 4)); cols["az_g"].append(round(s[3], 4))
            cols["gx_dps"].append(round(s[4], 2)); cols["gy_dps"].append(round(s[5], 2)); cols["gz_dps"].append(round(s[6], 2))
            v = self._vertical(s)
            cols["vertical_g"].append(round(v if v is not None else 1.0, 4))
            cols["dynamic_g"].append(round(self._dynamic(s), 4))
        return {
            "started_at": started.isoformat() if started else None,
            "count": len(rows),
            "sample_rate_hz": _rate(rows),
            "vertical_axis": "xyz"[self._vertical_axis],
            "vertical_sign": self._vertical_sign,
            "bias_g": list(self._bias) if self._bias else None,
            "calibration_stability_g": self._calibration_stability_g,
            "columns": cols,
        }

    def recording_data(self) -> dict[str, Any]:
        """기존 API 호환: dict 목록(피크 보존 다운샘플, 최대 2000개)."""
        with self._lock:
            record = list(self._record)
            recording = self._recording
        rows = self._decimate(record, 2000)
        return {
            "recording": recording,
            "count": len(record),
            "samples": [self._derived(s) for s in rows],
        }

    def reset_peak(self) -> dict[str, Any]:
        with self._lock:
            self._peak = None
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
            live = list(self._samples)
            record = list(self._record)
            recording = self._recording
            peak = self._peak
            error = self._error
        # 다운샘플·변환은 잠금 밖에서 수행해 샘플링 스레드를 막지 않습니다.
        latest = self._derived(live[-1]) if live else None
        return {
            "mode": "hardware",
            "available": self._available,
            "calibrated": self._bias is not None,
            "bias_g": list(self._bias) if self._bias is not None else None,
            "vertical_axis": "xyz"[self._vertical_axis] if self._bias is not None else None,
            "latest": latest,
            "samples": [self._derived(s) for s in self._decimate(live, self.LIVE_POINTS)],
            "recording": recording,
            "recorded_samples": [self._derived(s) for s in self._decimate(record, 400)],
            "recorded_sample_count": len(record),
            "recorded_seconds": round(record[-1][0] - record[0][0], 3) if len(record) > 1 else 0.0,
            "actual_sample_rate_hz": _rate(live),
            "calibration_stability_g": self._calibration_stability_g,
            "peak": self._derived(peak) if peak else None,
            "sample_rate_target_hz": int(round(1 / self.SAMPLE_INTERVAL)),
            "range_g": 8,
            "error": error,
        }


def _rate(rows: Iterable[Sample]) -> float | None:
    rows = list(rows)
    if len(rows) < 2:
        return None
    elapsed = rows[-1][0] - rows[0][0]
    return round((len(rows) - 1) / elapsed, 1) if elapsed > 0 else None


mpu6050_service = MPU6050Service()
