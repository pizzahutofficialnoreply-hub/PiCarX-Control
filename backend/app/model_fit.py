"""관계식 후보 비교, 신뢰구간, 최대 통과 속도, 출력→속도 지도, 자동 해석 문장.

관계식은 하나로 정해져 있지 않습니다. 같은 데이터에 여러 후보식을 맞춰 보고
'새 데이터를 얼마나 잘 맞히는가'(교차검증 오차)로 비교합니다.

데이터 단위: v (m/s), m (kg), Δa (g, 정지 1 g를 뺀 최대 수직 가속도 증가분).
모든 계산은 순수 파이썬입니다.
"""
from __future__ import annotations

import math
from typing import Any, Callable

from .impact_analysis import _solve

# 양측 95% t 임계값 (자유도 1~30), 30 초과는 1.96
_T95 = [12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228, 2.201, 2.179, 2.160, 2.145, 2.131,
        2.120, 2.110, 2.101, 2.093, 2.086, 2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042]


def t95(df: int) -> float:
    if df <= 0:
        return float("nan")
    return _T95[df - 1] if df <= len(_T95) else 1.96


Point = tuple[float, float, float]  # (v, m_kg, Δa)


# --------------------------------------------------------------------------- 최소제곱
def ols(x: list[list[float]], y: list[float]) -> dict[str, Any] | None:
    """y = Xβ. β, 표준오차, 잔차를 돌려줍니다."""
    n, k = len(y), len(x[0])
    if n < k:
        return None
    xtx = [[sum(r[i] * r[j] for r in x) for j in range(k)] for i in range(k)]
    xty = [sum(r[i] * y[t] for t, r in enumerate(x)) for i in range(k)]
    beta = _solve(xtx, xty)
    if beta is None:
        return None
    resid = [y[t] - sum(b * xi for b, xi in zip(beta, r)) for t, r in enumerate(x)]
    ssr = sum(e * e for e in resid)
    df = n - k
    se: list[float | None] = [None] * k
    if df > 0:
        sigma2 = ssr / df
        inv = []
        for i in range(k):
            unit = [1.0 if j == i else 0.0 for j in range(k)]
            col = _solve(xtx, unit)
            inv.append(col)
        if all(c is not None for c in inv):
            se = [math.sqrt(max(0.0, sigma2 * inv[i][i])) for i in range(k)]  # type: ignore[index]
    return {"beta": beta, "se": se, "df": df, "ssr": ssr}


# --------------------------------------------------------------------------- 후보 모델
class Spec:
    def __init__(self, id: str, name: str, formula: str, kind: str, design: Callable[[Point, bool], list[float]],
                 target: Callable[[Point], float | None], to_coef: Callable[[list[float], bool], dict[str, float]],
                 param_names: Callable[[bool], list[str]], explain: str):
        self.id, self.name, self.formula, self.kind = id, name, formula, kind
        self.design, self.target, self.to_coef, self.param_names, self.explain = design, target, to_coef, param_names, explain


def _ln(x: float) -> float | None:
    return math.log(x) if x > 0 else None


