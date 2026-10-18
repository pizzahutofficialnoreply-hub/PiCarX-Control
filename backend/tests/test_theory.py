import math

from app import distance as dist
from app import impact_analysis as ia
from app import model_fit as mf


def test_outlier_methods():
    vals = [1.0, 1.1, 0.9, 1.05, 3.0]
    trim, _ = ia.outlier_flags(vals, "trim")
    assert trim == [False, False, True, False, True]
    iqr, info = ia.outlier_flags(vals, "iqr")
    assert iqr == [False, False, False, False, True]
    assert info["q1"] == 1.0 and info["q3"] == 1.1
    # n=5에서는 표본표준편차로 2s 밖이 나오기 어렵다(최대 z = (n−1)/√n ≈ 1.79)
    sig, _ = ia.outlier_flags(vals, "sigma2")
    assert not any(sig)
    sig10, _ = ia.outlier_flags([1.0, 1.02, 0.98, 1.01, 0.99, 1.0, 1.03, 0.97, 1.0, 2.0], "sigma2")
    assert sig10[-1] and sum(sig10) == 1
    assert ia.outlier_flags(vals, "none")[0] == [False] * 5


def test_entry_sin_matches_geometry():
    # 높이 10 mm, 폭 152 mm → R = 293.8 mm, 바퀴 r = 25 mm
    r_bump = ia.bump_radius_mm(10, 152)
    s = ia.entry_sin(r_bump, 10, 25)
    assert abs(s - math.sqrt(318.8 ** 2 - 308.8 ** 2) / 318.8) < 1e-3
    assert ia.entry_sin(None, 10, 25) is None


def test_distance_speed_and_outliers():
    ts, ds = dist.synthesize(1.5, 1.6, 0.6, 0.5, 2.5)
    ds = list(ds)
    ds[30] = ds[30] - 25  # 튀는 값
    res = dist.analyze(ts, ds, 1.5, 1.6, 0.6)
    assert res["n_removed"] >= 1
    assert abs(res["approach_speed_mps"] - 0.6) < 0.05
    assert res["drop_pct"] > 5


def test_speed_model_inverse():
    groups = [{"bump_id": "A", "output_mode": "pwm", "output_value": o, "total_mass_g": m, "mean_speed_mps": 0.1 + 0.008 * o - 0.1 * m / 1000}
              for o in (40, 60, 80) for m in (600, 900)]
    models = mf.speed_models(groups)
    v = mf.speed_from_output(models, "A", "pwm", 70, 750)
    assert abs(v - (0.1 + 0.56 - 0.075)) < 1e-6
    assert abs(mf.output_from_speed(models, "A", "pwm", v, 750) - 70) < 1e-6


def test_glitch_samples_are_replaced():
    import random
    random.seed(2)
    n = 400
    t = [i / 550 for i in range(n)]
    az = [1.1 + random.gauss(0, 0.02) for _ in range(n)]
    gx = [random.gauss(0, 1) for _ in range(n)]
    cols = {"t_s": t, "ax_g": [0.01] * n, "ay_g": [0.02] * n, "az_g": az, "gx_dps": gx, "gy_dps": list(gx[::-1]), "gz_dps": [0.5] * n,
            "vertical_g": [a - 0.1 for a in az], "dynamic_g": [0.0] * n}
    # 통신 오류 모양: az가 정확히 4 g, 0 g / 자이로 세 축 같음
    for i, val in ((100, 3.9998), (200, -0.0002)):
        cols["az_g"][i] = val
        cols["vertical_g"][i] = val - 0.1022 + 0.0002
        cols["gx_dps"][i] = cols["gy_dps"][i] = cols["gz_dps"][i] = -0.02
    # 진짜 충격(여러 표본에 걸침)은 그대로
    for k, extra in zip(range(300, 305), (0.8, 2.0, 3.0, 2.0, 0.8)):
        cols["vertical_g"][k] += extra
        cols["az_g"][k] += extra
    out, n_bad = ia.remove_glitches(cols)
    assert n_bad == 2
    assert max(out["vertical_g"][90:110]) < 1.2 and min(out["vertical_g"][190:210]) > 0.8
    assert max(out["vertical_g"][295:310]) > 3.5
