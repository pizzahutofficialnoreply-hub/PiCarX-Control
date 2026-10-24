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


def _near_whole_g(x: float) -> bool:
    """가속도 원시값의 아래 12비트가 0인 값(정확히 0, 1, 2, 4, 8 g 근처)인지. I²C 전송 오류의 흔적입니다."""
    return abs(x - round(x)) < 0.0006


def remove_glitches(columns: dict[str, list[float]]) -> tuple[dict[str, list[float]], int]:
    """I²C 통신 오류로 깨진 표본(한 점만 튀는 값)을 앞뒤 값의 평균으로 바꿉니다.

    깨진 표본의 특징(실제 기록에서 확인):
      - 가속도 한 축이 정확히 0 / 1 / 2 / 4 / 8 g (원시값의 아래쪽 비트가 모두 0)이면서 앞뒤 표본과 0.25 g 넘게 다르거나
      - 자이로 세 축이 완전히 같은 값(원시값 0)
    실제 충격은 여러 표본(수 ms)에 걸쳐 오르내리므로, 한 점만 튀고 위 특징이 있는 값만 고칩니다.
    """
    t = columns.get("t_s") or []
    n = len(t)
    keys = [k for k in ("ax_g", "ay_g", "az_g", "gx_dps", "gy_dps", "gz_dps", "vertical_g", "dynamic_g") if len(columns.get(k) or []) == n]
    if n < 5 or not all(k in keys for k in ("ax_g", "ay_g", "az_g", "vertical_g")):
        return columns, 0
    v = columns["vertical_g"]
    bad = []
    for i in range(1, n - 1):
        gyro_zero = all(k in keys for k in ("gx_dps", "gy_dps", "gz_dps")) and columns["gx_dps"][i] == columns["gy_dps"][i] == columns["gz_dps"][i]
        jump = abs(v[i] - (v[i - 1] + v[i + 1]) / 2) > 0.25
        whole = any(_near_whole_g(columns[k][i]) for k in ("ax_g", "ay_g", "az_g"))
        if gyro_zero or (whole and jump):
            bad.append(i)
    if not bad:
        return columns, 0
    out = {k: list(vs) for k, vs in columns.items()}
    badset = set(bad)
    for i in bad:
        lo = i - 1
        while lo > 0 and lo in badset:
            lo -= 1
        hi = i + 1
        while hi < n - 1 and hi in badset:
            hi += 1
        w = (t[i] - t[lo]) / (t[hi] - t[lo]) if t[hi] > t[lo] else 0.5
        for k in keys:
            out[k][i] = round(columns[k][lo] + (columns[k][hi] - columns[k][lo]) * w, 4)
    return out, len(bad)


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
    still = _quiet_window(columns, t, still, still_s)
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


def _quiet_window(columns: dict[str, Sequence[float]], t: Sequence[float], still: list[int], still_s: float) -> list[int]:
    """자이로 영점 구간: 기본은 처음 still_s초. 그 구간이 흔들렸으면(측정 전 조향 서보 정렬 등, 자이로 표준편차 0.5 °/s 초과)
    처음 2초 안에서 가장 조용한 still_s초 구간으로 바꿉니다. 처음 구간이 조용하면(10/18 기록 등) 예전과 똑같습니다."""
    axes = ("gx_dps", "gy_dps", "gz_dps")

    def noise(ids: list[int]) -> float:
        out = 0.0
        for k in axes:
            vals = [columns[k][i] for i in ids]
            m = sum(vals) / len(vals)
            out = max(out, (sum((x - m) ** 2 for x in vals) / len(vals)) ** 0.5)
        return out
    if noise(still) <= 0.5:
        return still
    best, best_n = still, noise(still)
    start = t[0]
    while start + still_s <= t[0] + 2.0:
        ids = [i for i, x in enumerate(t) if start <= x < start + still_s]
        if len(ids) >= 10:
            nz = noise(ids)
            if nz < best_n:
                best, best_n = ids, nz
        start += 0.1
    return best


def corrupt_bursts(v: Sequence[float], swing_g: float = 3.0) -> int:
    """센서 버퍼 칸 밀림 등으로 값이 위아래로 크게 번갈아 튀는 횟수(연속 두 표본이 반대 방향으로 각각 swing_g 넘게 변함).
    실제 충격은 몇 ms에 걸쳐 오르내려 이런 모양이 거의 없습니다(10/18 기록: 깨진 표본을 고친 뒤 0~2회)."""
    n = 0
    for i in range(1, len(v) - 1):
        a, b = v[i] - v[i - 1], v[i + 1] - v[i]
        if a * b < 0 and abs(a) > swing_g and abs(b) > swing_g:
            n += 1
    return n


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