SPECS: list[Spec] = [
    Spec("power_vm", "거듭제곱 (속도·질량)", "Δa = k₀ · vⁿ · mᵖ", "power",
         lambda p, mass: [1.0, math.log(p[0]), math.log(p[1])],
         lambda p: _ln(p[2]),
         lambda b, mass: {"k0": math.exp(b[0]), "n": b[1], "p": b[2]},
         lambda mass: ["ln k₀", "n", "p"],
         "지수 n, p를 데이터가 정합니다. 보고서의 기본 모델입니다."),
    Spec("power_v", "거듭제곱 (속도만)", "Δa = k₀ · vⁿ", "power",
         lambda p, mass: [1.0, math.log(p[0])],
         lambda p: _ln(p[2]),
         lambda b, mass: {"k0": math.exp(b[0]), "n": b[1], "p": 0.0},
         lambda mass: ["ln k₀", "n"],
         "질량 효과를 무시합니다. 질량이 한 단계뿐일 때 씁니다."),
    Spec("n1", "속도에 비례 (충돌 모델)", "Δa = k₀ · v · mᵖ", "power",
         lambda p, mass: [1.0] + ([math.log(p[1])] if mass else []),
         lambda p: _ln(p[2] / p[0]) if p[0] > 0 else None,
         lambda b, mass: {"k0": math.exp(b[0]), "n": 1.0, "p": b[1] if mass else 0.0},
         lambda mass: ["ln k₀"] + (["p"] if mass else []),
         "n을 1로 고정한 이론식(진입 충돌 지배)."),
    Spec("n2", "속도 제곱에 비례 (원운동 모델)", "Δa = k₀ · v² · mᵖ", "power",
         lambda p, mass: [1.0] + ([math.log(p[1])] if mass else []),
         lambda p: _ln(p[2] / (p[0] ** 2)) if p[0] > 0 else None,
         lambda b, mass: {"k0": math.exp(b[0]), "n": 2.0, "p": b[1] if mass else 0.0},
         lambda mass: ["ln k₀"] + (["p"] if mass else []),
         "n을 2로 고정한 이론식(곡률·원운동 지배)."),
    Spec("linear", "일차식", "Δa = a + b·v (+ c·m)", "linear",
         lambda p, mass: [1.0, p[0]] + ([p[1]] if mass else []),
         lambda p: p[2],
         lambda b, mass: {"a": b[0], "b": b[1], "c": b[2] if mass else 0.0},
         lambda mass: ["a", "b"] + (["c"] if mass else []),
         "가장 단순한 직선 근사. 절편 a가 0이 아니면 저속 외삽이 부정확합니다."),
    Spec("quadratic", "이차식", "Δa = a + b·v² (+ c·m)", "quadratic",
         lambda p, mass: [1.0, p[0] ** 2] + ([p[1]] if mass else []),
         lambda p: p[2],
         lambda b, mass: {"a": b[0], "b": b[1], "c": b[2] if mass else 0.0},
         lambda mass: ["a", "b"] + (["c"] if mass else []),
         "v²에 비례하는 항 + 상수. 원운동 모델에 오프셋을 더한 형태."),
]


def predict(model: dict[str, Any], v: float, m_kg: float) -> float:
    c = model["coef"]
    if model["kind"] == "power":
        return c["k0"] * (v ** c["n"]) * (m_kg ** c.get("p", 0.0)) if v > 0 and m_kg > 0 else 0.0
    if model["kind"] == "linear":
        return c["a"] + c["b"] * v + c.get("c", 0.0) * m_kg
    return c["a"] + c["b"] * v * v + c.get("c", 0.0) * m_kg


def solve_speed(model: dict[str, Any] | None, delta_target: float, m_kg: float, v_hi: float = 6.0) -> float | None:
    """예측 Δa가 목표값이 되는 속도(증가 함수 가정, 이분법)."""
    if not model or delta_target <= 0 or m_kg <= 0:
        return None
    lo, hi = 1e-4, v_hi
    f_lo, f_hi = predict(model, lo, m_kg) - delta_target, predict(model, hi, m_kg) - delta_target
    if f_lo > 0 or f_hi < 0:
        return None
    for _ in range(80):
        mid = (lo + hi) / 2
        if predict(model, mid, m_kg) - delta_target > 0:
            hi = mid
        else:
            lo = mid
    return round((lo + hi) / 2, 4)


def _fit_spec(spec: Spec, pts: list[Point], use_mass: bool) -> dict[str, Any] | None:
    rows, ys, used = [], [], []
    for p in pts:
        y = spec.target(p)
        if y is None:
            continue
        rows.append(spec.design(p, use_mass))
        ys.append(y)
        used.append(p)
    if len(rows) < len(rows[0]) if rows else True:
        return None
    res = ols(rows, ys)
    if res is None:
        return None
    coef = spec.to_coef(res["beta"], use_mass)
    model = {"id": spec.id, "kind": spec.kind, "coef": coef}
    return {"model": model, "ols": res, "points": used}


def _metrics(model: dict[str, Any], pts: list[Point]) -> tuple[float, float, float]:
    pred = [predict(model, v, m) for v, m, _ in pts]
    obs = [d for *_, d in pts]
    ssr = sum((o - q) ** 2 for o, q in zip(obs, pred))
    mean = sum(obs) / len(obs)
    sst = sum((o - mean) ** 2 for o in obs)
    r2 = 1 - ssr / sst if sst > 0 else 1.0
    return ssr, r2, math.sqrt(ssr / len(obs))


