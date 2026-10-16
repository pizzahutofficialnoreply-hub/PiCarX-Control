"""가짜 PiCar-X + MPU-6050 공용 상태(개발·시험 전용).

차가 움직이면 위치를 적분하고, 앞·뒤 축이 방지턱(x = 0.6 m)을 지날 때
수직 가속도 피크를 만듭니다. 바퀴 정렬이 약간 틀어진 것처럼 직진 중
천천히 왼쪽으로 휘도록(약 +4 °/s) 만들어 자이로 직진 보정을 시험할 수 있습니다.
"""
import math
import os
import random
import threading
import time

LOCK = threading.Lock()
WHEELBASE = 0.095
BUMP_X = 0.6
DRIFT_DPS = 4.0
GYRO_BIAS_DPS = 0.8
TAU_S = 0.15  # 모터 속도 응답 시간상수(가속 구간을 흉내)
# 후진이 전진보다 빠른 차를 흉내 내려면 FAKE_BACKWARD_FACTOR=1.3 처럼 지정
BACKWARD_FACTOR = float(os.getenv("FAKE_BACKWARD_FACTOR", "1.0"))
TAPE_OFFSETS = (0.02, 0.0, -0.02)  # 그레이스케일 센서 좌·중·우의 옆 위치(m, 왼쪽 +)

state = {
    "motion": "stopped",   # stopped | forward | backward
    "duty": 0.0,
    "steering": 0.0,
    "x": 0.0,              # 뒷축 위치 (m)
    "y": 0.0,              # 테이프(y=0)에서 옆으로 벗어난 거리 (m)
    "v": 0.0,              # 실제 속도(부호 포함, m/s)
    "accel": 0.0,          # 진행 방향 가속도 (m/s²)
    "heading": 0.0,        # 도, 반시계 +
    "yaw_rate": 0.0,
    "last": time.monotonic(),
    "pulses": [],          # (t, amplitude)
}


def speed_for_duty(duty: float) -> float:
    return max(0.0, 0.95 * (duty - 28.0) / 72.0)


def _crossings(x0: float, x1: float, now: float, v: float) -> None:
    for axle_offset, scale in ((WHEELBASE, 1.0), (0.0, 0.85)):
        a, b = x0 + axle_offset, x1 + axle_offset
        if (a - BUMP_X) * (b - BUMP_X) < 0:
            amp = 0.9 * (v / 0.5) ** 1.6 * scale
            state["pulses"].append((now, amp))


def update(now: float | None = None) -> None:
    now = now or time.monotonic()
    with LOCK:
        dt = now - state["last"]
        state["last"] = now
        if dt <= 0 or dt > 0.5:
            return
        direction = 1 if state["motion"] == "forward" else -1 if state["motion"] == "backward" else 0
        target = direction * speed_for_duty(state["duty"]) * (BACKWARD_FACTOR if direction < 0 else 1.0)
        v_old = state["v"]
        state["v"] += (target - v_old) * (1 - math.exp(-dt / TAU_S))
        state["accel"] = (state["v"] - v_old) / dt
        v = abs(state["v"])
        x0 = state["x"]
        state["x"] += state["v"] * dt
        state["y"] += state["v"] * dt * math.sin(math.radians(state["heading"]))
        if v > 0.01:
            _crossings(x0, state["x"], now, v)
        steer = math.radians(state["steering"])
        # 앞으로 갈 때 오른쪽 조향(+) → 시계 방향(−) 회전, 후진이면 반대
        sgn = 1 if state["v"] > 0 else -1 if state["v"] < 0 else 0
        yaw = -sgn * math.degrees(v / WHEELBASE * math.tan(steer)) + (DRIFT_DPS * sgn if v > 0.01 else 0.0)
        state["yaw_rate"] = yaw
        state["heading"] += yaw * dt
        state["pulses"] = [p for p in state["pulses"] if now - p[0] < 0.1]


def vertical_g(now: float) -> float:
    g = 1.0 + random.gauss(0, 0.012)
    if state["motion"] != "stopped":
        g += 0.04 * math.sin(2 * math.pi * 38 * now) + random.gauss(0, 0.02)
    for t0, amp in state["pulses"]:
        d = now - t0
        g += amp * math.exp(-((d - 0.004) / 0.003) ** 2) - 0.35 * amp * math.exp(-((d - 0.016) / 0.005) ** 2)
    return g


def grayscale() -> list[float]:
    """밝은 바닥(약 1200)에 검은 테이프(약 300, 폭 19 mm)가 y = 0을 따라 붙어 있다고 가정."""
    out = []
    for off in TAPE_OFFSETS:
        d = state["y"] + off
        out.append(1200 - 900 * math.exp(-(d / 0.012) ** 2) + random.gauss(0, 8))
    return out