def pitch_events(t: Sequence[float], pitch: Sequence[float] | None, wheelbase_mm: float | None,
                 narrow: bool = True, around: tuple[float, float] | None = None) -> dict[str, float] | None:
    """차의 앞뒤 기울기(피치)로 네 순간을 찾습니다: 앞바퀴 오름(FU)·내림(FD), 뒷바퀴 오름(RU)·내림(RD).

    좁은 방지턱(바퀴가 턱을 지나는 거리 < 축간거리, 예: 폭 38 mm 반원)에서
      앞바퀴가 오르면 앞이 들려 기울기가 한쪽(+)으로 커졌다가, 앞바퀴가 내려오면 0으로 돌아오고(FD),
      뒷바퀴가 오르면 반대쪽(−)으로 기울었다가, 뒷바퀴가 내려오면 다시 0(RD)이 됩니다.
    넓은 방지턱(폭 > 축간거리)은 + 구간이 FU→RU, − 구간이 FD→RD 입니다.
    각 순간은 기울기가 그 구간 최대치의 20%를 넘는/내려가는 시각으로 잡습니다(오름·내림에 같은 기준을 써서
    시각 차이는 정확). 두 구간의 모양이 서로 맞지 않으면 None.
    """
    if pitch is None or not wheelbase_mm or len(t) < 50:
        return None
    n = len(t)
    rng = [i for i in range(n) if around is None or around[0] <= t[i] <= around[1]]
    if len(rng) < 20:
        return None
    ia_ = max(rng, key=lambda i: abs(pitch[i]))
    # 방지턱 전 평평한 구간의 기울기를 0점으로
    pre = sorted(pitch[i] for i in range(n) if t[ia_] - 0.7 <= t[i] <= t[ia_] - 0.3) or [0.0]
    base = pre[len(pre) // 2]
    q0 = [x - base for x in pitch]
    # 가장 먼저 나타난 큰 기울기 구간이 앞바퀴 쪽
    big = max(abs(q0[i]) for i in rng)
    if big < 2.5:
        return None
    first = next(i for i in rng if abs(q0[i]) >= 0.6 * big)
    sgn = 1.0 if q0[first] > 0 else -1.0
    q = [sgn * x for x in q0]
    # 첫 구간(+)의 꼭대기
    a = first
    while a + 1 < n and q[a + 1] >= 0.3 * big:
        a += 1
    pa = max(range(first, a + 1), key=lambda i: q[i])
    A = q[pa]
    lo = 0.2 * A
    i0 = pa
    while i0 > 0 and q[i0 - 1] >= lo:
        i0 -= 1
    i1 = pa
    while i1 < n - 1 and q[i1] >= lo:
        i1 += 1
    if i0 == 0 or i1 >= n - 1:
        return None
    dur = t[i1] - t[i0]
    # 두 번째 구간(−): 첫 구간이 끝난 뒤 (첫 구간 길이×3 + 0.3초) 안의 가장 낮은 곳
    lim = t[i1] + 3 * dur + 0.3
    cand = [i for i in range(i1, n) if t[i] <= lim]
    if not cand:
        return None
    pb = min(cand, key=lambda i: q[i])
    B = -q[pb]
    if B < 0.3 * A:
        return None
    lo2 = 0.2 * B
    j0 = pb
    while j0 > i1 and q[j0 - 1] <= -lo2:
        j0 -= 1
    j1 = pb
    while j1 < n - 1 and q[j1] <= -lo2:
        j1 += 1
    if j1 >= n - 1:
        return None
    s1, e1, s2, e2 = t[i0], t[i1], t[j0], t[j1]
    if narrow:
        ev = {"FU": s1, "FD": e1, "RU": s2, "RD": e2}
    else:
        ev = {"FU": s1, "RU": e1, "FD": s2, "RD": e2}
    a1, a2 = ev["RU"] - ev["FU"], ev["RD"] - ev["FD"]
    c1, c2 = ev["FD"] - ev["FU"], ev["RD"] - ev["RU"]
    if a1 <= 0 or a2 <= 0 or c1 <= 0 or c2 <= 0:
        return None
    if abs(a1 / a2 - 1) > 0.35 or abs(c1 / c2 - 1) > 0.5:
        return None
    ev.update(front_deg=round(A, 2), rear_deg=round(B, 2), axle_dt_s=(a1 + a2) / 2, cross_s=(c1 + c2) / 2,
              speed_mps=(wheelbase_mm / 1000.0) / ((a1 + a2) / 2))
    return ev


ANALYSIS_VERSION = 9  # 분석 방식이 바뀌면 올려서 저장된 시행을 자동으로 다시 분석

EVENT_LABEL = {"FU": "앞바퀴 오름", "FD": "앞바퀴 내림", "RU": "뒷바퀴 오름", "RD": "뒷바퀴 내림"}


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


def peak_preserving_filter(t: Sequence[float], v: Sequence[float], cutoff_hz: float, k: float = 4.0) -> list[float]:
    """진동만 거르고 충격 봉우리는 원래 높이 그대로 남기는 필터.

    1) 저역 통과 필터로 주행 진동을 거른 뒤, 2) 거른 신호에서 진동(σ)보다 k배 이상 솟은 충격 자리를 찾고,
    3) 그 주변(±w)은 거르기 전 원래 값으로 되돌립니다(가장자리는 부드럽게 이어 붙임).
    그래서 진동 지그재그는 줄지만 방지턱 충격의 최대값은 거의 깎이지 않습니다.
    """
    n = min(len(t), len(v))
    f = lowpass(t, v, cutoff_hz)
    if not cutoff_hz or n < 8 or f == list(v[:n]):
        return f
    sigma = _robust_sigma([x - 1.0 for x in f])
    peaks = find_peaks(t[:n], f, max(0.06, k * sigma), 0.03)
    if not peaks:
        return f
    w = 0.01  # 봉우리 앞뒤 10 ms만 원래 값으로(그 밖 10 ms는 부드럽게 이어 붙임) – 옆의 진동 마루까지 되살리지 않게
    weight = [0.0] * n
    for p in peaks:
        i = p.index
        j = i
        while j >= 0 and t[i] - t[j] <= 2 * w:
            d = t[i] - t[j]
            weight[j] = max(weight[j], 1.0 if d <= w else 1.0 - (d - w) / w)
            j -= 1
        j = i + 1
        while j < n and t[j] - t[i] <= 2 * w:
            d = t[j] - t[i]
            weight[j] = max(weight[j], 1.0 if d <= w else 1.0 - (d - w) / w)
            j += 1
    return [weight[i] * v[i] + (1.0 - weight[i]) * f[i] for i in range(n)]


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


def entry_sin(bump_radius_mm: float | None, bump_height_mm: float | None, wheel_radius_mm: float | None) -> float | None:
    """바퀴 중심이 방지턱(원호)에 오르기 시작할 때 진행 방향이 꺾이는 각 α의 sin.

    바퀴 중심은 반지름 R+r인 원(방지턱 원호를 바퀴 반지름만큼 바깥으로 넓힌 원)을 따라 움직이고,
    평지 높이(r)에서 이 원과 만나는 점에서 갑자기 위로 꺾입니다.
        sin α = √((R+r)² − (R−h+r)²) / (R+r)
    수평 속도 v로 들어오면 위쪽 속도가 순간적으로 v·sin α 생기므로, 충격량(이론) = m·v·sin α 입니다.
    """
    if not bump_radius_mm or not bump_height_mm or wheel_radius_mm is None:
        return None
    big = bump_radius_mm + wheel_radius_mm
    low = bump_radius_mm - bump_height_mm + wheel_radius_mm
    if big <= 0 or low >= big:
        return None
    return (big * big - low * low) ** 0.5 / big


def _pulse_metrics(t: Sequence[float], v: Sequence[float], result: dict[str, Any], mass_g: float | None,
                   bump_radius: float | None, bump_height_mm: float | None, wheel_radius_mm: float | None) -> dict[str, Any]:
    """앞·뒤 바퀴 충격 펄스의 넓이(충격량)와 길이(Δt), 그리고 이론값.

    - Δv_z = g·∫(a−1)dt : 펄스 동안 생긴 위쪽 속도 변화(m/s). 넓이는 저역 필터로 거의 바뀌지 않습니다.
    - 충격량 I = m·Δv_z (N·s), 평균 충격력 = I / Δt
    - 펄스 구간: 피크에서 양옆으로 (a−1)이 피크 높이의 10% 아래로 떨어질 때까지(최대 ±60 ms)
    - 앞바퀴 + 뒷바퀴 Δv_z 합은 센서 위치와 상관없이 차 전체가 받은 위쪽 속도 변화(이론 v·sin α)와 비교합니다.
    - 원운동: 바퀴가 원호 꼭대기를 지날 때 a = 1 − v²/(g·R_eff) (g 단위) → 꼭대기 최솟값과 비교
    """
    out: dict[str, Any] = {"impulse_front_ns": None, "impulse_rear_ns": None, "impulse_ns": None, "dvz_mps": None,
                           "pulse_s": None, "pulse_rear_s": None, "avg_force_n": None, "momentum_nsx": None,
                           "entry_sin": None, "dvz_theory_mps": None, "impulse_theory_ns": None,
                           "crest_min_g": None, "crest_theory_g": None}
    n = len(t)
    if n < 5 or result.get("front_t_s") is None:
        return out

    def pulse(tc: float) -> tuple[float, float] | None:
        i = min(range(n), key=lambda k: abs(t[k] - tc))
        top = v[i] - 1.0
        if top <= 0:
            return None
        lim = 0.1 * top
        a = i
        while a > 0 and v[a - 1] - 1.0 > lim and tc - t[a - 1] <= 0.06:
            a -= 1
        b = i
        while b < n - 1 and v[b + 1] - 1.0 > lim and t[b + 1] - tc <= 0.06:
            b += 1
        area = 0.0
        for k in range(a, b):
            area += ((v[k] - 1.0) + (v[k + 1] - 1.0)) / 2 * (t[k + 1] - t[k])
        width = t[b] - t[a]
        return area, width

    pf = pulse(result["front_t_s"])
    pr = pulse(result["rear_t_s"]) if result.get("rear_t_s") is not None else None
    m = (mass_g or 0) / 1000.0
    if pf:
        out["pulse_s"] = round(pf[1], 5)
        if m:
            out["impulse_front_ns"] = round(m * G * pf[0], 6)
            if pf[1] > 0:
                out["avg_force_n"] = round(m * G * pf[0] / pf[1], 4)
    if pr:
        out["pulse_rear_s"] = round(pr[1], 5)
        if m:
            out["impulse_rear_ns"] = round(m * G * pr[0], 6)
    if pf and pr:
        dv = G * (pf[0] + pr[0])
        out["dvz_mps"] = round(dv, 5)
        if m:
            out["impulse_ns"] = round(m * dv, 6)
    sp = result.get("speed_mps")
    s_in = entry_sin(bump_radius, bump_height_mm, wheel_radius_mm)
    out["entry_sin"] = None if s_in is None else round(s_in, 4)
    if sp:
        if m:
            out["momentum_nsx"] = round(m * sp, 5)
        if s_in is not None:
            out["dvz_theory_mps"] = round(sp * s_in, 5)
            if m:
                out["impulse_theory_ns"] = round(m * sp * s_in, 6)
    # 꼭대기(원운동) 구간: 앞바퀴 피크 직후 ~ 뒷바퀴 피크 직전(뒷바퀴가 없으면 앞바퀴 뒤 0.15 s)
    lo_t = result["front_t_s"] + 0.01
    hi_t = (result["rear_t_s"] - 0.01) if result.get("rear_t_s") is not None else lo_t + 0.15
    seg = [v[k] for k in range(n) if lo_t <= t[k] <= hi_t]
    if seg:
        out["crest_min_g"] = round(min(seg), 4)
    r_eff = effective_radius_m(bump_radius, wheel_radius_mm)
    if r_eff and sp:
        out["crest_theory_g"] = round(1.0 - sp * sp / (G * r_eff), 4)
    return out


def _restore_peak_heights(result: dict[str, Any], t: Sequence[float], raw: Sequence[float],
                          lo: float | None, hi: float | None, w: float = 0.01) -> None:
    """진동을 거르면 충격 봉우리도 낮아지므로, 찾은 피크 시각 ±10 ms 안의 '거르기 전' 최댓값으로 높이를 되돌립니다.

    피크 시각(속도 계산에 쓰는 앞·뒤 바퀴 간격)은 거른 신호의 값을 그대로 둡니다.
    """
    n = min(len(t), len(raw))

    def peak_at(tc: float | None) -> float | None:
        if tc is None:
            return None
        vals = [raw[i] for i in range(n) if abs(t[i] - tc) <= w and (lo is None or t[i] >= lo) and (hi is None or t[i] <= hi)]
        return max(vals) if vals else None

    for tk, gk in (("front_t_s", "front_g"), ("rear_t_s", "rear_g"), ("landing_t_s", "landing_g"), ("peak_t_s", "peak_g")):
        g = peak_at(result.get(tk))
        if g is not None and result.get(gk) is not None:
            result[gk] = round(max(g, result[gk]), 4)
    # 가장 큰 충격은 고른 바퀴 피크들 중 큰 값과도 비교(거른 신호의 최댓값 위치가 조금 다를 수 있음)
    cands = [result.get(k) for k in ("peak_g", "front_g", "rear_g", "landing_g") if result.get(k) is not None]
    if cands:
        result["peak_g"] = round(max(cands), 4)
        result["peak_delta_g"] = round(result["peak_g"] - 1.0, 4)
    for c in result.get("candidates") or []:
        g = peak_at(c.get("t_s"))
        if g is not None:
            c["g"] = round(max(g, c["g"]), 4)
    ap = result.get("auto_pair")
    if ap:
        for tk, gk in (("front_t_s", "front_g"), ("rear_t_s", "rear_g")):
            g = peak_at(ap.get(tk))
            if g is not None:
                ap[gk] = round(max(g, ap[gk]), 4)
    result["params"]["peak_keep"] = True


# --------------------------------------------------------------------------- 단계별 충격·충격량, 보고서 지표
IMPACT_METRICS = {
    "up": "올라갈 때 충격(앞·뒤 바퀴 평균)",
    "front_up": "앞바퀴 올라갈 때 충격",
    "peak": "가장 큰 충격",
}


def _pulse_area(t: Sequence[float], v: Sequence[float], tc: float, lo: float, hi: float) -> tuple[float, float, float] | None:
    """tc 근처(±3 ms) 꼭짓점에서 양옆으로 (a−1)이 꼭짓점 높이의 10% 아래로 떨어질 때까지(단, lo~hi 안) 넓이를 잽니다.

    (넓이 g·s, 펄스 길이 s, 꼭짓점 Δa g)를 돌려줍니다. 이웃 단계와 겹치지 않게 lo~hi는 이웃 꼭짓점과의 가운데로 자릅니다.
    """
    n = len(t)
    idx = [k for k in range(n) if abs(t[k] - tc) <= 0.003] or [min(range(n), key=lambda k: abs(t[k] - tc))]
    i = max(idx, key=lambda k: v[k])
    top = v[i] - 1.0
    if top <= 0:
        return None
    lim = 0.1 * top
    a = i
    while a > 0 and v[a - 1] - 1.0 > lim and t[a - 1] >= lo:
        a -= 1
    b = i
    while b < n - 1 and v[b + 1] - 1.0 > lim and t[b + 1] <= hi:
        b += 1
    # 양 끝은 기준 아래로 떨어진 바로 옆 표본까지 넣습니다(표본 하나짜리 짧은 충격도 넓이가 0이 되지 않게). 음수는 0으로.
    a, b = max(0, a - 1), min(n - 1, b + 1)
    area = 0.0
    for k in range(a, b):
        area += (max(0.0, v[k] - 1.0) + max(0.0, v[k + 1] - 1.0)) / 2 * (t[k + 1] - t[k])
    return area, t[b] - t[a], top


def event_metrics(t: Sequence[float], v: Sequence[float], result: dict[str, Any], mass_g: float | None) -> dict[str, Any]:
    """네 단계(앞 오름·앞 내림·뒤 오름·뒤 내림)마다 Δa와 충격량, 그리고 보고서 지표.

    - 단계 충격량 I = m·g·∫(a−1)dt (N·s), 펄스 구간은 꼭짓점 높이의 10%까지(이웃 단계와 겹치지 않게 자름)
    - 올라갈 때 충격(up_delta_g) = (앞 오름 Δa + 뒤 오름 Δa) / 2 – 바퀴가 방지턱 모서리에 부딪히는 충격이라 속도에 따라 커짐
    - 올라갈 때 충격량(up_impulse_ns) = 앞 오름 + 뒤 오름 충격량(차 전체가 받은 위쪽 운동량, 이론 m·v·sin α와 비교)
    - 내려올 때 충격은 방지턱 높이만큼 떨어지는 낙하라 속도와 관계가 약해, 따로 기록만 합니다.
    """
    out: dict[str, Any] = {"event_delta_g": {}, "event_impulse_ns": {}, "event_pulse_s": {}, "up_delta_g": None,
                           "front_up_delta_g": None, "rear_up_delta_g": None, "up_impulse_ns": None,
                           "front_up_impulse_ns": None, "rear_up_impulse_ns": None}
    if len(t) < 5:
        return out
    m = (mass_g or 0) / 1000.0
    ev = sorted(result.get("events") or [], key=lambda e: e["peak_t_s"])
    for j, e in enumerate(ev):
        tc = e["peak_t_s"]
        lo = max(tc - 0.06, (ev[j - 1]["peak_t_s"] + tc) / 2) if j > 0 else tc - 0.06
        hi = min(tc + 0.06, (tc + ev[j + 1]["peak_t_s"]) / 2) if j + 1 < len(ev) else tc + 0.06
        out["event_delta_g"][e["kind"]] = round(e["g"] - 1.0, 4)
        p = _pulse_area(t, v, tc, lo, hi)
        if p is None:
            continue
        out["event_pulse_s"][e["kind"]] = round(p[1], 5)
        if m:
            out["event_impulse_ns"][e["kind"]] = round(m * G * p[0], 6)
    d, imp = out["event_delta_g"], out["event_impulse_ns"]
    fu, ru = d.get("FU"), d.get("RU")
    fi, ri = imp.get("FU"), imp.get("RU")
    if fu is None and ru is None and result.get("pair_source") == "pitch" and result.get("front_g") is not None:
        # 네 단계를 못 찾았지만 기울기로 앞·뒤 바퀴가 '오른' 시각은 찾은 경우: 그 피크가 올라갈 때 충격입니다.
        fu = round(result["front_g"] - 1.0, 4)
        ru = None if result.get("rear_g") is None else round(result["rear_g"] - 1.0, 4)
        fi, ri = result.get("impulse_front_ns"), result.get("impulse_rear_ns")
    out["front_up_delta_g"], out["rear_up_delta_g"] = fu, ru
    out["front_up_impulse_ns"], out["rear_up_impulse_ns"] = fi, ri
    if fu is not None and ru is not None:
        out["up_delta_g"] = round((fu + ru) / 2, 4)
    if fi is not None and ri is not None:
        out["up_impulse_ns"] = round(fi + ri, 6)
    return out


def metric_delta(analysis: dict[str, Any] | None, metric: str = "peak") -> float | None:
    """보고서 지표로 쓰는 회차 하나의 Δa(g)."""
    a = analysis or {}
    if metric == "up":
        return a.get("up_delta_g")
    if metric == "front_up":
        return a.get("front_up_delta_g")
    return a.get("peak_delta_g")


def run_metric(run: dict[str, Any], metric: str = "peak") -> float | None:
    """회차의 지표 값. 모의 데이터는 네 단계를 만들지 않으므로 앞·뒤 바퀴 피크(오름)로 대신합니다."""
    a = run.get("analysis") or {}
    d = metric_delta(a, metric)
    if d is None and run.get("simulated") and metric in ("up", "front_up") and a.get("front_g") is not None:
        f = a["front_g"] - 1.0
        if metric == "front_up" or a.get("rear_g") is None:
            return round(f, 4)
        return round((f + a["rear_g"] - 1.0) / 2, 4)
    return d


def heading_out(run: dict[str, Any], limit_deg: float | None) -> bool:
    """차가 비스듬히 지나간 회차: 방지턱 도착 때 방향이나 주행 중 최대 방향이 허용 각도를 넘음."""
    if not limit_deg:
        return False
    hb = (run.get("motor") or {}).get("heading_at_bump_deg")
    hm = run.get("heading_max_deg")
    return (hb is not None and abs(hb) > limit_deg) or (hm is not None and abs(hm) > limit_deg)


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
    columns, glitches = remove_glitches(columns)
    t_all = list(columns.get("t_s") or [])
    v_all = list(columns.get("vertical_g") or [])
    v_raw_all = list(v_all)
    if filter_hz:
        # 시각·짝 찾기는 진동을 거른 값으로 하고, 피크 높이는 아래에서 원래 기록으로 되돌립니다(피크는 깎지 않음).
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
        "glitches_fixed": glitches, "max_gap_ms": None, "version": ANALYSIS_VERSION, "events": [],
        "params": {"threshold_delta_g": threshold_delta_g, "min_gap_s": min_gap_s, "jump_g": jump_g,
                   "wheelbase_mm": wheelbase_mm, "wheel_radius_mm": wheel_radius_mm, "bump_radius_mm": bump_radius,
                   "filter_hz": filter_hz, "detect_level": detect_level, "peak_keep": True},
    }
    if len(v) < 3:
        flags.append("표본이 너무 적습니다.")
        return result
    if glitches:
        flags.append(f"센서 통신 오류로 깨진 표본 {glitches}개(한 점만 튀는 0·2·4·8 g 값)를 앞뒤 값으로 바꿨습니다.")
    bursts = corrupt_bursts(v_raw_all)
    result["corrupt_swings"] = bursts
    if bursts >= 5:
        # 값이 −7 g ↔ +8 g처럼 계속 번갈아 튀는 구간은 실제 충격이 아니라 센서 기록 오류입니다. 최댓값이 이 구간에서 나오면
        # Δa가 6~7 g로 잘못 잡히므로 이 시행은 집계에서 뺍니다.
        flags.append(f"센서 값이 위아래로 크게 번갈아 튀는 구간이 있습니다({bursts}번, 센서 버퍼 칸 밀림 등 기록 오류). "
                     "최대 충격 값을 믿을 수 없어 이 시행은 집계에서 빠집니다. 다시 측정하세요.")
    gaps = [(t[i + 1] - t[i]) for i in range(len(t) - 1)]
    if gaps:
        mg = max(gaps)
        result["max_gap_ms"] = round(mg * 1000, 1)
        if mg > 0.05:
            n_gap = sum(1 for g in gaps if g > 0.05)
            flags.append(f"센서 기록이 {n_gap}번 끊겼습니다(가장 긴 공백 {mg * 1000:.0f} ms). 공백 동안 충격을 놓쳤을 수 있습니다(I²C 읽기 오류).")

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
    # 최우선: 기울기로 네 순간(앞 오름·앞 내림·뒤 오름·뒤 내림)을 모두 찾으면 그걸로 속도와 각 충격을 정합니다.
    cross_mm = None
    if bump_radius and bump_height_mm and wheel_radius_mm is not None:
        big_r, low_r = bump_radius + wheel_radius_mm, bump_radius - bump_height_mm + wheel_radius_mm
        if low_r < big_r:
            cross_mm = 2 * (big_r * big_r - low_r * low_r) ** 0.5
    if cross_mm is None and bump_width_mm:
        cross_mm = bump_width_mm
    narrow = cross_mm is None or wheelbase_mm is None or cross_mm < wheelbase_mm
    ev4 = pitch_events(t, pitch, wheelbase_mm, narrow, span) if span else None
    if ev4 is not None and not (MIN_SPEED_MPS <= ev4["speed_mps"] <= MAX_SPEED_MPS):
        ev4 = None
    events: list[dict[str, Any]] = []
    if ev4 is not None:
        def win_peak(t0: float, before: float = 0.03, after: float = 0.06) -> Peak:
            idxs = [i for i in range(len(t)) if t0 - before <= t[i] <= t0 + after] or [min(range(len(t)), key=lambda i: abs(t[i] - t0))]
            i = max(idxs, key=lambda i: v[i])
            return Peak(i, t[i], v[i])
        order = sorted(("FU", "FD", "RU", "RD"), key=lambda k: ev4[k])
        for j, k in enumerate(order):
            # 이웃한 순간과 겹치지 않게 창을 가운데에서 자릅니다(빠르면 앞바퀴 내림과 뒷바퀴 오름이 가까움).
            before = 0.03 if j == 0 else min(0.03, (ev4[k] - ev4[order[j - 1]]) / 2)
            after = 0.06 if j == 3 else min(0.06, (ev4[order[j + 1]] - ev4[k]) / 2)
            pk = win_peak(ev4[k], before, after)
            events.append({"kind": k, "label": EVENT_LABEL[k], "t_s": round(ev4[k], 4), "peak_t_s": round(pk.t_s, 4), "g": round(pk.value_g, 4)})
        result["events"] = events
        result["pitch"] = {"front_up_t_s": round(ev4["FU"], 4), "rear_up_t_s": round(ev4["RU"], 4), "max_deg": max(ev4["front_deg"], ev4["rear_deg"]),
                           "front_down_t_s": round(ev4["FD"], 4), "rear_down_t_s": round(ev4["RD"], 4),
                           "cross_s": round(ev4["cross_s"], 4), "speed_mps": round(ev4["speed_mps"], 4)}
    by_pitch = None if ev4 is not None else (pitch_axle_times(t, pitch, wheelbase_mm, bump_width_mm, span) if span else None)
    if ev4 is not None:
        fu = next(e for e in events if e["kind"] == "FU"); ru = next(e for e in events if e["kind"] == "RU")
        pitch_pair = (Peak(0, fu["peak_t_s"], fu["g"]), Peak(0, ru["peak_t_s"], ru["g"]))
    elif by_pitch is not None:
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
        result["pair_source"] = "pitch4" if ev4 is not None else "pitch"
    elif pattern is not None:
        pa, pb, pc, pd, pv = pattern
        pair = (pa, pc)
        result["pair_source"] = "pattern"
        result["pattern"] = {"front_up_t_s": round(pa.t_s, 4), "front_down_t_s": round(pb.t_s, 4),
                             "rear_up_t_s": round(pc.t_s, 4), "rear_down_t_s": round(pd.t_s, 4),
                             "speed_mps": round(pv, 4),
                             "bump_pass_s": round(((pb.t_s - pa.t_s) + (pd.t_s - pc.t_s)) / 2, 4)}
        landing = [p for p in landing if p not in (pa, pb, pc, pd)]
        if landing:
            hardest = max(landing, key=lambda p: p.value_g)
            result.update(landing_t_s=round(hardest.t_s, 4), landing_g=round(hardest.value_g, 4))
        else:
            result["landing_t_s"] = result["landing_g"] = None
    else:
        pair = pick_axle_pair([p for p in peaks if p not in landing], wheelbase_mm)
        result["pair_source"] = "auto" if pair else None
    if pair is not None:
        result["auto_pair"] = {"front_t_s": round(pair[0].t_s, 4), "front_g": round(pair[0].value_g, 4),
                               "rear_t_s": round(pair[1].t_s, 4), "rear_g": round(pair[1].value_g, 4),
                               "source": result["pair_source"]}
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
    if pair is not None and result["pair_source"] not in ("manual", "pitch", "pitch4"):
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
    if bursts >= 5:
        pair = None
        result["bump_detected"] = False
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
            elif result["pair_source"] == "pitch4" and result.get("pitch"):
                # 기울기로 찾은 오름→오름, 내림→내림 간격의 평균(충격 피크의 위치보다 정확)
                result["speed_mps"] = result["pitch"]["speed_mps"]
                result["axle_dt_s"] = round(ev4["axle_dt_s"], 4)
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
    if landing and result.get("landing_g") is not None and not any("겹쳐" in f for f in flags):
        flags.append(f"지면 이탈 후 착지 피크 {result['landing_g']:.2f} g를 앞·뒤 바퀴 피크에서 제외했습니다.")

    # 이론식 ② 충격량·③ 원운동 비교용 값(바퀴가 턱에 오르는 순간의 충격 펄스)
    result.update(_pulse_metrics(t, v, result, mass_g, bump_radius, bump_height_mm, wheel_radius_mm))
    result.update(event_metrics(t, v, result, mass_g))
    if filter_hz:
        _restore_peak_heights(result, t_all, v_raw_all, window[0], window[1])

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


