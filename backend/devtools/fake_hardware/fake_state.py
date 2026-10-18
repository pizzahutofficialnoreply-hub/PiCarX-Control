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
# FAKE_BUMP=0 이면 방지턱 없는 평지(평지 오검출 시험용)
HAS_BUMP = os.getenv("FAKE_BUMP", "1") != "0"
DRIFT_DPS = 4.0
GYRO_BIAS_DPS = 0.8
TAU_S = 0.15  # 모터 속도 응답 시간상수(가속 구간을 흉내)
# 후진이 전진보다 빠른 차를 흉내 내려면 FAKE_BACKWARD_FACTOR=1.3 처럼 지정
BACKWARD_FACTOR = float(os.getenv("FAKE_BACKWARD_FACTOR", "1.0"))
# 왼쪽 바퀴가 더 센 차를 흉내 내려면 FAKE_LEFT_FACTOR=1.08 처럼 지정(좌우 균형 점검 시험)
LEFT_FACTOR = float(os.getenv("FAKE_LEFT_FACTOR", "1.0"))
TRACK = 0.11
# 초음파 시험용 벽 위치(뒷축 기준 x, m). 앞범퍼는 앞축보다 약 3 cm 앞
WALL_X = float(os.getenv("FAKE_WALL_X", "1.6"))
BUMPER = 0.03
# 코스 시험용 가로 테이프(출발선·정지선) 위치: 그레이스케일 센서(앞축보다 4 cm 앞)의 x
SENSOR_AHEAD = WHEELBASE + 0.04
START_X = float(os.getenv("FAKE_START_X", str(round(SENSOR_AHEAD, 3))))
STOP_X = float(os.getenv("FAKE_STOP_X", "1.15"))
CROSS_HALF_LEN = 0.07
TAPE_OFFSETS = (0.02, 0.0, -0.02)  # 그레이스케일 센서 좌·중·우의 옆 위치(m, 왼쪽 +)

state = {
    "motion": "stopped",   # stopped | forward | backward
    "duty": 0.0,
    "duty_l": 0.0,
    "duty_r": 0.0,
    "steering": 0.0,
    "x": 0.0,              # 뒷축 위치 (m)
    "y": 0.0,              # 테이프(y=0)에서 옆으로 벗어난 거리 (m)
    "v": 0.0,              # 실제 속도(부호 포함, m/s)
    "accel": 0.0,          # 진행 방향 가속도 (m/s²)
    "heading": 0.0,        # 도, 반시계 +
    "yaw_rate": 0.0,
    "last": time.monotonic(),
    "pulses": [],          # (t, amplitude)
    "sl": 1, "sr": 1,      # 좌우 바퀴 방향(+1 전진, −1 후진) – 제자리 회전 시험
}


def speed_for_duty(duty: float) -> float:
    return max(0.0, 0.95 * (duty - 28.0) / 72.0)


BUMP_R, BUMP_H, WHEEL_R = 0.023, 0.010, 0.025   # 반원형 방지턱(폭 38 mm, 높이 10 mm)과 바퀴 반지름
BUMP_X0 = math.sqrt((BUMP_R + WHEEL_R) ** 2 - (BUMP_R - BUMP_H + WHEEL_R) ** 2)  # 바퀴 중심이 턱에 닿는 거리(±)


def wheel_height(dx: float) -> float:
    """바퀴 중심이 방지턱 가운데에서 dx만큼 떨어져 있을 때 들린 높이(m)."""
    if not HAS_BUMP or abs(dx) >= BUMP_X0:
        return 0.0
    return -(BUMP_R - BUMP_H) + math.sqrt((BUMP_R + WHEEL_R) ** 2 - dx * dx) - WHEEL_R


def _crossings(x0: float, x1: float, now: float, v: float, c: float = 1.0) -> None:
    """앞·뒤 바퀴가 방지턱 가장자리(오를 때·내려올 때)를 지나면 충격 펄스를 만듭니다."""
    for axle_offset, scale in ((WHEELBASE, 1.0), (0.0, 0.85)):
        a, b = x0 + axle_offset * c, x1 + axle_offset * c
        for edge in (BUMP_X - BUMP_X0, BUMP_X + BUMP_X0):
            if (a - edge) * (b - edge) < 0:
                up = abs(b - BUMP_X) < abs(a - BUMP_X)
                amp = (0.9 if up else 0.6) * (v / 0.5) ** 1.6 * scale
                state["pulses"].append((now, amp))
                if up:
                    state["v"] *= 0.93  # 턱을 오르며 잠깐 느려짐


