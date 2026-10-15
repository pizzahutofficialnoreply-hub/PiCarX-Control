"""가짜 PiCar-X + MPU-6050 공용 상태(개발·시험 전용).

차가 움직이면 위치를 적분하고, 앞·뒤 축이 방지턱(x = 0.6 m)을 지날 때
수직 가속도 피크를 만듭니다. 바퀴 정렬이 약간 틀어진 것처럼 직진 중
천천히 왼쪽으로 휘도록(약 +4 °/s) 만들어 자이로 직진 보정을 시험할 수 있습니다.
"""
import math
import random
import threading
import time

LOCK = threading.Lock()
WHEELBASE = 0.095
BUMP_X = 0.6
DRIFT_DPS = 4.0
GYRO_BIAS_DPS = 0.8

state = {
    "motion": "stopped",   # stopped | forward | backward
    "duty": 0.0,
    "steering": 0.0,
    "x": 0.0,              # 뒷축 위치 (m)
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
        v = speed_for_duty(state["duty"]) if state["motion"] != "stopped" else 0.0
        direction = 1 if state["motion"] == "forward" else -1 if state["motion"] == "backward" else 0
        x0 = state["x"]
        state["x"] += direction * v * dt
        if v > 0:
            _crossings(x0, state["x"], now, v)
        steer = math.radians(state["steering"])
        # 앞으로 갈 때 오른쪽 조향(+) → 시계 방향(−) 회전, 후진이면 반대
        yaw = -direction * math.degrees(v / WHEELBASE * math.tan(steer)) + (DRIFT_DPS * direction if v > 0 else 0.0)
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
