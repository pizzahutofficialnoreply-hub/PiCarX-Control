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


def test_bump_keeps_wheels_straight_by_default():
    # 기본(straight): 방지턱 위에서는 좌우로 꺾지 않고 학습된 직진 조향에 고정 → 충격 피크가 조향에 흔들리지 않음
    s2 = S.model_copy(update={"steer_ff_deg": 1.3})
    g = GuidedSteer(s2, ref_deg=0.0)
    _, _, t = _run(g, ON, 0.0)
    steer, ok, t = _run(g, RIGHT, 8.0, tilt=(5.0, 0.0), n=15, t0=t)
    assert ok and g.mode == "bump" and steer == 1.3
    # 턱을 지나면(0.25초 뒤) 틀어진 방향을 바로잡음(왼쪽으로 8° 틀어짐 → 오른쪽 조향)
    steer, _, _ = _run(g, ON, 8.0, n=30, t0=t)
    assert g.mode != "bump" and steer > 3


def test_bump_hold_mode_steers_back_on_bump():
    s2 = S.model_copy(update={"bump_steer": "hold"})
    g = GuidedSteer(s2, ref_deg=0.0)
    _, _, t = _run(g, ON, 0.0)
    steer, ok, _ = _run(g, RIGHT, 8.0, tilt=(5.0, 0.0), n=15, t0=t)
    assert ok and g.mode == "bump" and steer > 5
    g2 = GuidedSteer(s2, ref_deg=0.0)
    _, _, t = _run(g2, ON, 0.0)
    steer, _, _ = _run(g2, LEFT, -8.0, tilt=(5.0, 0.0), n=15, t0=t)
    assert g2.mode == "bump" and steer < -5


def test_calibration_check_flags_side_sensor_that_never_saw_tape():
    from app.line_follow import calibration_check
    bad = LineSettings(floor=[1200, 1200, 1200], line=[1180, 300, 1150])   # 좁은 유도선 위에서 '테이프' 보정
    msgs = calibration_check(bad)
    assert any("왼쪽" in m for m in msgs) and any("오른쪽" in m for m in msgs)
    assert calibration_check(S) == []


def test_learns_straight_steer_when_centered():
    g = GuidedSteer(S, ref_deg=0.0)
    _run(g, ON, 0.0, n=100)
    assert g.summary()["steer_calm_mean"] is not None


def test_camera_line_scan_finds_guide_and_cross():
    import numpy as np
    from app.vision import LineCam, LineCamSettings, line_scan
    img = np.full((120, 160, 3), 220.0)
    img[:, 95:105] = 30                       # 유도선이 오른쪽
    r = line_scan(img, LineCamSettings())
    assert r["guide"] > 0.1 and r["cross_y"] is None
    img2 = img.copy()
    img2[105:115, :] = 30                     # 화면 아래쪽 가로선
    r2 = line_scan(img2, LineCamSettings())
    assert r2["cross_y"] > 0.7
    cam = LineCam(lambda: None, LineCamSettings())
    cam.feed(r2, 1.0)
    cam.feed(r, 1.2)                          # 가로선이 화면 아래로 사라짐 → 차 밑으로 들어감
    assert cam.cross_passed_at == 1.2


def test_lost_uses_camera_guide():
    class Cam:
        def guide(self, now=None):
            return -0.8                        # 카메라: 유도선이 왼쪽
    g = GuidedSteer(S, ref_deg=0.0, cam=Cam())
    _, _, t = _run(g, ON, 0.0)
    steer, ok, _ = _run(g, OFF, 0.0, n=20, t0=t)
    assert g.mode == "lost" and steer < 0 and g.cam_used > 0


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


def test_corrupt_grayscale_reads_are_ignored():
    from app.line_follow import calibration_check, clean_raw, weights
    assert clean_raw([1459, 49383, 1495]) is None            # I²C가 깨진 값(0~4095 밖)
    assert clean_raw([1459, 1492, 1495]) == [1459.0, 1492.0, 1495.0]
    broken = LineSettings(floor=[42042.5, 49383.8, 49798.2], line=[173.4, 290.7, 238.7])
    assert any("범위" in m for m in calibration_check(broken))
    good = LineSettings(floor=[1460, 1490, 1495], line=[173, 290, 238])
    w = weights([1459, 1492, 1495], good)
    assert w is not None and max(w) < 0.05                    # 바닥 위 → 테이프 0%
    g = GuidedSteer(good, ref_deg=0.0)
    _run(g, [1459, 300, 1495], 0.0)
    steer, ok, _ = _run(g, [1459, 49383, 1495], 0.0, n=3)    # 깨진 읽기 → 직전 값 사용, 놓친 것으로 보지 않음
    assert ok and g.mode == "line"
