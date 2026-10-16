"""방지턱 통과 충격 분석 (보고서 III~V장의 계산 절차를 그대로 구현).

모든 함수는 순수 파이썬이며 numpy 없이 라즈베리파이에서 동작합니다.

용어
- vertical_g : 정지 보정 자세 기준 수직 가속도. 정지 1 g, 자유낙하 0 g.
- Δa (delta) : vertical_g − 1. 충격으로 인한 수직 가속도 증가분(g).
- 앞/뒤 바퀴 피크 : 앞바퀴와 뒷바퀴가 차례로 방지턱에 닿을 때 생기는 두 피크.
  간격 Δt와 축간거리 L로 실제 통과 속도 v = L / Δt 를 구합니다.
- Π = v² / (g · R_eff) : R_eff = 방지턱 곡률 반경 R + 바퀴 반지름 r. Π = 1이 점프 경계.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

G = 9.80665
MIN_SPEED_MPS = 0.05  # PiCar-X가 방지턱을 넘으며 낼 수 있는 속도 범위(이 밖이면 바퀴 짝이 아님)
MAX_SPEED_MPS = 2.0


# --------------------------------------------------------------------------- 형상
def bump_radius_mm(height_mm: float | None, width_mm: float | None) -> float | None:
    """원호 단면 방지턱의 곡률 반경 R = (w²/4 + h²) / (2h)."""
    if not height_mm or not width_mm or height_mm <= 0 or width_mm <= 0:
        return None
    return (width_mm ** 2 / 4 + height_mm ** 2) / (2 * height_mm)


def effective_radius_m(radius_mm: float | None, wheel_radius_mm: float | None) -> float | None:
    if radius_mm is None or not wheel_radius_mm:
        return None
    return (radius_mm + wheel_radius_mm) / 1000.0


def critical_speed_mps(r_eff_m: float | None) -> float | None:
    """원운동 모델의 점프 임계 속도 v_c = √(g·R_eff)."""
    return math.sqrt(G * r_eff_m) if r_eff_m else None


# --------------------------------------------------------------------------- 피크
@dataclass
class Peak:
    index: int
    t_s: float
    value_g: float


def find_peaks(t: Sequence[float], v: Sequence[float], threshold_delta_g: float, min_gap_s: float) -> list[Peak]:
    """vertical_g − 1 ≥ 기준인 국소 최댓값. min_gap 안의 피크는 가장 큰 것만 남깁니다."""
    peaks: list[Peak] = []
    level = 1.0 + threshold_delta_g
    for i in range(1, len(v) - 1):
        if v[i] >= level and v[i] >= v[i - 1] and v[i] > v[i + 1]:
            if peaks and t[i] - peaks[-1].t_s < min_gap_s:
                if v[i] > peaks[-1].value_g:
                    peaks[-1] = Peak(i, t[i], v[i])
            else:
                peaks.append(Peak(i, t[i], v[i]))
    return peaks


def pick_axle_pair(peaks: list[Peak], wheelbase_mm: float | None) -> tuple[Peak, Peak] | None:
    """앞바퀴·뒷바퀴 피크 한 쌍을 고릅니다.

    축간거리를 알면 속도가 물리적으로 가능한 범위(0.03~4 m/s)인 쌍 중
    두 피크 높이의 합이 가장 큰 쌍을, 모르면 가장 큰 두 피크를 시간순으로 고릅니다.
    """
    if len(peaks) < 2:
        return None
    ranked = sorted(peaks, key=lambda p: p.value_g, reverse=True)[:6]
    if not wheelbase_mm:
        a, b = sorted(ranked[:2], key=lambda p: p.t_s)
        return a, b
    L = wheelbase_mm / 1000.0
    best: tuple[float, tuple[Peak, Peak]] | None = None
    for i in range(len(ranked)):
        for j in range(i + 1, len(ranked)):
            a, b = sorted((ranked[i], ranked[j]), key=lambda p: p.t_s)
            dt = b.t_s - a.t_s
            if dt <= 0:
                continue
            speed = L / dt
            if MIN_SPEED_MPS <= speed <= MAX_SPEED_MPS:
                score = a.value_g + b.value_g
                if best is None or score > best[0]:
                    best = (score, (a, b))
    return best[1] if best else None


def pitch_series(columns: dict[str, Sequence[float]], still_s: float = 0.5) -> list[float] | None:
    """자이로로 앞뒤 기울기(피치, °)를 적분합니다. 처음 정지 구간 평균을 영점으로 씁니다.

    수직축은 정지 상태에서 가속도가 가장 큰 축, 피치축은 남은 두 축 중 주행 중 회전이 더 큰 축입니다.
    부호(앞이 들릴 때 +인지 −인지)는 센서 장착 방향에 따라 다르므로 쓰는 쪽에서 맞춥니다.
    """
    t = list(columns.get("t_s") or [])
    axes = ["gx_dps", "gy_dps", "gz_dps"]
    if len(t) < 50 or not all(columns.get(k) for k in axes + ["ax_g", "ay_g", "az_g"]):
        return None
    still = [i for i, x in enumerate(t) if x - t[0] <= still_s]
    if len(still) < 10:
        return None
    acc = [abs(sum(columns[k][i] for i in still) / len(still)) for k in ("ax_g", "ay_g", "az_g")]
    vert = max(range(3), key=lambda i: acc[i])
    def bias(k: str) -> float:
        return sum(columns[k][i] for i in still) / len(still)
    cands = [axes[i] for i in range(3) if i != vert]
    def spread(k: str) -> float:
        b = bias(k)
        return sum((g - b) ** 2 for g in columns[k])
    k = max(cands, key=spread)
    b = bias(k)
    g = columns[k]
    out, p = [0.0], 0.0
    for i in range(1, len(t)):
        p += (g[i] - b) * (t[i] - t[i - 1])
        out.append(p)
    return out


def _mean_between(t: Sequence[float], y: Sequence[float], lo: float, hi: float) -> float | None:
    """구간 가운데 60%의 평균(충격 순간의 튐을 피함)."""
    a, b = lo + 0.2 * (hi - lo), hi - 0.2 * (hi - lo)
    vals = [y[i] for i in range(len(t)) if a <= t[i] <= b]
    return sum(vals) / len(vals) if vals else None


def pitch_axle_times(t: Sequence[float], pitch: Sequence[float] | None, wheelbase_mm: float | None,
                     bump_width_mm: float | None, around: tuple[float, float] | None = None) -> tuple[float, float, float] | None:
    """앞뒤 기울기(피치)로 앞바퀴·뒷바퀴가 턱에 오른 시각을 찾습니다. 반환: (앞 오름, 뒤 오름, 최대 기울기°)

    앞바퀴가 턱에 오르면 앞이 들리고(+θ), 뒷바퀴가 오르면
      · 방지턱이 축간거리보다 넓을 때: 두 바퀴가 다 위에 있어 다시 수평(0)이 되고
      · 좁을 때: 앞바퀴는 이미 내려왔으므로 뒤가 들림(−θ)
    오름 시각은 기울기가 최대치의 25%를 넘는(또는 내려가는) 순간으로 잡습니다. 기울기 변화가 뚜렷하지 않으면 None.
    """
    if pitch is None or not wheelbase_mm or len(t) < 20:
        return None
    rng = [i for i in range(len(pitch)) if around is None or around[0] <= t[i] <= around[1]]
    if not rng:
        return None
    big = max(abs(pitch[i]) for i in rng)
    if big < 2.5:
        return None
    # 먼저 나타난 큰 기울기가 앞바퀴(뒷바퀴 기울기는 반대 부호로 나중에 나옴)
    first = next(i for i in rng if abs(pitch[i]) >= 0.5 * big)
    sgn = 1.0 if pitch[first] > 0 else -1.0
    p = [sgn * x for x in pitch]
    k = first
    while k + 1 < len(p) and p[k + 1] >= p[k] - 0.05 * big:
        k += 1
        if p[k] < 0.5 * big:
            break
    k = max(range(first, k + 1), key=lambda i: p[i])
    theta = pitch[k]
    lvl = 0.25 * abs(theta)
    # 최대 기울기 직전, 기울기가 lvl을 처음 넘은 순간 = 첫 번째 바퀴(앞바퀴)가 오르는 중
    i0 = k
    while i0 > 0 and p[i0 - 1] >= lvl:
        i0 -= 1
    if i0 == 0:
        return None
    t_front = t[i0]
    # 최대 이후 lvl 아래로 내려간 순간
    i1 = k
    while i1 < len(p) - 1 and p[i1] >= lvl:
        i1 += 1
    if i1 >= len(p) - 1:
        return None
    wide = bump_width_mm is not None and bump_width_mm >= wheelbase_mm
    if bump_width_mm is None:
        # 폭을 모르면: 내려온 뒤 50 ms 이상 수평에 머물면 넓은 방지턱(두 바퀴가 다 위)으로 봄
        j = i1
        while j < len(p) - 1 and abs(p[j]) < lvl and t[j] - t[i1] < 0.05:
            j += 1
        wide = t[j] - t[i1] >= 0.05
    if wide:
        t_rear = t[i1]
    else:
        j = i1
        while j < len(p) - 1 and p[j] > -lvl and t[j] - t[i1] < 1.0:
            j += 1
        if p[j] > -lvl:
            return None
        t_rear = t[j]
    if t_rear <= t_front:
        return None
    return t_front, t_rear, abs(theta)


def pick_axle_pattern(peaks: list[Peak], wheelbase_mm: float | None, bump_width_mm: float | None,
                      t: Sequence[float] | None = None, pitch: Sequence[float] | None = None,
                      bump_height_mm: float | None = None, all_out: list | None = None) -> tuple[Peak, Peak, Peak, Peak, float] | None:
    """방지턱 폭이 있으면 바퀴마다 '올라갈 때'와 '내려올 때' 두 번 충격이 납니다(총 4개).

    앞바퀴 오름(a) → 앞바퀴 내림(b) → 뒷바퀴 오름(c) → 뒷바퀴 내림(d) (폭이 축간거리보다 크면 b와 c 순서가 바뀜)
    두 바퀴는 같은 방지턱을 같은 속도로 지나므로
      · 앞 오름→뒤 오름(c−a) ≈ 앞 내림→뒤 내림(d−b) = L / v
      · 앞 오름→앞 내림(b−a) ≈ 뒤 오름→뒤 내림(d−c) = (방지턱을 지나는 거리) / v
    이 규칙을 만족하는 4개 묶음 중 충격 합이 가장 큰 것을 고릅니다. 반환: (a, b, c, d, 속도)
    """
    if not wheelbase_mm or len(peaks) < 4:
        return None
    L = wheelbase_mm / 1000.0
    top = sorted(sorted(peaks, key=lambda p: p.value_g, reverse=True)[:10], key=lambda p: p.t_s)
    best: tuple[float, tuple[Peak, Peak, Peak, Peak, float]] | None = None
    n = len(top)
    for ia_ in range(n):
        a = top[ia_]
        for ic in range(ia_ + 1, n):
            c = top[ic]
            d1 = c.t_s - a.t_s
            v = L / d1 if d1 > 0 else 0
            if not (MIN_SPEED_MPS <= v <= MAX_SPEED_MPS):
                continue
            for ib in range(ia_ + 1, n):
                if ib == ic:
                    continue
                b = top[ib]
                # 방지턱 폭이 축간거리보다 좁으면 앞바퀴가 내려온 뒤 뒷바퀴가 올라가고(b < c), 넓으면 반대(c < b)입니다.
                if bump_width_mm and wheelbase_mm and ((bump_width_mm < wheelbase_mm) != (b.t_s < c.t_s)):
                    continue
                s1 = b.t_s - a.t_s
                for idd in range(max(ib, ic) + 1, n):
                    d = top[idd]
                    d2, s2 = d.t_s - b.t_s, d.t_s - c.t_s
                    if s2 <= 0 or abs(d1 - d2) > 0.15 * max(d1, d2) or abs(s1 - s2) > 0.35 * max(s1, s2):
                        continue
                    span = (d1 + d2) / 2
                    speed = L / span
                    if bump_width_mm:
                        w_est = speed * (s1 + s2) / 2 * 1000.0
                        if not (0.4 * bump_width_mm <= w_est <= 2.5 * bump_width_mm):
                            continue
                    mismatch = abs(d1 - d2) / span + abs(s1 - s2) / max(s1, s2)
                    score = a.value_g + b.value_g + c.value_g + d.value_g - 2.0 * mismatch
                    if pitch is not None and t is not None and bump_height_mm:
                        # 앞바퀴만 턱 위 → 앞이 들림(+θ), 뒷바퀴만 턱 위 → 뒤가 들림(−θ), 두 바퀴 다 평지 → 0
                        theta = math.degrees(math.asin(min(1.0, bump_height_mm / wheelbase_mm)))
                        p1 = _mean_between(t, pitch, a.t_s, b.t_s)
                        p2 = _mean_between(t, pitch, c.t_s, d.t_s)
                        if p1 is not None and p2 is not None:
                            sgn = 1.0 if p1 >= 0 else -1.0
                            err = abs(sgn * p1 - theta) + abs(sgn * p2 + theta)
                            if b.t_s < c.t_s:
                                p0 = _mean_between(t, pitch, b.t_s, c.t_s)
                                err += abs(p0) if p0 is not None else 0.0
                            score -= 0.3 * err
                    if all_out is not None:
                        all_out.append((score, speed))
                    if best is None or score > best[0]:
                        best = (score, (a, b, c, d, speed))
    return best[1] if best else None


def airborne_segments(t: Sequence[float], v: Sequence[float], jump_g: float, min_duration_s: float = 0.005) -> list[tuple[float, float, float]]:
    """수직 가속도가 jump_g 미만으로 유지된 구간(공중에 뜬 구간) 목록: (시작, 끝, 최솟값)."""
    segments: list[tuple[float, float, float]] = []
    start: int | None = None
    for i, value in enumerate(v):
        if value < jump_g:
            if start is None:
                start = i
        elif start is not None:
            if t[i - 1] - t[start] >= min_duration_s:
                segments.append((t[start], t[i - 1], min(v[start:i])))
            start = None
    if start is not None and t[-1] - t[start] >= min_duration_s:
        segments.append((t[start], t[-1], min(v[start:])))
    return segments


DETECT_LEVELS: dict[str, tuple[float, float, float]] = {
    # 이름: (진동 σ의 몇 배부터 피크로 보나, 바퀴 피크가 다른 진동보다 몇 배 커야 방지턱으로 인정하나,
    #        '최소 충격' 설정값에 곱할 배율)
    "strict": (6.0, 1.5, 1.0),
    "normal": (4.5, 1.3, 1.0),
    "sensitive": (3.5, 1.15, 0.6),
}


def lowpass(t: Sequence[float], v: Sequence[float], cutoff_hz: float) -> list[float]:
    """2차 버터워스 저역 통과 필터를 앞뒤로 한 번씩 적용(위상 지연 없음).

    차체·센서가 출렁이는 빠른 진동(수십 Hz)을 줄이고, 방지턱 충격처럼 수십 ms 동안 이어지는
    변화는 남깁니다. 필터를 쓰면 피크 값이 조금 작아지므로 같은 설정으로 측정한 값끼리 비교하세요.
    """
    n = min(len(t), len(v))
    if not cutoff_hz or n < 8 or t[n - 1] <= t[0]:
        return list(v[:n])
    fs = (n - 1) / (t[n - 1] - t[0])
    if cutoff_hz >= fs / 2.2:
        return list(v[:n])
    import math as _m
    k = _m.tan(_m.pi * cutoff_hz / fs)
    q = _m.sqrt(2.0)
    norm = 1.0 / (1.0 + q * k + k * k)
    b0 = k * k * norm
    b1, b2 = 2.0 * b0, b0
    a1 = 2.0 * (k * k - 1.0) * norm
    a2 = (1.0 - q * k + k * k) * norm

    def run(x: list[float]) -> list[float]:
        y = [0.0] * len(x)
        x1 = x2 = x[0]
        y1 = y2 = x[0]
        for i, xi in enumerate(x):
            yi = b0 * xi + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
            x2, x1, y2, y1 = x1, xi, y1, yi
            y[i] = yi
        return y

    fwd = run(list(v[:n]))
    return run(fwd[::-1])[::-1]


def _robust_sigma(values: list[float]) -> float:
    """중앙값 절대편차(MAD)로 구한 표준편차. 큰 피크 몇 개에 흔들리지 않습니다."""
    if not values:
        return 0.0
    ordered = sorted(values)
    med = ordered[len(ordered) // 2]
    dev = sorted(abs(x - med) for x in values)
    return 1.4826 * dev[len(dev) // 2]


def _manual_pair(t: Sequence[float], v: Sequence[float], front: float, rear: float, window_s: float = 0.015) -> tuple[Peak, Peak] | None:
    """지정한 시각 ±15 ms 안의 최댓값을 피크로 씁니다."""
    def near(target: float) -> Peak | None:
        idx = [i for i in range(len(t)) if abs(t[i] - target) <= window_s]
        if not idx:
            return None
        i = max(idx, key=lambda j: v[j])
        return Peak(i, t[i], v[i])
    a, b = near(front), near(rear)
    if a is None or b is None or b.t_s <= a.t_s:
        return None
    return a, b


# --------------------------------------------------------------------------- 시행 분석
def analyze_run(
    columns: dict[str, Sequence[float]],
    *,
    wheelbase_mm: float | None,
    mass_g: float | None,
    bump_radius: float | None = None,
    wheel_radius_mm: float | None = None,
    threshold_delta_g: float = 0.3,
    min_gap_s: float = 0.03,
    jump_g: float = 0.2,
    window: tuple[float | None, float | None] = (None, None),
    override: dict[str, float] | None = None,
    filter_hz: float = 0.0,
    detect_level: str = "normal",
    bump_width_mm: float | None = None,
    bump_height_mm: float | None = None,
) -> dict[str, Any]:
    """한 시행 분석.

    앞·뒤 바퀴 구분 규칙: 차는 앞으로 가므로 **앞바퀴가 먼저** 방지턱에 닿습니다.
    그래서 짝으로 고른 두 피크 중 시간이 이른 쪽이 앞바퀴, 늦은 쪽이 뒷바퀴입니다.
    짝은 (1) 착지 피크를 빼고 (2) 축간거리로 계산한 속도가 0.03~4 m/s인 쌍 중
    (3) 두 피크 높이의 합이 가장 큰 쌍으로 고릅니다. ``override``로 사람이 직접 지정할 수 있습니다.
    """
    t_all = list(columns.get("t_s") or [])
    v_all = list(columns.get("vertical_g") or [])
    if filter_hz:
        v_all = lowpass(t_all, v_all, filter_hz)
    noise_k, standout_min, floor_scale = DETECT_LEVELS.get(detect_level, DETECT_LEVELS["normal"])
    dyn_all = list(columns.get("dynamic_g") or [])
    n = min(len(t_all), len(v_all))
    lo, hi = window
    idx = [i for i in range(n) if (lo is None or t_all[i] >= lo) and (hi is None or t_all[i] <= hi)]
    t = [t_all[i] for i in idx]
    v = [v_all[i] for i in idx]
    flags: list[str] = []
    result: dict[str, Any] = {
        "sample_count": n,
        "duration_s": round(t_all[n - 1] - t_all[0], 4) if n > 1 else 0.0,
        "sample_rate_hz": round((n - 1) / (t_all[n - 1] - t_all[0]), 1) if n > 1 and t_all[n - 1] > t_all[0] else None,
        "peak_g": None, "peak_delta_g": None, "peak_t_s": None,
        "peak_dynamic_g": round(max(dyn_all), 4) if dyn_all else None,
        "front_t_s": None, "front_g": None, "rear_t_s": None, "rear_g": None,
        "axle_dt_s": None, "speed_mps": None,
        "min_g": None, "jump": None, "airborne_s": None, "landing_t_s": None, "landing_g": None,
        "peak_force_n": None, "dynamic_force_n": None,
        "pi": None, "r_eff_m": None, "critical_speed_mps": None,
        "peak_count": 0, "flags": flags, "candidates": [], "pair_source": None,
        "bump_detected": None, "noise_sigma_g": None, "threshold_used_g": None, "standout_ratio": None,
        "params": {"threshold_delta_g": threshold_delta_g, "min_gap_s": min_gap_s, "jump_g": jump_g,
                   "wheelbase_mm": wheelbase_mm, "wheel_radius_mm": wheel_radius_mm, "bump_radius_mm": bump_radius,
                   "filter_hz": filter_hz, "detect_level": detect_level},
    }
    if len(v) < 3:
        flags.append("표본이 너무 적습니다.")
        return result

    k = max(range(len(v)), key=lambda i: v[i])
    result.update(peak_g=round(v[k], 4), peak_delta_g=round(v[k] - 1.0, 4), peak_t_s=round(t[k], 4))
    if result["sample_rate_hz"] is not None and result["sample_rate_hz"] < 300:
        flags.append(f"표본 속도 {result['sample_rate_hz']} Hz – 짧은 충돌 피크를 놓쳤을 수 있습니다.")
    if v[k] >= 7.5:
        flags.append("±8 g 범위 포화 가능성이 있습니다.")

    # 주행 진동의 크기(σ)를 중앙값 기반으로 재고, 피크 기준을 max(설정값, k·σ)로 올립니다(k는 감지 민감도).
    # 바닥이 거칠거나 모터 진동이 큰 날에도 진동을 바퀴 피크로 착각하지 않게 하려는 것입니다.
    sigma = _robust_sigma([x - 1.0 for x in v])
    # 진동을 거르면 충격 봉우리도 조금 낮아지므로 고정 최소값을 절반으로 낮춥니다(진동 기준 k·σ는 그대로 지켜 줌).
    if filter_hz:
        floor_scale *= 0.5
    threshold_used = max(threshold_delta_g * floor_scale, noise_k * sigma)
    result.update(noise_sigma_g=round(sigma, 4), threshold_used_g=round(threshold_used, 4))
    peaks = find_peaks(t, v, threshold_used, min_gap_s)
    result["peak_count"] = len(peaks)
    # 공중 구간 직후(30 ms 이내)의 피크는 바퀴가 방지턱에 닿은 피크가 아니라 착지 피크이므로
    # 앞·뒤 바퀴 짝짓기에서 뺍니다. 착지 피크를 뒷바퀴로 잘못 고르면 속도가 크게 틀어집니다.
    segments = airborne_segments(t, v, jump_g)
    landing = [p for p in peaks if any(end <= p.t_s <= end + 0.03 for _, end, _ in segments)]
    if landing:
        hardest = max(landing, key=lambda p: p.value_g)
        result.update(landing_t_s=round(hardest.t_s, 4), landing_g=round(hardest.value_g, 4))
    if segments:
        result["airborne_s"] = round(sum(end - start for start, end, _ in segments), 4)
    result["candidates"] = [
        {"t_s": round(p.t_s, 4), "g": round(p.value_g, 4), "landing": p in landing}
        for p in sorted(sorted(peaks, key=lambda p: p.value_g, reverse=True)[:12], key=lambda p: p.t_s)
    ]
    # 1순위: 바퀴마다 오름·내림 두 충격(총 4개)이 규칙적으로 나타나는 묶음. 내림 충격은 착지처럼 보여도 포함합니다.
    pitch_all = pitch_series(columns)
    pitch = [pitch_all[i] for i in idx] if pitch_all is not None else None
    # 0순위: 기울기(피치)로 앞·뒤 바퀴가 턱에 오른 시각을 찾고, 그 근처(±80 ms)의 가장 큰 충격 피크에 맞춥니다.
    # 충격이 있었던 구간 근처에서만 찾습니다(주행 끝에 벽·완충재에 올라타 기운 것을 방지턱으로 착각하지 않게).
    span = (peaks[0].t_s - 0.4, peaks[-1].t_s + 0.2) if peaks else None
    by_pitch = pitch_axle_times(t, pitch, wheelbase_mm, bump_width_mm, span) if span else None
    if by_pitch is not None:
        tf, tr, th = by_pitch
        def near(target: float) -> Peak | None:
            close = [p for p in find_peaks(t, v, max(0.05, 2.0 * sigma), min_gap_s) if abs(p.t_s - target) <= 0.08]
            return max(close, key=lambda p: p.value_g) if close else None
        pf, pr = near(tf), near(tr)
        result["pitch"] = {"front_up_t_s": round(tf, 4), "rear_up_t_s": round(tr, 4), "max_deg": round(th, 2)}
        if pf is not None and pr is not None and pr.t_s > pf.t_s and abs((pr.t_s - pf.t_s) / (tr - tf) - 1) <= 0.25:
            pitch_pair = (pf, pr)
        else:
            # 충격 피크가 애매하면 기울기 시각을 그대로 씁니다(높이는 그 시각 근처 최댓값).
            def at(target: float) -> Peak:
                idxs = [i for i in range(len(t)) if abs(t[i] - target) <= 0.03] or [min(range(len(t)), key=lambda i: abs(t[i] - target))]
                i = max(idxs, key=lambda i: v[i])
                return Peak(i, target, v[i])
            pitch_pair = (at(tf), at(tr))
    else:
        pitch_pair = None
    scored: list = []
    pattern = None if pitch_pair is not None else pick_axle_pattern(peaks, wheelbase_mm, bump_width_mm, t, pitch, bump_height_mm, scored)
    if pattern is not None and scored:
        # 점수가 거의 같은데 속도가 20% 넘게 다른 다른 해석이 있으면 어느 쪽인지 확신할 수 없습니다.
        top_score = max(sc for sc, _ in scored)
        close = sorted(sp for sc, sp in scored if top_score - sc <= 0.6)
        rivals: list[float] = []
        for sp in close:  # 10% 안쪽으로 비슷한 속도는 하나로 묶음
            if not rivals or sp > rivals[-1] * 1.1:
                rivals.append(round(sp, 2))
        if len(rivals) > 1:
            result["speed_candidates_mps"] = rivals
            flags.append("앞·뒤 바퀴 구분이 애매합니다: 충격 4개(바퀴마다 오름·내림)를 두 가지로 해석할 수 있어 속도 후보가 "
                         + " / ".join(f"{x:.2f}" for x in rivals) + " m/s입니다. 영상으로 한 번 속도를 재서 확인하거나, "
                         "폭이 아주 좁은(둥근 막대) 또는 아주 넓은(25 cm 이상) 방지턱을 쓰면 구분이 확실해집니다.")
            pattern = None
    if pitch_pair is not None:
        pair = pitch_pair
        result["pair_source"] = "pitch"
    elif pattern is not None:
        pa, pb, pc, pd, pv = pattern
        pair = (pa, pc)
        result["pair_source"] = "pattern"
        result["pattern"] = {"front_up_t_s": round(pa.t_s, 4), "front_down_t_s": round(pb.t_s, 4),
                             "rear_up_t_s": round(pc.t_s, 4), "rear_down_t_s": round(pd.t_s, 4),
                             "speed_mps": round(pv, 4),
                             "bump_pass_s": round(((pb.t_s - pa.t_s) + (pd.t_s - pc.t_s)) / 2, 4)}
        landing = [p for p in landing if p not in (pa, pb, pc, pd)]
        result["landing_t_s"] = result["landing_g"] = None
    else:
        pair = pick_axle_pair([p for p in peaks if p not in landing], wheelbase_mm)
        result["pair_source"] = "auto" if pair else None
    if override and override.get("front_t_s") is not None and override.get("rear_t_s") is not None:
        manual = _manual_pair(t, v, float(override["front_t_s"]), float(override["rear_t_s"]))
        if manual:
            pair = manual
            result["pair_source"] = "manual"
            flags.append("앞·뒤 바퀴 피크를 사람이 직접 지정했습니다.")
    if pair is None and landing:
        # 착지와 뒷바퀴 접지가 겹친 경우: 착지 피크를 포함해 다시 짝을 찾되 표시해 둡니다.
        pair = pick_axle_pair(peaks, wheelbase_mm)
        if pair is not None:
            flags.append("착지 피크가 바퀴 피크와 겹쳐 짝에 포함했습니다 – 속도 오차 가능(영상 실측과 비교 권장).")
    if pair is not None and result["pair_source"] not in ("manual", "pitch"):
        # 방지턱이 정말 있었는지: 고른 두 피크가 나머지 진동 피크보다 뚜렷이(민감도에 따라 1.15~1.5배 이상) 커야 합니다.
        small = find_peaks(t, v, max(0.08, min(3.0, noise_k * 0.66) * sigma), min_gap_s)
        near = lambda p: any(0 <= p.t_s - q.t_s <= 0.04 or abs(p.t_s - q.t_s) < 1e-9 for q in pair)  # noqa: E731
        others = [p.value_g - 1.0 for p in small if not near(p) and p not in landing
                  and not any(abs(p.t_s - q.t_s) < 1e-9 for q in landing)]
        weakest = min(pair[0].value_g, pair[1].value_g) - 1.0
        strongest = max(pair[0].value_g, pair[1].value_g) - 1.0
        if others:
            # 방지턱 충격은 보통 앞바퀴(또는 뒷바퀴) 하나가 뚜렷하게 큽니다. 둘 중 '뚜렷한 쪽'이 진동보다 크면 인정하고,
            # 약한 쪽은 후보 기준(k·σ)만 넘으면 됩니다(짝 간격은 같은 조건 회차끼리 다시 맞춤).
            ratio = strongest / max(others) if max(others) > 0 else None
            result["standout_ratio"] = None if ratio is None else round(ratio, 2)
            if ratio is not None and ratio < standout_min:
                flags.append(f"방지턱 통과가 확인되지 않았습니다: 가장 큰 바퀴 피크(+{strongest:.2f} g)가 다른 진동(+{max(others):.2f} g)보다 뚜렷하지 않습니다. "
                             "평지 주행이었거나 충격이 너무 작으면 이렇게 됩니다. 이 시행은 집계에서 빠집니다.")
                pair = None
                result["bump_detected"] = False
        hi, lo = max(pair[0].value_g, pair[1].value_g) - 1.0 if pair else 0, weakest
        if pair and lo > 0 and hi / lo > 4:
            flags.append("앞·뒤 바퀴 피크 크기가 4배 넘게 다릅니다. 짝이 맞는지 그래프로 확인하세요.")
    if pair is None:
        if result["bump_detected"] is None:
            result["bump_detected"] = False
            flags.append("방지턱 통과(앞·뒤 바퀴 피크 한 쌍)를 찾지 못했습니다. 평지 주행이거나 충격이 진동과 구분되지 않습니다. 이 시행은 집계에서 빠집니다.")
    else:
        result["bump_detected"] = True
        a, b = pair
        dt = b.t_s - a.t_s
        result.update(front_t_s=round(a.t_s, 4), front_g=round(a.value_g, 4),
                      rear_t_s=round(b.t_s, 4), rear_g=round(b.value_g, 4), axle_dt_s=round(dt, 4))
        if wheelbase_mm:
            if result["pair_source"] == "pattern" and result.get("pattern"):
                result["speed_mps"] = result["pattern"]["speed_mps"]  # 앞·뒤 오름 간격과 내림 간격의 평균으로 계산
            else:
                result["speed_mps"] = round((wheelbase_mm / 1000.0) / dt, 4)
        else:
            flags.append("축간거리가 없어 속도를 계산하지 않았습니다(설정에서 입력).")
    # 지면 이탈 판정: 첫 피크 직전 ~ 마지막 피크 이후 구간의 최솟값과 공중 구간 유무
    if peaks:
        seg = [v[i] for i in range(len(v)) if peaks[0].t_s - 0.05 <= t[i] <= peaks[-1].t_s + 0.3]
        if seg:
            result["min_g"] = round(min(seg), 4)
            # 진동 한 점이 잠깐 기준 아래로 내려간 것은 점프가 아닙니다. 5 ms 이상 이어진 공중 구간만 인정합니다.
            lo_t, hi_t = peaks[0].t_s - 0.05, peaks[-1].t_s + 0.3
            result["jump"] = any(end >= lo_t and start <= hi_t for start, end, _ in segments)
    if landing and not any("겹쳐" in f for f in flags):
        flags.append(f"지면 이탈 후 착지 피크 {result['landing_g']:.2f} g를 앞·뒤 바퀴 피크에서 제외했습니다.")

    if mass_g and mass_g > 0 and result["peak_g"] is not None:
        m = mass_g / 1000.0
        result["peak_force_n"] = round(m * G * result["peak_g"], 4)
        result["dynamic_force_n"] = round(m * G * result["peak_delta_g"], 4)

    r_eff = effective_radius_m(bump_radius, wheel_radius_mm)
    if r_eff:
        result["r_eff_m"] = round(r_eff, 5)
        result["critical_speed_mps"] = round(critical_speed_mps(r_eff) or 0, 4)
        if result["speed_mps"]:
            result["pi"] = round(result["speed_mps"] ** 2 / (G * r_eff), 4)
    return result


# --------------------------------------------------------------------------- 조건별 집계
def trimmed(values: list[float]) -> tuple[list[float], list[float]]:
    """보고서 규칙: 3회 이상이면 최댓값·최솟값 하나씩 제외."""
    if len(values) < 3:
        return list(values), []
    ordered = sorted(values)
    return ordered[1:-1], [ordered[0], ordered[-1]]


def _mean(values: Iterable[float]) -> float | None:
    values = [x for x in values if x is not None]
    return sum(values) / len(values) if values else None


def group_key(run: dict[str, Any]) -> tuple:
    c = run.get("condition", {})
    return (c.get("bump_id") or "-", round(float(c.get("total_mass_g") or 0) / 5) * 5,
            c.get("output_mode") or "pwm", int(round(float(c.get("output_value") or 0))))


def harmonize_pairs(runs: list[dict[str, Any]], tolerance: float = 0.3) -> list[dict[str, Any]]:
    """같은 조건 반복 회차끼리 앞·뒤 바퀴 간격(Δt)을 맞춥니다.

    같은 조건이면 통과 속도가 비슷해야 하므로, 회차들의 Δt 중앙값에서 30% 넘게 벗어난 회차는
    저장된 피크 후보 중 중앙값에 가장 가까운(±25%) 쌍으로 다시 고릅니다. 맞는 쌍이 없으면 그대로 둡니다.
    원본은 바꾸지 않고 고친 복사본 목록을 돌려줍니다(사람이 직접 지정한 쌍은 건드리지 않음).
    """
    groups: dict[tuple, list[dict[str, Any]]] = {}
    for run in runs:
        a = run.get("analysis") or {}
        if run.get("excluded") or not a.get("bump_detected") or a.get("pair_source") in ("manual", "pattern", "pitch") or not a.get("axle_dt_s"):
            continue
        groups.setdefault(group_key(run), []).append(run)
    fixed: dict[str, dict[str, Any]] = {}
    for members in groups.values():
        if len(members) < 3:
            continue
        dts = sorted(m["analysis"]["axle_dt_s"] for m in members)
        med = dts[len(dts) // 2] if len(dts) % 2 else (dts[len(dts) // 2 - 1] + dts[len(dts) // 2]) / 2
        for m in members:
            a = m["analysis"]
            if abs(a["axle_dt_s"] / med - 1) <= tolerance:
                continue
            cands = [c for c in a.get("candidates") or [] if not c.get("landing")]
            best = None
            for i, c1 in enumerate(cands):
                for c2 in cands[i + 1:]:
                    dt = c2["t_s"] - c1["t_s"]
                    if dt > 0 and abs(dt / med - 1) <= 0.25:
                        score = c1["g"] + c2["g"]
                        if best is None or score > best[0]:
                            best = (score, c1, c2)
            if best is None:
                continue
            _, f, r = best
            new = dict(a)
            dt = r["t_s"] - f["t_s"]
            wb = (a.get("params") or {}).get("wheelbase_mm")
            new.update(front_t_s=f["t_s"], front_g=f["g"], rear_t_s=r["t_s"], rear_g=r["g"], axle_dt_s=round(dt, 4),
                       pair_source="consistency")
            if wb:
                new["speed_mps"] = round(wb / 1000.0 / dt, 4)
                if a.get("r_eff_m"):
                    new["pi"] = round(new["speed_mps"] ** 2 / (G * a["r_eff_m"]), 4)
            new["flags"] = list(a.get("flags") or []) + [
                f"같은 조건 다른 회차들의 앞·뒤 바퀴 간격({med * 1000:.0f} ms)과 많이 달라({a['axle_dt_s'] * 1000:.0f} ms) 그 간격에 맞는 피크 쌍으로 다시 골랐습니다."]
            fixed[m["id"]] = {**m, "analysis": new}
    return [fixed.get(r.get("id"), r) for r in runs]


def aggregate(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple, list[dict[str, Any]]] = {}
    for run in runs:
        if run.get("excluded"):
            continue
        a = run.get("analysis") or {}
        if a.get("peak_delta_g") is None or a.get("bump_detected") is False:
            continue
        groups.setdefault(group_key(run), []).append(run)
    rows = []
    for key, items in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2], kv[0][3])):
        deltas = [r["analysis"]["peak_delta_g"] for r in items]
        kept, dropped = trimmed(deltas)
        # 제외된 최댓값·최솟값 시행을 뺀 나머지(같은 값이 여러 개여도 kept 개수만큼만)
        ordered_runs = sorted(items, key=lambda r: r["analysis"]["peak_delta_g"])
        kept_runs = ordered_runs[1:-1] if dropped else items
        speeds = [r["analysis"].get("speed_mps") for r in kept_runs if r["analysis"].get("speed_mps")]
        pis = [r["analysis"].get("pi") for r in kept_runs if r["analysis"].get("pi")]
        mean_delta = _mean(kept)
        mass_g = key[1]
        rows.append({
            "bump_id": key[0], "total_mass_g": mass_g, "output_mode": key[2], "output_value": key[3],
            "n_runs": len(items), "n_used": len(kept),
            "mean_speed_mps": round(_mean(speeds), 4) if speeds else None,
            "speed_min_mps": round(min(speeds), 4) if speeds else None,
            "speed_max_mps": round(max(speeds), 4) if speeds else None,
            "mean_peak_delta_g": round(mean_delta, 4) if mean_delta is not None else None,
            "mean_peak_g": round(mean_delta + 1.0, 4) if mean_delta is not None else None,
            "kept_min_delta_g": round(min(kept), 4), "kept_max_delta_g": round(max(kept), 4),
            "dropped_delta_g": [round(x, 4) for x in dropped],
            "mean_force_n": round(mass_g / 1000.0 * G * (mean_delta + 1.0), 4) if mean_delta is not None else None,
            "mean_pi": round(_mean(pis), 4) if pis else None,
            "jump_count": sum(1 for r in items if r["analysis"].get("jump")),
            "run_ids": [r["id"] for r in items],
        })
    return rows


# --------------------------------------------------------------------------- 회귀
def _solve(a: list[list[float]], b: list[float]) -> list[float] | None:
    """가우스 소거로 작은 선형계를 풉니다."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-12:
            return None
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(n):
            if r != col:
                f = m[r][col] / m[col][col]
                for c in range(col, n + 1):
                    m[r][c] -= f * m[col][c]
    return [m[i][n] / m[i][i] for i in range(n)]