def compare_models(pts: list[Point]) -> dict[str, Any]:
    """모든 후보 모델을 맞추고 비교표를 만듭니다."""
    pts = [p for p in pts if p[0] > 0 and p[1] > 0 and p[2] > 0]
    masses = {round(m, 4) for _, m, _ in pts}
    speeds = {round(v, 4) for v, _, _ in pts}
    use_mass = len(masses) >= 2
    out: dict[str, Any] = {"points": len(pts), "masses": len(masses), "speeds": len(speeds), "models": [], "best": None, "note": None}
    if len(pts) < 2 or len(speeds) < 2:
        out["note"] = "서로 다른 속도 조건의 평균이 2개 이상 있어야 식을 맞출 수 있습니다."
        return out
    for spec in SPECS:
        if spec.id == "power_vm" and not use_mass:
            continue
        fit = _fit_spec(spec, pts, use_mass)
        if fit is None:
            continue
        model = fit["model"]
        res = fit["ols"]
        n_par = len(res["beta"])
        ssr, r2, rmse = _metrics(model, pts)
        # 한 점씩 빼고 맞춘 뒤 그 점을 예측한 오차(교차검증) – 과적합이면 커집니다.
        loo_err = []
        if len(pts) - 1 >= n_par + 1:
            for i in range(len(pts)):
                sub = _fit_spec(spec, pts[:i] + pts[i + 1:], use_mass)
                if sub is None:
                    loo_err = []
                    break
                v, m, d = pts[i]
                loo_err.append(predict(sub["model"], v, m) - d)
        loocv = math.sqrt(sum(e * e for e in loo_err) / len(loo_err)) if loo_err else None
        nobs = len(pts)
        aic = nobs * math.log(ssr / nobs) + 2 * n_par if ssr > 0 else None
        tcrit = t95(res["df"])
        params = []
        for name, b, se in zip(spec.param_names(use_mass), res["beta"], res["se"]):
            ci = (b - tcrit * se, b + tcrit * se) if se is not None and math.isfinite(tcrit) else None
            params.append({"name": name, "value": b, "se": se, "ci95": ci})
        out["models"].append({
            **model, "name": spec.name, "formula": spec.formula, "explain": spec.explain,
            "n_params": n_par, "df": res["df"], "r2": r2, "rmse": rmse, "loocv_rmse": loocv, "aic": aic,
            "params": params, "fit_space": "log" if spec.kind == "power" else "linear",
            "exact_fit": res["df"] == 0,
        })
    ranked = [m for m in out["models"] if m["loocv_rmse"] is not None]
    if ranked:
        out["best"] = min(ranked, key=lambda m: m["loocv_rmse"])["id"]
        out["rank_by"] = "loocv"
    elif out["models"]:
        cand = [m for m in out["models"] if m["aic"] is not None] or out["models"]
        out["best"] = min(cand, key=lambda m: (m["aic"] if m["aic"] is not None else 1e9, m["n_params"]))["id"]
        out["rank_by"] = "aic"
        out["note"] = "점이 적어 교차검증을 못 했습니다. 조건(속도·질량)을 늘리면 비교가 정확해집니다."
    return out


def choose(comparison: dict[str, Any], model_id: str) -> dict[str, Any] | None:
    models = comparison.get("models") or []
    if model_id and model_id != "auto":
        for m in models:
            if m["id"] == model_id:
                return m
    for m in models:
        if m["id"] == comparison.get("best"):
            return m
    return models[0] if models else None


