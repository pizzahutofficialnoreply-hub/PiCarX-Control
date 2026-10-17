"""초음파 거리 센서(PiCar-X 앞쪽 HC-SR04 계열)로 벽까지의 거리를 기록하고, 거리 변화로 속도를 구합니다.

원리
- 초음파 센서는 소리가 벽에 부딪혀 돌아오는 시간으로 거리를 잽니다(약 2~400 cm, 보통 ±1 cm).
- 주행 중 초당 15~25번 거리를 읽어 두면, 거리-시간 그래프의 기울기가 곧 차의 속력입니다.
  (벽 쪽으로 가면 거리가 줄어들므로 속력 = −기울기)
- 한 점씩 빼면 잡음이 커서(1 cm / 0.05 s = 0.2 m/s), 앞뒤 0.15초 안의 점들에 직선을 맞춰 기울기를 구합니다
  (국소 선형 회귀). 이렇게 하면 속력 오차가 대략 ±0.03 m/s 수준이 됩니다.
- 방지턱을 오르면 차가 잠깐 느려지므로 속력 그래프가 움푹 들어갑니다. 이 '속력 골'을 MPU-6050의
  앞바퀴 충격 시각과 비교하면, 초음파만으로도 방지턱을 찾을 수 있는지 확인할 수 있습니다.
- 차가 방지턱에서 앞뒤로 기울면 초음파가 바닥이나 벽 위쪽에 맞아 거리값이 잠깐 튈 수 있습니다.
  이런 튀는 값은 중앙값 기준(햄펠 필터)으로 걸러 내고, 몇 개를 걸렀는지 기록합니다.

카메라 한 대로는 거리(깊이)를 직접 알 수 없어(크기를 아는 표적이 필요) 여기서는 쓰지 않습니다. PiCar-X에는
라이다가 없고, 앞쪽 센서는 초음파입니다.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Sequence

VALID_MIN_CM = 2.0
VALID_MAX_CM = 400.0


class DistanceLogger:
    """별도 스레드에서 초음파 거리를 일정 간격으로 읽어 (단조 시각, cm)로 쌓습니다."""

    def __init__(self, read: Callable[[], float | None], hz: float = 20.0) -> None:
        self.read = read
        self.period = 1.0 / max(2.0, min(40.0, hz))
        self.samples: list[tuple[float, float | None]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.errors = 0

    def start(self) -> None:
        self._stop.clear()
        self.samples = []
        self._thread = threading.Thread(target=self._loop, name="ultrasonic", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        nxt = time.monotonic()
        while not self._stop.is_set():
            t = time.monotonic()
            try:
                d = self.read()
            except Exception:
                d = None
                self.errors += 1
            if d is not None and not (VALID_MIN_CM <= d <= VALID_MAX_CM):
                d = None
            # 측정 시각은 읽기 시작과 끝의 가운데(초음파 왕복 시간만큼 늦어지는 것 보정)
            self.samples.append(((t + time.monotonic()) / 2, d))
            nxt += self.period
            delay = nxt - time.monotonic()
            if delay > 0:
                self._stop.wait(delay)
            else:
                nxt = time.monotonic()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def latest(self, n: int = 3, max_age_s: float = 0.4) -> float | None:
        """최근 유효값 n개의 중앙값(튀는 값 1개에 흔들리지 않게)."""
        now = time.monotonic()
        vals = [d for t, d in reversed(self.samples[-8:]) if d is not None and now - t <= max_age_s][:n]
        if not vals:
            return None
        vals.sort()
        return vals[len(vals) // 2]


def hampel(t: Sequence[float], d: Sequence[float], half: int = 3, k: float = 3.0) -> tuple[list[float], list[float], int]:
    """튀는 값(차가 기울어 초음파가 바닥·다른 물체에 맞은 값 등)을 뺍니다.

    차가 움직이는 동안 거리는 직선으로 줄어드므로, 이웃 점들(자기 자신 제외)에 테일–센 직선
    (두 점씩 짝지은 기울기의 중앙값)을 맞춰 그 점을 예측하고, 예측과 차이가 이웃들의 흩어짐(MAD)의
    k배와 1.5 cm보다 크면 뺍니다. 그냥 중앙값과 비교하면 빠르게 움직일 때 정상 값도 빠지거나
    튀는 값을 놓칩니다.
    """
    out_t, out_d, removed = [], [], 0
    n = len(d)
    for i in range(n):
        nb = [j for j in range(max(0, i - half), min(n, i + half + 1)) if j != i]
        if len(nb) < 3:
            out_t.append(t[i]); out_d.append(d[i])
            continue
        slopes = sorted((d[b] - d[a]) / (t[b] - t[a]) for x, a in enumerate(nb) for b in nb[x + 1:] if t[b] != t[a])
        slope = slopes[len(slopes) // 2] if slopes else 0.0
        inter = sorted(d[j] - slope * t[j] for j in nb)
        c = inter[len(inter) // 2]
        res = sorted(abs(d[j] - (c + slope * t[j])) for j in nb)
        mad = res[len(res) // 2] * 1.4826
        if abs(d[i] - (c + slope * t[i])) > max(k * mad, 1.5):
            removed += 1
            continue
        out_t.append(t[i]); out_d.append(d[i])
    return out_t, out_d, removed


def local_speed(t: Sequence[float], d_cm: Sequence[float], half_window_s: float = 0.15, approach: bool = True) -> list[float | None]:
    """각 점 앞뒤 half_window_s 안의 점들에 직선을 맞춘 기울기로 속력(m/s)을 구합니다."""
    out: list[float | None] = []
    n = len(t)
    for i in range(n):
        idx = [j for j in range(n) if abs(t[j] - t[i]) <= half_window_s]
        if len(idx) < 4:
            out.append(None)
            continue
        mt = sum(t[j] for j in idx) / len(idx)
        md = sum(d_cm[j] for j in idx) / len(idx)
        sxx = sum((t[j] - mt) ** 2 for j in idx)
        if sxx <= 0:
            out.append(None)
            continue
        slope = sum((t[j] - mt) * (d_cm[j] - md) for j in idx) / sxx  # cm/s
        out.append(round((-slope if approach else slope) / 100.0, 4))
    return out


def _interp(t: Sequence[float], y: Sequence[float], x: float) -> float | None:
    if not t or x < t[0] or x > t[-1]:
        return None
    for i in range(len(t) - 1):
        if t[i] <= x <= t[i + 1]:
            w = (x - t[i]) / (t[i + 1] - t[i]) if t[i + 1] > t[i] else 0.0
            return y[i] + (y[i + 1] - y[i]) * w
    return y[-1]


def analyze(t_s: Sequence[float], cm: Sequence[float | None], front_t_s: float | None, rear_t_s: float | None,
            axle_speed_mps: float | None, direction: str = "forward", ignore_before_s: float | None = None) -> dict[str, Any]:
    """초음파 기록 분석.

    - approach_speed_mps : 방지턱 직전(앞바퀴 0.5~0.1초 전) 평균 속력
    - bump_speed_mps     : 앞바퀴 ~ 뒷바퀴+0.1초 평균 속력(앞·뒤 바퀴 간격으로 구한 속도와 비교)
    - min_speed_mps / drop_pct : 방지턱 근처 가장 느린 속력과 직전 대비 감소율
    - us_bump_t_s        : 초음파 속력만으로 찾은 '속력 골' 시각(직전 최고 속력보다 8% 이상 느려진 가장 깊은 곳)
    - distance_at_front_cm : 앞바퀴가 턱에 닿았을 때 벽까지 거리(회차마다 같아야 정상 → 방지턱 위치 확인)
    """
    pairs = [(t, d) for t, d in zip(t_s, cm) if d is not None]
    res: dict[str, Any] = {"n_samples": len(t_s), "n_valid": len(pairs), "n_removed": 0, "rate_hz": None,
                           "approach_speed_mps": None, "bump_speed_mps": None, "min_speed_mps": None, "drop_pct": None,
                           "us_bump_t_s": None, "us_vs_imu_s": None, "distance_at_front_cm": None,
                           "axle_vs_us_pct": None, "max_speed_mps": None}
    if len(pairs) < 6:
        return res
    t = [p[0] for p in pairs]
    d = [p[1] for p in pairs]
    if t[-1] > t[0]:
        res["rate_hz"] = round((len(t) - 1) / (t[-1] - t[0]), 1)
    t, d, removed = hampel(t, d)
    res["n_removed"] = removed
    v = local_speed(t, d, approach=(direction != "backward"))
    vt = [(ti, vi) for ti, vi in zip(t, v) if vi is not None]
    if not vt:
        return res
    res["max_speed_mps"] = round(max(x for _, x in vt), 4)

    def mean_between(a: float, b: float) -> float | None:
        xs = [vi for ti, vi in vt if a <= ti <= b]
        return round(sum(xs) / len(xs), 4) if xs else None

    if front_t_s is not None:
        res["approach_speed_mps"] = mean_between(front_t_s - 0.5, front_t_s - 0.1)
        end = (rear_t_s if rear_t_s is not None else front_t_s + 0.2) + 0.1
        res["bump_speed_mps"] = mean_between(front_t_s, end)
        near = [vi for ti, vi in vt if front_t_s - 0.05 <= ti <= end + 0.2]
        if near:
            res["min_speed_mps"] = round(min(near), 4)
            if res["approach_speed_mps"]:
                res["drop_pct"] = round((1 - res["min_speed_mps"] / res["approach_speed_mps"]) * 100, 1)
        dd = _interp(t, d, front_t_s)
        res["distance_at_front_cm"] = None if dd is None else round(dd, 1)
        if axle_speed_mps and res["bump_speed_mps"]:
            res["axle_vs_us_pct"] = round((axle_speed_mps / res["bump_speed_mps"] - 1) * 100, 1)
    # 초음파만으로 방지턱 찾기: 달리는 중(최고 속력의 50% 이상) 직전 최고 속력보다 8% 이상 느려진 가장 깊은 골
    best = None
    run_max = 0.0
    top = res["max_speed_mps"] or 0
    for ti, vi in vt:
        if ignore_before_s is not None and ti < ignore_before_s:
            continue  # 일부러 감속한 구간은 방지턱 골로 보지 않음
        run_max = max(run_max, vi)
        if run_max >= 0.5 * top and vi <= run_max * 0.92:
            depth = 1 - vi / run_max
            # 멈추는 구간(속력이 계속 0으로 감)은 제외: 이후에 다시 빨라지는 골만
            # 다시 거의 원래 속력(직전 최고의 90%)까지 회복하는 골만 '방지턱'으로 봄(멈추는 구간은 회복하지 않음)
            later = [x for tt, x in vt if ti < tt <= ti + 0.5]
            if later and max(later) >= run_max * 0.9 and (best is None or depth > best[1]):
                best = (ti, depth)
    if best is not None:
        res["us_bump_t_s"] = round(best[0], 3)
        res["us_dip_pct"] = round(best[1] * 100, 1)
        if front_t_s is not None:
            res["us_vs_imu_s"] = round(best[0] - front_t_s, 3)
    return res


def series(t_s: Sequence[float], cm: Sequence[float | None], direction: str = "forward") -> dict[str, list]:
    """그래프용: 거리(원래 값), 걸러 낸 거리, 속력."""
    pairs = [(t, d) for t, d in zip(t_s, cm) if d is not None]
    if len(pairs) < 2:
        return {"t_s": [], "cm": [], "t_clean": [], "cm_clean": [], "speed_mps": []}
    t = [p[0] for p in pairs]
    d = [p[1] for p in pairs]
    tc, dc, _ = hampel(t, d)
    return {"t_s": [round(x, 4) for x in t], "cm": [round(x, 2) for x in d], "t_clean": [round(x, 4) for x in tc],
            "cm_clean": [round(x, 2) for x in dc], "speed_mps": local_speed(tc, dc, approach=(direction != "backward"))}


def synthesize(front_t_s: float | None, rear_t_s: float | None, speed_mps: float | None, pre_roll_s: float,
               end_s: float, start_cm: float = 160.0, hz: float = 20.0, seed: int = 0) -> tuple[list[float], list[float | None]]:
    """모의 모드용 합성 거리: 출발 → 일정 속력 → 방지턱에서 12% 느려짐 → 정지. ±0.4 cm 잡음."""
    import math
    import random
    rng = random.Random(seed)
    v_run = speed_mps or 0.5
    ts, ds = [], []
    x = 0.0
    dt = 1.0 / hz
    t = 0.0
    while t <= end_s:
        if t < pre_roll_s:
            v = 0.0
        else:
            v = v_run * (1 - math.exp(-(t - pre_roll_s) / 0.15))
            if front_t_s is not None:
                end_b = (rear_t_s if rear_t_s is not None else front_t_s + 0.2) + 0.1
                if front_t_s <= t <= end_b:
                    v *= 0.88
        x += v * dt
        d = start_cm - x * 100 + rng.gauss(0, 0.4)
        ts.append(round(t, 4))
        ds.append(round(d, 2) if d > VALID_MIN_CM else None)
        t += dt
    return ts, ds