def linear_least_squares(x: list[list[float]], y: list[float]) -> tuple[list[float], float] | None:
    """y = X·β 최소제곱. (β, R²) 반환. X의 첫 열은 상수항 1이어야 합니다."""
    if len(y) < len(x[0]):
        return None
    k = len(x[0])
    xtx = [[sum(row[i] * row[j] for row in x) for j in range(k)] for i in range(k)]
    xty = [sum(row[i] * y[r] for r, row in enumerate(x)) for i in range(k)]
    beta = _solve(xtx, xty)
    if beta is None:
        return None
    pred = [sum(b * xi for b, xi in zip(beta, row)) for row in x]
    mean_y = sum(y) / len(y)
    ss_tot = sum((yi - mean_y) ** 2 for yi in y)
    ss_res = sum((yi - pi) ** 2 for yi, pi in zip(y, pred))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 1.0
    return beta, r2


def fit_power_law(groups: list[dict[str, Any]], bump_id: str | None = None) -> dict[str, Any]:
    """Δa = k₀ · v^n · m^p 를 로그 선형회귀로 맞춥니다(조건별 평균값 사용, m은 kg)."""
    pts = [(g["mean_speed_mps"], g["total_mass_g"] / 1000.0, g["mean_peak_delta_g"]) for g in groups
           if (bump_id is None or g["bump_id"] == bump_id)
           and g.get("mean_speed_mps") and g.get("mean_peak_delta_g") and g["mean_peak_delta_g"] > 0 and g["total_mass_g"] > 0]
    out: dict[str, Any] = {"bump_id": bump_id, "points": len(pts), "per_mass": [], "overall": None,
                           "formula": None, "note": None}
    if not pts:
        out["note"] = "속도가 계산된 조건 평균이 없어 회귀를 할 수 없습니다."
        return out
    masses = sorted({round(m, 4) for _, m, _ in pts})
    for mass in masses:
        sub = [(v, d) for v, m, d in pts if round(m, 4) == mass]
        if len(sub) >= 2 and len({v for v, _ in sub}) >= 2:
            fit = linear_least_squares([[1.0, math.log(v)] for v, _ in sub], [math.log(d) for _, d in sub])
            if fit:
                (c, n), r2 = fit
                out["per_mass"].append({"mass_g": round(mass * 1000), "k": round(math.exp(c), 5), "n": round(n, 4),
                                        "r2": round(r2, 4), "points": len(sub)})
    if len(masses) >= 2 and len(pts) >= 4:
        fit = linear_least_squares([[1.0, math.log(v), math.log(m)] for v, m, _ in pts], [math.log(d) for *_, d in pts])
        if fit:
            (c, n, p), r2 = fit
            out["overall"] = {"k0": round(math.exp(c), 6), "n": round(n, 4), "p": round(p, 4), "r2": round(r2, 4)}
            out["formula"] = f"Δa = {math.exp(c):.4g} · v^{n:.3f} · m^{p:.3f}   (v: m/s, m: kg, Δa: g)"
    elif len(pts) >= 2 and len({v for v, _, _ in pts}) >= 2:
        fit = linear_least_squares([[1.0, math.log(v)] for v, _, _ in pts], [math.log(d) for *_, d in pts])
        if fit:
            (c, n), r2 = fit
            out["overall"] = {"k0": round(math.exp(c), 6), "n": round(n, 4), "p": 0.0, "r2": round(r2, 4)}
            out["formula"] = f"Δa = {math.exp(c):.4g} · v^{n:.3f}   (질량 1단계만 있어 p = 0으로 둠)"
            out["note"] = "질량이 2단계 이상이어야 질량 지수 p를 구할 수 있습니다."
            if len(pts) == 2:
                out["note"] += " 속도 조건이 2개뿐이라 곡선이 두 점을 그대로 지납니다(R² = 1, 신뢰도 낮음). 속도를 4~5단계로 늘리세요."
    else:
        out["note"] = "서로 다른 속도의 조건 평균이 2개 이상 필요합니다."
    return out