# --------------------------------------------------------------------------- 출력 → 속도
def speed_maps(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """방지턱·질량·출력 방식별로 '출력값 → 실제 속도' 직선(v = a + b·출력)."""
    buckets: dict[tuple, list[tuple[float, float]]] = {}
    for g in groups:
        if g.get("mean_speed_mps"):
            buckets.setdefault((g["bump_id"], g["total_mass_g"], g["output_mode"]), []).append((float(g["output_value"]), g["mean_speed_mps"]))
    maps = []
    for (bump, mass, mode), xy in sorted(buckets.items()):
        entry: dict[str, Any] = {"bump_id": bump, "total_mass_g": mass, "output_mode": mode,
                                 "points": [{"output": x, "speed_mps": y} for x, y in sorted(xy)], "a": None, "b": None, "r2": None}
        if len({x for x, _ in xy}) >= 2:
            res = ols([[1.0, x] for x, _ in xy], [y for _, y in xy])
            if res:
                a, b = res["beta"]
                mean = sum(y for _, y in xy) / len(xy)
                sst = sum((y - mean) ** 2 for _, y in xy)
                entry.update(a=a, b=b, r2=1 - res["ssr"] / sst if sst > 0 else 1.0)
        maps.append(entry)
    return maps


def output_for_speed(maps: list[dict[str, Any]], bump_id: str, mass_g: float, mode: str, target_v: float) -> dict[str, Any] | None:
    """목표 속도에 필요한 출력값(가장 가까운 질량의 출력→속도 직선 사용)."""
    cands = [m for m in maps if m["bump_id"] == bump_id and m["output_mode"] == mode and m["b"]]
    if not cands:
        return None
    best = min(cands, key=lambda m: abs(m["total_mass_g"] - mass_g))
    if best["b"] <= 0:
        return None
    out = (target_v - best["a"]) / best["b"]
    xs = [p["output"] for p in best["points"]]
    return {"output": round(out, 1), "based_on_mass_g": best["total_mass_g"],
            "extrapolated": not (min(xs) <= out <= max(xs)), "r2": best["r2"]}


# --------------------------------------------------------------------------- 해석 문장
def _f(x: float | None, d: int = 3) -> str:
    return "—" if x is None else f"{x:.{d}f}"


def interpret(bump_id: str, groups: list[dict[str, Any]], comparison: dict[str, Any], chosen: dict[str, Any] | None,
              criterion: dict[str, Any] | None) -> list[str]:
    """보고서 결론에 옮겨 쓸 수 있는 자동 해석 문장(숫자는 실제 데이터에서 계산)."""
    mine = [g for g in groups if g["bump_id"] == bump_id and g.get("mean_speed_mps") and g.get("mean_peak_delta_g") is not None]
    lines: list[str] = []
    if not mine:
        return ["아직 집계할 조건이 없습니다."]
    masses = sorted({g["total_mass_g"] for g in mine})
    for mass in masses:
        sub = sorted([g for g in mine if g["total_mass_g"] == mass], key=lambda g: g["mean_speed_mps"])
        if len(sub) >= 2:
            lo, hi = sub[0], sub[-1]
            ratio = hi["mean_peak_delta_g"] / lo["mean_peak_delta_g"] if lo["mean_peak_delta_g"] > 0 else None
            lines.append(f"총질량 {mass:g} g에서 통과 속도가 {lo['mean_speed_mps']:.3f} → {hi['mean_speed_mps']:.3f} m/s로 "
                         f"{hi['mean_speed_mps'] / lo['mean_speed_mps']:.2f}배가 될 때 최대 수직 가속도 증가분 Δa는 "
                         f"{lo['mean_peak_delta_g']:.3f} → {hi['mean_peak_delta_g']:.3f} g로 {_f(ratio, 2)}배가 되었다.")
    if chosen:
        r2 = chosen["r2"]
        lines.append(f"여러 후보식 중 교차검증 오차가 가장 작은(또는 선택한) 식은 '{chosen['name']}' {chosen['formula']}이며, "
                     f"결정계수 R² = {r2:.3f}{'(점 수와 매개변수 수가 같아 R²=1은 의미가 약함)' if chosen.get('exact_fit') else ''}이다.")
        n_param = next((p for p in chosen["params"] if p["name"] == "n"), None)
        if n_param:
            ci = n_param["ci95"]
            text = f"속도 지수 n = {n_param['value']:.2f}"
            if ci:
                text += f" (95% 신뢰구간 {ci[0]:.2f} ~ {ci[1]:.2f})"
                inc1, inc2 = ci[0] <= 1 <= ci[1], ci[0] <= 2 <= ci[1]
                if inc1 and inc2:
                    text += ": 신뢰구간이 1과 2를 모두 포함해 충돌 모델(n=1)과 원운동 모델(n=2)을 구분할 수 없다. 속도 조건을 넓혀야 한다."
                elif inc2:
                    text += ": 2를 포함하므로 원운동(곡률) 모델과 일치한다."
                elif inc1:
                    text += ": 1을 포함하므로 진입 충돌 모델과 일치한다."
                elif ci[1] < 1:
                    text += ": 1보다 작아 속도 증가에 비해 충격 증가가 완만하다(타이어·차체 완충 가능성)."
                elif ci[0] > 2:
                    text += ": 2보다 커서 원운동만으로 설명되지 않는다(점프·착지 충격 포함 가능성)."
                else:
                    text += ": 1과 2 사이로, 충돌과 원운동 효과가 함께 작용하는 것으로 보인다."
            else:
                text += "(신뢰구간을 계산할 만큼 점이 많지 않음)."
            lines.append(text)
        p_param = next((p for p in chosen["params"] if p["name"] == "p"), None)
        if p_param and p_param["ci95"]:
            lo_p, hi_p = p_param["ci95"]
            if lo_p <= 0 <= hi_p:
                lines.append(f"질량 지수 p = {p_param['value']:.2f} (95% CI {lo_p:.2f} ~ {hi_p:.2f})로 0을 포함한다. "
                             "즉 이 범위에서 질량은 가속도에 뚜렷한 영향을 주지 않았고(강체 모델과 일치), 충격력 F = m·a는 질량에 비례해 커졌다.")
            elif hi_p < 0:
                lines.append(f"질량 지수 p = {p_param['value']:.2f} (95% CI {lo_p:.2f} ~ {hi_p:.2f}) < 0: 무거울수록 가속도는 오히려 작아졌다"
                             "(타이어 완충으로 충격 시간이 길어지는 효과). 그러나 충격력 F = m·a는 질량과 함께 커질 수 있다.")
            else:
                lines.append(f"질량 지수 p = {p_param['value']:.2f} (95% CI {lo_p:.2f} ~ {hi_p:.2f}) > 0: 무거울수록 가속도도 커졌다.")
        elif len(masses) < 2:
            lines.append("질량이 한 단계뿐이라 질량의 효과는 아직 판단할 수 없다.")
        else:
            full = next((m for m in comparison.get("models", []) if m["id"] == "power_vm"), None)
            fp = next((p for p in full["params"] if p["name"] == "p"), None) if full else None
            text = "질량 항이 없는 식이 새 데이터를 더 잘 맞혔다. 즉 이 범위에서 질량은 최대 가속도에 뚜렷한 영향을 주지 않았다"
            if fp and fp["ci95"]:
                text += f"(질량까지 넣은 식의 p = {fp['value']:.2f}, 95% CI {fp['ci95'][0]:.2f} ~ {fp['ci95'][1]:.2f})"
            lines.append(text + ". 다만 충격력 F = m·a는 질량에 비례해 커진다.")
    jumped = sorted([g for g in mine if g["jump_count"] > 0], key=lambda g: g["mean_speed_mps"])
    vc = criterion.get("critical_speed_mps") if criterion else None
    if jumped:
        first = jumped[0]
        text = f"지면 이탈(점프)은 약 {first['mean_speed_mps']:.3f} m/s부터 나타났다"
        if vc:
            text += f"(원운동 모델의 이론 임계 속도 v_c = {vc:.3f} m/s, 차이 {abs(first['mean_speed_mps'] - vc) / vc * 100:.0f}%)"
        lines.append(text + ".")
    elif vc:
        top = max(g["mean_speed_mps"] for g in mine)
        lines.append(f"측정한 최고 속도 {top:.3f} m/s까지 점프는 없었다(이론 임계 속도 v_c = {vc:.3f} m/s).")
    if criterion and criterion.get("a_limit_g"):
        rows = [f"{v['mass_g']:g} g → " + (v["note"] if v.get("note") else f"{_f(v['vmax_mps'])} m/s") for v in criterion.get("vmax_by_mass", [])]
        if rows:
            lines.append(f"허용 최대 수직 가속도 {criterion['a_limit_g']:.2f} g를 넘지 않는 최대 통과 속도는 " + ", ".join(rows) + "이다.")
    if comparison.get("note"):
        lines.append("주의: " + comparison["note"])
    return lines
