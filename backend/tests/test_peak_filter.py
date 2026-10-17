import math
import random

from app import impact_analysis as ia


def _signal():
    random.seed(3)
    t = [i / 700 for i in range(1400)]
    v = [1 + 0.08 * math.sin(2 * math.pi * 60 * x) + 0.03 * random.gauss(0, 1) for x in t]
    for c, h in ((0.8, 1.2), (0.9, 0.7)):
        for i, x in enumerate(t):
            v[i] += h * math.exp(-((x - c) / 0.004) ** 2)
    return t, v


def test_peak_preserving_filter_keeps_peaks_and_removes_vibration():
    t, v = _signal()
    f = ia.peak_preserving_filter(t, v, 20)
    peak = lambda s, c: max(s[i] for i, x in enumerate(t) if abs(x - c) < 0.02)  # noqa: E731
    for c in (0.8, 0.9):
        assert abs(peak(f, c) - peak(v, c)) < 0.02
    quiet = [i for i, x in enumerate(t) if x < 0.6]
    rms = lambda s: (sum((s[i] - 1) ** 2 for i in quiet) / len(quiet)) ** 0.5  # noqa: E731
    assert rms(f) < rms(v) * 0.4


def test_manual_override_keeps_auto_pair_for_restore():
    t, v = _signal()
    cols = {"t_s": t, "vertical_g": v, "dynamic_g": [x - 1 for x in v]}
    auto = ia.analyze_run(cols, wheelbase_mm=95, mass_g=600, filter_hz=20)
    man = ia.analyze_run(cols, wheelbase_mm=95, mass_g=600, filter_hz=20, override={"front_t_s": 0.79, "rear_t_s": 0.905})
    assert man["pair_source"] == "manual"
    assert man["auto_pair"]["front_t_s"] == auto["front_t_s"]
    assert abs(man["front_t_s"] - 0.8) < 0.01