OUTLIER_METHODS = ("trim", "sigma2", "iqr", "none")


def quartile_inc(sorted_vals: list[float], q: float) -> float:
    """엑셀 QUARTILE.INC(=PERCENTILE.INC)과 같은 선형 보간 사분위수."""
    if not sorted_vals:
        raise ValueError("empty")
    pos = (len(sorted_vals) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def outlier_flags(values: list[float], method: str = "trim") -> tuple[list[bool], dict[str, Any]]:
    """값마다 '이상치로 뺄지' 여부와 판정에 쓴 기준을 돌려줍니다.

    - trim: 3개 이상이면 최댓값·최솟값을 하나씩 뺌(계획서의 원래 규칙)
    - sigma2: 평균 ± 2×표본표준편차(엑셀 AVERAGE, STDEV.S) 밖이면 뺌
    - iqr: Q1 − 1.5×IQR 보다 작거나 Q3 + 1.5×IQR 보다 크면 뺌(엑셀 QUARTILE.INC, 투키 울타리)
    - none: 빼지 않음
    """
    n = len(values)
    out = [False] * n
    info: dict[str, Any] = {"method": method}
    if method == "trim":
        if n >= 3:
            order = sorted(range(n), key=lambda i: values[i])
            out[order[0]] = out[order[-1]] = True
        return out, info
    if method == "sigma2" and n >= 3:
        m = sum(values) / n
        s = (sum((x - m) ** 2 for x in values) / (n - 1)) ** 0.5
        info.update(mean=round(m, 4), sd=round(s, 4), low=round(m - 2 * s, 4), high=round(m + 2 * s, 4))
        out = [abs(x - m) > 2 * s for x in values]
    elif method == "iqr" and n >= 4:
        sv = sorted(values)
        q1, q3 = quartile_inc(sv, 0.25), quartile_inc(sv, 0.75)
        d = q3 - q1
        info.update(q1=round(q1, 4), q3=round(q3, 4), iqr=round(d, 4), low=round(q1 - 1.5 * d, 4), high=round(q3 + 1.5 * d, 4))
        out = [x < q1 - 1.5 * d or x > q3 + 1.5 * d for x in values]
    if all(out):  # 전부 빠지는 일은 없게
        out = [False] * n
    return out, info


def _mean(values: Iterable[float]) -> float | None:
    values = [x for x in values if x is not None]
    return sum(values) / len(values) if values else None


def group_key(run: dict[str, Any]) -> tuple:
    c = run.get("condition", {})
    return (c.get("bump_id") or "-", round(float(c.get("total_mass_g") or 0) / 5) * 5,
            c.get("output_mode") or "pwm", int(round(float(c.get("output_value") or 0))), c.get("direction") or "forward")


def harmonize_pairs(runs: list[dict[str, Any]], tolerance: float = 0.3) -> list[dict[str, Any]]:
    """같은 조건 반복 회차끼리 앞·뒤 바퀴 간격(Δt)을 맞춥니다.

    같은 조건이면 통과 속도가 비슷해야 하므로, 회차들의 Δt 중앙값에서 30% 넘게 벗어난 회차는
    저장된 피크 후보 중 중앙값에 가장 가까운(±25%) 쌍으로 다시 고릅니다. 맞는 쌍이 없으면 그대로 둡니다.
    원본은 바꾸지 않고 고친 복사본 목록을 돌려줍니다(사람이 직접 지정한 쌍은 건드리지 않음).
    """
    groups: dict[tuple, list[dict[str, Any]]] = {}
    for run in runs:
        a = run.get("analysis") or {}
        if run.get("excluded") or not a.get("bump_detected") or a.get("pair_source") in ("manual", "pattern", "pitch", "pitch4") or not a.get("axle_dt_s"):
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
                    new["crest_theory_g"] = round(1.0 - new["pi"], 4)
                if a.get("entry_sin") is not None:
                    new["dvz_theory_mps"] = round(new["speed_mps"] * a["entry_sin"], 5)
                    if a.get("momentum_nsx") and a.get("speed_mps"):
                        m_kg = a["momentum_nsx"] / a["speed_mps"]
                        new["momentum_nsx"] = round(m_kg * new["speed_mps"], 5)
                        new["impulse_theory_ns"] = round(m_kg * new["speed_mps"] * a["entry_sin"], 6)
            new["flags"] = list(a.get("flags") or []) + [
                f"같은 조건 다른 회차들의 앞·뒤 바퀴 간격({med * 1000:.0f} ms)과 많이 달라({a['axle_dt_s'] * 1000:.0f} ms) 그 간격에 맞는 피크 쌍으로 다시 골랐습니다."]
            fixed[m["id"]] = {**m, "analysis": new}
    return [fixed.get(r.get("id"), r) for r in runs]


def aggregate(runs: list[dict[str, Any]], method: str = "trim", metric: str = "peak",
              heading_limit_deg: float | None = None) -> list[dict[str, Any]]:
    """조건별 평균. metric: 'peak'(가장 큰 충격), 'up'(올라갈 때 앞·뒤 평균), 'front_up'(앞바퀴 올라갈 때).

    heading_limit_deg를 주면 방지턱에 비스듬히(그 각도 넘게) 들어간 회차는 계산에서 뺍니다.
    """
    groups: dict[tuple, list[dict[str, Any]]] = {}
    skipped: dict[tuple, dict[str, list[str]]] = {}
    for run in runs:
        if run.get("excluded"):
            continue
        a = run.get("analysis") or {}
        if a.get("bump_detected") is False or a.get("peak_delta_g") is None:
            continue
        key = group_key(run)
        if heading_out(run, heading_limit_deg):
            skipped.setdefault(key, {}).setdefault("heading", []).append(run["id"])
            continue
        if run_metric(run, metric) is None:
            skipped.setdefault(key, {}).setdefault("no_metric", []).append(run["id"])
            continue
        groups.setdefault(key, []).append(run)
    rows = []
    for key, items in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2], kv[0][3])):
        deltas = [run_metric(r, metric) for r in items]
        flags, info = outlier_flags(deltas, method)
        kept_runs = [r for r, f in zip(items, flags) if not f]
        kept = [run_metric(r, metric) for r in kept_runs]
        dropped = [run_metric(r, metric) for r, f in zip(items, flags) if f]
        maxes = [r["analysis"]["peak_delta_g"] for r in kept_runs if r["analysis"].get("peak_delta_g") is not None]
        up_imps = [r["analysis"].get("up_impulse_ns") for r in kept_runs if r["analysis"].get("up_impulse_ns")]
        ev_imp: dict[str, float] = {}
        for kind in ("FU", "FD", "RU", "RD"):
            vals = [(r["analysis"].get("event_impulse_ns") or {}).get(kind) for r in kept_runs]
            vals = [x for x in vals if x is not None]
            if vals:
                ev_imp[kind] = round(sum(vals) / len(vals), 6)
        speeds = [r["analysis"].get("speed_mps") for r in kept_runs if r["analysis"].get("speed_mps")]
        pis = [r["analysis"].get("pi") for r in kept_runs if r["analysis"].get("pi")]
        imps = [r["analysis"].get("impulse_ns") for r in kept_runs if r["analysis"].get("impulse_ns")]
        dvzs = [r["analysis"].get("dvz_mps") for r in kept_runs if r["analysis"].get("dvz_mps")]
        crests = [r["analysis"].get("crest_min_g") for r in kept_runs if r["analysis"].get("crest_min_g") is not None]
        favg = [r["analysis"].get("avg_force_n") for r in kept_runs if r["analysis"].get("avg_force_n")]
        ev_mean: dict[str, float] = {}
        ev_n: dict[str, int] = {}
        for kind in ("FU", "FD", "RU", "RD"):
            vals = [e["g"] - 1.0 for r in kept_runs for e in (r["analysis"].get("events") or []) if e.get("kind") == kind]
            if vals:
                ev_mean[kind] = round(sum(vals) / len(vals), 4)
                ev_n[kind] = len(vals)
        pulses = [r["analysis"].get("pulse_s") for r in kept_runs if r["analysis"].get("pulse_s")]
        mean_delta = _mean(kept)
        sd_delta = (sum((x - mean_delta) ** 2 for x in kept) / (len(kept) - 1)) ** 0.5 if len(kept) > 1 and mean_delta is not None else None
        mass_g = key[1]
        rows.append({
            "bump_id": key[0], "total_mass_g": mass_g, "output_mode": key[2], "output_value": key[3], "direction": key[4],
            "n_runs": len(items), "n_used": len(kept),
            "mean_speed_mps": round(_mean(speeds), 4) if speeds else None,
            "speed_min_mps": round(min(speeds), 4) if speeds else None,
            "speed_max_mps": round(max(speeds), 4) if speeds else None,
            "mean_peak_delta_g": round(mean_delta, 4) if mean_delta is not None else None,
            "sd_peak_delta_g": round(sd_delta, 4) if sd_delta is not None else None,
            "mean_peak_g": round(mean_delta + 1.0, 4) if mean_delta is not None else None,
            "kept_min_delta_g": round(min(kept), 4), "kept_max_delta_g": round(max(kept), 4),
            "dropped_delta_g": [round(x, 4) for x in dropped],
            "dropped_run_ids": [r["id"] for r, f in zip(items, flags) if f],
            "outlier": info,
            "mean_force_n": round(mass_g / 1000.0 * G * (mean_delta + 1.0), 4) if mean_delta is not None else None,
            "mean_impulse_ns": round(_mean(imps), 6) if imps else None,
            "mean_pulse_s": round(_mean(pulses), 5) if pulses else None,
            "mean_dvz_mps": round(_mean(dvzs), 5) if dvzs else None,
            "mean_crest_min_g": round(_mean(crests), 4) if crests else None,
            "mean_avg_force_n": round(_mean(favg), 4) if favg else None,
            "event_delta_g": ev_mean, "event_n": ev_n, "event_impulse_ns": ev_imp,
            "metric": metric, "mean_max_delta_g": round(_mean(maxes), 4) if maxes else None,
            "mean_up_impulse_ns": round(_mean(up_imps), 6) if up_imps else None,
            "skipped_heading_ids": (skipped.get(key) or {}).get("heading", []),
            "skipped_no_metric_ids": (skipped.get(key) or {}).get("no_metric", []),
            "mean_pi": round(_mean(pis), 4) if pis else None,
            "jump_count": sum(1 for r in items if r["analysis"].get("jump")),
            "n_skipped": sum(len(x) for x in (skipped.get(key) or {}).values()),
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
