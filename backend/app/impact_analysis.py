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
MIN_SPEED_MPS = 0.03
MAX_SPEED_MPS = 4.0


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
) -> dict[str, Any]:
    t_all = list(columns.get("t_s") or [])
    v_all = list(columns.get("vertical_g") or [])
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
        "peak_count": 0, "flags": flags,
        "params": {"threshold_delta_g": threshold_delta_g, "min_gap_s": min_gap_s, "jump_g": jump_g,
                   "wheelbase_mm": wheelbase_mm, "wheel_radius_mm": wheel_radius_mm, "bump_radius_mm": bump_radius},
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

    peaks = find_peaks(t, v, threshold_delta_g, min_gap_s)
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
    pair = pick_axle_pair([p for p in peaks if p not in landing], wheelbase_mm)
    if pair is None and landing:
        # 착지와 뒷바퀴 접지가 겹친 경우: 착지 피크를 포함해 다시 짝을 찾되 표시해 둡니다.
        pair = pick_axle_pair(peaks, wheelbase_mm)
        if pair is not None:
            flags.append("착지 피크가 바퀴 피크와 겹쳐 짝에 포함했습니다 – 속도 오차 가능(영상 실측과 비교 권장).")
    if pair is None:
        flags.append("앞·뒤 바퀴 피크 한 쌍을 찾지 못했습니다(검출 기준을 낮추거나 원자료를 확인).")
    else:
        a, b = pair
        dt = b.t_s - a.t_s
        result.update(front_t_s=round(a.t_s, 4), front_g=round(a.value_g, 4),
                      rear_t_s=round(b.t_s, 4), rear_g=round(b.value_g, 4), axle_dt_s=round(dt, 4))
        if wheelbase_mm:
            result["speed_mps"] = round((wheelbase_mm / 1000.0) / dt, 4)
        else:
            flags.append("축간거리가 없어 속도를 계산하지 않았습니다(설정에서 입력).")
    # 지면 이탈 판정: 첫 피크 직전 ~ 마지막 피크 이후 구간의 최솟값과 공중 구간 유무
    if peaks:
        seg = [v[i] for i in range(len(v)) if peaks[0].t_s - 0.05 <= t[i] <= peaks[-1].t_s + 0.3]
        if seg:
            result["min_g"] = round(min(seg), 4)
            result["jump"] = bool(segments) or min(seg) < jump_g
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


def aggregate(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple, list[dict[str, Any]]] = {}
    for run in runs:
        if run.get("excluded"):
            continue
        a = run.get("analysis") or {}
        if a.get("peak_delta_g") is None:
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
