"""유도선 + 자이로 조향(GuidedSteer): 방지턱 위 방향 유지, 벗어났을 때 되돌아가는 방향, 놓쳤을 때 추정."""
from app.line_follow import GuidedSteer, LineSettings, TiltGate

S = LineSettings(floor=[1200, 1200, 1200], line=[300, 300, 300])
ON = [1200, 300, 1200]        # 가운데 센서만 테이프
RIGHT = [1200, 1200, 300]     # 테이프가 오른쪽 센서 밑
LEFT = [300, 1200, 1200]
OFF = [1200, 1200, 1200]


def _run(g: GuidedSteer, raw, heading, tilt=(0.0, 0.0), n=10, t0=0.0):
    steer, ok, t = 0.0, True, t0
    for _ in range(n):
        t += 0.02
        steer, ok = g.update(raw, heading, tilt, t)
    return steer, ok, t


def test_centered_gives_no_steering():
    g = GuidedSteer(S)
    steer, ok, _ = _run(g, ON, 0.0)
    assert ok and steer == 0.0 and g.mode == "line"


def test_tape_on_right_steers_right_and_left_steers_left():
    steer, _, _ = _run(GuidedSteer(S, ref_deg=0.0), RIGHT, 0.0)
    assert steer > 3
    steer, _, _ = _run(GuidedSteer(S, ref_deg=0.0), LEFT, 0.0)
    assert steer < -3


def test_bump_ignores_tape_and_holds_heading():
    g = GuidedSteer(S, ref_deg=0.0)
    _run(g, ON, 0.0)
    # 방지턱 위(기울기 5°): 센서가 오른쪽 테이프를 보는 것처럼 읽혀도 무시하고,
    # 턱에 부딪혀 왼쪽으로 8° 틀어진 것만 되돌림(오른쪽 조향 +)
    steer, ok, _ = _run(g, RIGHT, 8.0, tilt=(5.0, 0.0), n=15)
    assert ok and g.mode == "bump" and steer > 5
    # 반대로 오른쪽으로 틀어지면 왼쪽 조향
    g2 = GuidedSteer(S, ref_deg=0.0)
    _, _, t = _run(g2, ON, 0.0)
    steer, _, _ = _run(g2, LEFT, -8.0, tilt=(5.0, 0.0), n=15, t0=t)
    assert g2.mode == "bump" and steer < -5


def test_lost_after_drift_goes_back_toward_estimated_side():
    g = GuidedSteer(S, ref_deg=0.0, speed_mps=0.8)
    _, _, t = _run(g, ON, 0.0)
    # 방지턱 위에서 차가 오른쪽으로 10° 틀어진 채 0.3초 달림 → 센서는 유도선 오른쪽으로 밀림 → 테이프는 왼쪽
    _, _, t = _run(g, OFF, -10.0, tilt=(5.0, 0.0), n=15, t0=t)
    steer, ok, _ = _run(g, OFF, -10.0, n=20, t0=t)
    assert g.mode == "lost" and ok
    assert g.y_est is not None and g.y_est < 0          # 테이프는 왼쪽에 있다고 추정
    assert steer < 0                                      # 왼쪽으로 조향


def test_lost_too_long_stops():
    g = GuidedSteer(S, ref_deg=0.0, lost_s=0.5)
    _, _, t = _run(g, ON, 0.0)
    _, ok, _ = _run(g, OFF, 0.0, n=40, t0=t)
    assert not ok


def test_reference_follows_line_direction_when_centered():
    g = GuidedSteer(S, ref_deg=0.0)
    _run(g, ON, 5.0, n=150)  # 3초 동안 가운데로 가며 방향 5° → 유도선 방향을 5°로 배움
    assert abs(g.ref - 5.0) < 0.5


def test_tilt_gate_ignores_slow_drift_and_stuck_offset():
    g = TiltGate(1.5, tau_s=1.0, max_s=1.5)
    t = 0.0
    for _ in range(50):                      # 평지
        t += 0.02
        assert not g.update((0.0, 0.0), t)
    t += 0.02
    assert g.update((5.0, 0.0), t)           # 방지턱: 갑자기 5°
    # 적분이 흘러 2.3°에 머물러도 1.5초 뒤에는 평지로 봄
    for _ in range(100):
        t += 0.02
        last = g.update((2.3, 0.0), t)
    assert not last
    # 아주 느린 흐름(초당 0.5°)은 방지턱으로 보지 않음
    for i in range(200):
        t += 0.02
        assert not g.update((2.3 + 0.01 * i, 0.0), t)