def predict_vmax(fit: dict[str, Any] | None, a_limit_g: float, mass_g: float) -> float | None:
    """v_max = ((a_lim − 1) / (k₀ · m^p))^(1/n)."""
    if not fit or not fit.get("overall") or a_limit_g <= 1 or mass_g <= 0:
        return None
    o = fit["overall"]
    if o["n"] <= 0 or o["k0"] <= 0:
        return None
    base = (a_limit_g - 1.0) / (o["k0"] * (mass_g / 1000.0) ** o["p"])
    return round(base ** (1.0 / o["n"]), 4) if base > 0 else None


# --------------------------------------------------------------------------- 모의 데이터
def simulated_speed_mps(output_mode: str, output_value: float, mass_g: float, base_mass_g: float) -> float:
    """모의 모드 전용 출력→속도 모델(실제 측정값 아님)."""
    duty = output_value if output_mode == "duty" else (output_value / 2 + 50 if output_value > 0 else 0)
    v = max(0.0, 0.95 * (duty - 28) / 72)
    extra = max(0.0, (mass_g - base_mass_g) / 1000.0)
    return round(v * max(0.4, 1 - 0.35 * extra), 4)


def synthesize_run(
    *, output_mode: str, output_value: float, mass_g: float, base_mass_g: float,
    wheelbase_mm: float, bump_radius: float | None, wheel_radius_mm: float | None,
    pre_roll_s: float = 1.0, rate_hz: float = 1000.0, seed: int | None = None,
) -> dict[str, list[float]]:
    """그래프·저장 흐름 확인용 합성 기록. 결과는 항상 simulated로 표시해 연구 데이터와 섞지 않습니다."""
    rng = random.Random(seed)
    v = max(0.08, simulated_speed_mps(output_mode, output_value, mass_g, base_mass_g) * rng.uniform(0.96, 1.04))
    L = wheelbase_mm / 1000.0
    t_front = pre_roll_s + 0.25 + 0.6 / v
    t_rear = t_front + L / v
    r_eff = effective_radius_m(bump_radius, wheel_radius_mm) or 0.08
    amp = 0.9 * (v / 0.5) ** 1.6 * (base_mass_g / max(mass_g, 1)) ** 0.15 * (0.08 / r_eff) ** 0.4
    pi = v * v / (G * r_eff)
    end = t_rear + 0.9
    cols: dict[str, list[float]] = {k: [] for k in ("t_s", "ax_g", "ay_g", "az_g", "gx_dps", "gy_dps", "gz_dps", "vertical_g", "dynamic_g")}
    steps = int(end * rate_hz)
    for i in range(steps):
        t = i / rate_hz
        moving = pre_roll_s <= t <= end - 0.4
        vert = 1.0 + rng.gauss(0, 0.012)
        if moving:
            vert += 0.04 * math.sin(2 * math.pi * 38 * t) + rng.gauss(0, 0.025)
        for tc, scale in ((t_front, 1.0), (t_rear, 0.85)):
            dtc = t - tc
            vert += amp * scale * math.exp(-(dtc / 0.004) ** 2)
            vert -= 0.35 * amp * scale * math.exp(-((dtc - 0.012) / 0.006) ** 2)
        if pi > 1.0:
            air = min(0.08, 0.05 * (pi - 1.0) + 0.02)
            if t_front + 0.01 < t < t_front + 0.01 + air:
                vert = 0.05 + rng.gauss(0, 0.02)
            land = t_front + 0.01 + air
            vert += 1.6 * amp * math.exp(-((t - land) / 0.004) ** 2)
        ax = 0.02 * math.sin(2 * math.pi * 11 * t) * moving + rng.gauss(0, 0.01)
        ay = rng.gauss(0, 0.01)
        cols["t_s"].append(round(t, 5))
        cols["ax_g"].append(round(ax, 4)); cols["ay_g"].append(round(ay, 4)); cols["az_g"].append(round(vert, 4))
        cols["gx_dps"].append(round(rng.gauss(0, 0.5), 2)); cols["gy_dps"].append(round(rng.gauss(0, 0.5), 2)); cols["gz_dps"].append(round(rng.gauss(0, 0.3), 2))
        cols["vertical_g"].append(round(vert, 4))
        cols["dynamic_g"].append(round(math.sqrt(ax * ax + ay * ay + (vert - 1) ** 2), 4))
    return cols


def decimate_columns(columns: dict[str, list[float]], points: int, key: str = "vertical_g") -> dict[str, list[float]]:
    """그래프용 축소: 구간마다 |key−1|이 가장 큰 표본을 남겨 피크를 보존합니다."""
    n = len(columns.get("t_s", []))
    if n <= points:
        return {k: list(vs) for k, vs in columns.items()}
    step = n / points
    picks = []
    ref = columns[key]
    for i in range(points):
        a, b = int(i * step), max(int((i + 1) * step), int(i * step) + 1)
        picks.append(max(range(a, min(b, n)), key=lambda j: abs(ref[j] - 1.0)))
    return {k: [vs[j] for j in picks] for k, vs in columns.items()}