def update(now: float | None = None) -> None:
    now = now or time.monotonic()
    with LOCK:
        dt = now - state["last"]
        state["last"] = now
        if dt <= 0 or dt > 0.5:
            return
        spinning = state["motion"] != "stopped" and state.get("sl", 1) != state.get("sr", 1)
        direction = 1 if state["motion"] == "forward" else -1 if state["motion"] == "backward" else 0
        target = 0.0 if spinning else direction * speed_for_duty(state["duty"]) * (BACKWARD_FACTOR if direction < 0 else 1.0)
        v_old = state["v"]
        state["v"] += (target - v_old) * (1 - math.exp(-dt / TAU_S))
        state["accel"] = (state["v"] - v_old) / dt
        v = abs(state["v"])
        x0 = state["x"]
        hr = math.radians(state["heading"])
        state["x"] += state["v"] * dt * math.cos(hr)
        state["y"] += state["v"] * dt * math.sin(hr)
        if v > 0.01 and HAS_BUMP:
            _crossings(x0, state["x"], now, v, math.cos(hr))
        steer = math.radians(state["steering"])
        # 앞으로 갈 때 오른쪽 조향(+) → 시계 방향(−) 회전, 후진이면 반대
        sgn = 1 if state["v"] > 0 else -1 if state["v"] < 0 else 0
        yaw = -sgn * math.degrees(v / WHEELBASE * math.tan(steer)) + (DRIFT_DPS * sgn if v > 0.01 else 0.0)
        if v > 0.01:
            # 좌우 바퀴 속도 차이 → 회전(오른쪽이 빠르면 왼쪽으로 돎, 반시계 +)
            vl = speed_for_duty(state["duty_l"]) * LEFT_FACTOR
            vr = speed_for_duty(state["duty_r"])
            yaw += sgn * math.degrees((vr - vl) / TRACK)
        if spinning:
            # 좌우 바퀴가 반대로 돌면 제자리 회전(뒷축 가운데 기준). 반시계(+) = 오른쪽 바퀴 전진.
            w = speed_for_duty(state["duty_r"]) * state["sr"] - speed_for_duty(state["duty_l"]) * state["sl"]
            yaw = math.degrees(w / TRACK) * 0.22  # 앞바퀴가 미끄러지며 버텨 실제보다 느리게 돎(약 150 °/s)
        state["yaw_rate"] = yaw
        state["heading"] += yaw * dt
        # 앞뒤 기울기(피치): 앞·뒤 바퀴가 들린 높이 차이로 계산(앞이 들리면 +)
        cx = math.cos(math.radians(state["heading"]))
        hf = wheel_height(state["x"] + WHEELBASE * cx - BUMP_X)
        hrr = wheel_height(state["x"] - BUMP_X)
        th = math.degrees(math.atan2(hf - hrr, WHEELBASE)) * (1 if cx >= 0 else -1)
        state["pitch_rate"] = (th - state.get("pitch", 0.0)) / dt
        state["pitch"] = th
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
    """밝은 바닥(약 1200)에 검은 테이프(약 300, 폭 19 mm)가 y = 0을 따라(유도선), x = START_X·STOP_X에 가로로 붙어 있다고 가정."""
    out = []
    h = math.radians(state["heading"])
    sx = state["x"] + SENSOR_AHEAD * math.cos(h)
    sy = state["y"] + SENSOR_AHEAD * math.sin(h)
    for off in TAPE_OFFSETS:
        px, py = sx - off * math.sin(h), sy + off * math.cos(h)
        guide = math.exp(-(py / 0.012) ** 2) if -0.3 <= px <= 1.5 else 0.0
        cross = 0.0
        for cx in (START_X, STOP_X):
            if abs(py) <= CROSS_HALF_LEN:
                cross = max(cross, math.exp(-((px - cx) / 0.011) ** 2))
        out.append(1200 - 900 * max(guide, cross) + random.gauss(0, 8))
    if os.getenv("FAKE_TRACE") and (max(out) < 750 or os.getenv("FAKE_TRACE") == "all"):
        import sys
        print(f"TRACE dark3 x={state['x']:.3f} y={state['y']:.3f} h={state['heading']:.1f} sx={sx:.3f} sy={sy:.3f}", file=sys.stderr)
    return out


def wall_distance_cm() -> float:
    """앞범퍼에서 벽까지 거리(초음파 흉내, ±0.5 cm 잡음, 가끔 튀는 값)."""
    update()
    d = (WALL_X - (state["x"] + WHEELBASE + BUMPER)) * 100 + random.gauss(0, 0.5)
    if random.random() < 0.02:
        d += random.choice((-25, 40))
    return round(max(2.0, d), 1)
