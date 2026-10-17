import math
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import experiment, impact_analysis as ia
from app.hardware import PiCarXAdapter


def synth(output, mass=1000, base=1000, radius=50.0, seed=1, mode="pwm"):
    return ia.synthesize_run(output_mode=mode, output_value=output, mass_g=mass, base_mass_g=base,
                             wheelbase_mm=95, bump_radius=radius, wheel_radius_mm=33, seed=seed)


class FlatGroundTests(unittest.TestCase):
    def test_flat_floor_vibration_is_not_a_bump(self):
        import random
        rng = random.Random(7)
        t, v = [], []
        for i in range(3000):
            x = i / 1000
            g = 1.0 + rng.gauss(0, 0.04)
            if rng.random() < 0.01:          # 바닥 이음매·모터 진동: 0.3~0.6 g 짧은 튐
                g += rng.uniform(0.3, 0.6)
            t.append(x); v.append(g)
        res = ia.analyze_run({"t_s": t, "vertical_g": v, "dynamic_g": [abs(x - 1) for x in v]}, wheelbase_mm=95, mass_g=1000)
        self.assertFalse(res["bump_detected"])
        self.assertIsNone(res["speed_mps"])
        runs = [{"id": "x", "condition": {"bump_id": "A", "total_mass_g": 1000, "output_mode": "pwm", "output_value": 50}, "analysis": res}]
        self.assertEqual(ia.aggregate(runs), [])

    def test_real_bump_still_detected_over_vibration(self):
        import random
        rng = random.Random(3)
        cols = synth(60, seed=5)
        cols["vertical_g"] = [g + (rng.uniform(0.2, 0.4) if rng.random() < 0.006 else 0) for g in cols["vertical_g"]]
        res = ia.analyze_run(cols, wheelbase_mm=95, mass_g=1000, bump_radius=50, wheel_radius_mm=33)
        self.assertTrue(res["bump_detected"])
        self.assertIsNotNone(res["speed_mps"])
        self.assertGreater(res["standout_ratio"] or 99, 1.5)


def _vibrating(bump: bool, amp: float = 0.8, seed: int = 1):
    """차체가 28 Hz로 ±0.3 g 출렁이는 주행(사용자 기록과 비슷) + 선택적으로 방지턱 두 피크(0.5 m/s)."""
    import math, random
    rng = random.Random(seed)
    t, v = [], []
    for i in range(2500):
        x = i / 1000
        g = 1 + 0.3 * math.sin(2 * math.pi * 28 * x + rng.random() * 0.3) + rng.gauss(0, 0.06)
        if bump:
            for c in (1.2, 1.2 + 0.095 / 0.5):
                g += amp * math.exp(-((x - c) / 0.012) ** 2)
        t.append(x); v.append(g)
    return {"t_s": t, "vertical_g": v, "dynamic_g": [0.0] * len(v)}


class FilterAndSensitivityTests(unittest.TestCase):
    def test_filter_reduces_vibration_and_finds_bump(self):
        raw = ia.analyze_run(_vibrating(True), wheelbase_mm=95, mass_g=650)
        self.assertFalse(raw["bump_detected"])            # 진동에 묻힘
        res = ia.analyze_run(_vibrating(True), wheelbase_mm=95, mass_g=650, filter_hz=20)
        self.assertTrue(res["bump_detected"])
        self.assertAlmostEqual(res["speed_mps"], 0.5, delta=0.05)
        self.assertLess(res["noise_sigma_g"], raw["noise_sigma_g"] / 3)

    def test_flat_stays_rejected_with_filter_and_sensitive(self):
        for hz in (0, 15, 20, 30):
            for level in ("strict", "normal", "sensitive"):
                res = ia.analyze_run(_vibrating(False), wheelbase_mm=95, mass_g=650, filter_hz=hz, detect_level=level)
                self.assertFalse(res["bump_detected"], (hz, level))

    def test_sensitive_finds_smaller_bump(self):
        normal = ia.analyze_run(_vibrating(True, amp=0.25), wheelbase_mm=95, mass_g=650, filter_hz=15, detect_level="normal")
        sens = ia.analyze_run(_vibrating(True, amp=0.25), wheelbase_mm=95, mass_g=650, filter_hz=15, detect_level="sensitive")
        self.assertFalse(normal["bump_detected"])
        self.assertTrue(sens["bump_detected"])


class AnalysisTests(unittest.TestCase):
    def test_bump_radius_formula(self):
        self.assertAlmostEqual(ia.bump_radius_mm(10, 60), 50.0)
        self.assertAlmostEqual(ia.bump_radius_mm(10, 30), 16.25)
        self.assertIsNone(ia.bump_radius_mm(0, 30))

    def test_axle_peaks_give_speed(self):
        cols = synth(60, seed=3)
        result = ia.analyze_run(cols, wheelbase_mm=95, mass_g=1000, bump_radius=50, wheel_radius_mm=33)
        expected = 95 / 1000 / (result["rear_t_s"] - result["front_t_s"])
        self.assertIsNotNone(result["speed_mps"])
        self.assertAlmostEqual(result["speed_mps"], expected, places=3)
        self.assertGreater(result["peak_delta_g"], 0.3)
        self.assertFalse(result["jump"])
        self.assertAlmostEqual(result["peak_force_n"], 1.0 * ia.G * result["peak_g"], places=3)
        self.assertAlmostEqual(result["pi"], result["speed_mps"] ** 2 / (ia.G * 0.083), places=3)

    def test_jump_detected_above_critical_speed(self):
        # 작은 반경(R_eff 작음) + 고속 → Π > 1 → 공중 구간(≈0 g)
        cols = synth(100, radius=5.0, seed=4)
        result = ia.analyze_run(cols, wheelbase_mm=95, mass_g=1000, bump_radius=5.0, wheel_radius_mm=33)
        self.assertGreater(result["pi"], 1.0)
        self.assertTrue(result["jump"])
        self.assertLess(result["min_g"], 0.2)

    def test_landing_peak_is_not_used_as_rear_wheel(self):
        # 점프 후 착지 피크가 뒷바퀴 피크보다 먼저·크게 나타나는 경우에도 속도는 모델값 근처여야 합니다.
        cols = synth(90, radius=16.25, seed=2)
        model_v = ia.simulated_speed_mps("pwm", 90, 1000, 1000)
        result = ia.analyze_run(cols, wheelbase_mm=95, mass_g=1000, bump_radius=16.25, wheel_radius_mm=33)
        self.assertTrue(result["jump"])
        self.assertIsNotNone(result["landing_g"])
        self.assertGreater(result["landing_g"], result["rear_g"])
        self.assertLess(abs(result["speed_mps"] - model_v) / model_v, 0.08)

    def test_missing_wheelbase_reports_flag(self):
        result = ia.analyze_run(synth(60), wheelbase_mm=None, mass_g=1000)
        self.assertIsNone(result["speed_mps"])
        self.assertTrue(any("축간거리" in f for f in result["flags"]))

    def test_two_speed_conditions_still_give_a_curve(self):
        groups = [{"bump_id": "A", "total_mass_g": 1000, "mean_speed_mps": v, "mean_peak_delta_g": 2 * v ** 1.5} for v in (0.4, 0.6)]
        fit = ia.fit_power_law(groups, "A")
        self.assertAlmostEqual(fit["overall"]["n"], 1.5, places=4)
        self.assertIn("신뢰도 낮음", fit["note"])
        self.assertEqual(len(fit["per_mass"]), 1)

    def test_trimmed_mean_drops_one_max_and_min(self):
        runs = [{"id": str(i), "condition": {"bump_id": "A", "total_mass_g": 1000, "output_mode": "pwm", "output_value": 50},
                 "analysis": {"peak_delta_g": d, "speed_mps": 0.5}} for i, d in enumerate([1.0, 2.0, 2.2, 2.4, 9.0])]
        group = ia.aggregate(runs)[0]
        self.assertEqual(group["n_runs"], 5)
        self.assertEqual(group["n_used"], 3)
        self.assertAlmostEqual(group["mean_peak_delta_g"], 2.2, places=4)
        self.assertEqual(group["dropped_delta_g"], [1.0, 9.0])

    def test_power_law_fit_recovers_exponents(self):
        groups = []
        for mass in (1000, 1150, 1300):
            for v in (0.2, 0.3, 0.4, 0.5, 0.6):
                groups.append({"bump_id": "A", "total_mass_g": mass, "mean_speed_mps": v,
                               "mean_peak_delta_g": 2.0 * v ** 1.7 * (mass / 1000) ** -0.2})
        fit = ia.fit_power_law(groups, "A")
        self.assertAlmostEqual(fit["overall"]["n"], 1.7, places=3)
        self.assertAlmostEqual(fit["overall"]["p"], -0.2, places=3)
        self.assertAlmostEqual(fit["overall"]["r2"], 1.0, places=6)
        vmax = ia.predict_vmax(fit, a_limit_g=2.0, mass_g=1000)
        self.assertAlmostEqual(vmax, (1.0 / 2.0) ** (1 / 1.7), places=3)


class DutyModeTests(unittest.TestCase):
    def test_never_reads_motor_pins(self):
        """robot_hat의 인자 없는 Pin.value()/pulse_width_percent()는 핀 상태를 바꿀 수 있어 호출하면 안 됩니다."""
        adapter = PiCarXAdapter.__new__(PiCarXAdapter)
        adapter.connected, adapter.connection_error, adapter.steering_center = True, None, 0
        car = MagicMock()
        car.motor_speed_pins = [MagicMock(), MagicMock()]
        car.motor_direction_pins = [MagicMock(), MagicMock()]
        adapter._car = car
        adapter.drive("forward", 60, 0)
        adapter.drive("backward", 60, 10)
        adapter.drive_experiment("pwm", 80)
        adapter.drive_experiment("duty", 50)
        for pin in car.motor_direction_pins:
            for call in pin.value.call_args_list:
                self.assertTrue(call.args or call.kwargs, "방향 핀을 인자 없이 읽었습니다")
        for pin in car.motor_speed_pins:
            for call in pin.pulse_width_percent.call_args_list:
                self.assertTrue(call.args or call.kwargs, "PWM 핀을 인자 없이 읽었습니다")

    def test_duty_mode_sets_direction_then_overrides_pins(self):
        adapter = PiCarXAdapter.__new__(PiCarXAdapter)
        adapter.connected, adapter.connection_error, adapter.steering_center = True, None, 0
        car = MagicMock()
        pins = [MagicMock(), MagicMock()]
        car.motor_speed_pins = pins
        adapter._car = car
        adapter.drive_experiment("duty", 35)
        car.forward.assert_called_once_with(1)
        for pin in pins:
            pin.pulse_width_percent.assert_any_call(35)
        car.reset_mock()
        adapter.drive_experiment("pwm", 80)
        # 측정 직진은 조향과 무관하게 두 바퀴에 같은 출력(라이브러리의 직진과 같은 호출)
        car.set_motor_speed.assert_any_call(1, 80)
        car.set_motor_speed.assert_any_call(2, -80)
        car.forward.assert_not_called()
        car.reset_mock()
        adapter.drive_experiment("pwm", 80, "forward", 7)   # 같은 출력, 조향만 변경 → 모터 명령 안 보냄
        car.set_motor_speed.assert_not_called()
        car.set_dir_servo_angle.assert_called_with(7)
        car.reset_mock()
        adapter.drive_experiment("pwm", 40, "backward", 5.4)
        car.set_motor_speed.assert_any_call(1, -40)
        car.set_motor_speed.assert_any_call(2, 40)
        car.set_dir_servo_angle.assert_called_with(5)
        # 전진과 후진은 같은 출력 경로: 같은 값이면 예상 듀티도 같아야 합니다.
        adapter.drive("forward", 100, 0); fwd = adapter.last_command["expected_duty_percent"]
        adapter.drive("backward", 100, 0); back = adapter.last_command["expected_duty_percent"]
        self.assertEqual(fwd, back)
        self.assertEqual(fwd, 100)


class ExperimentApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.patch_store = patch.object(experiment, "store", experiment.ExperimentStore(Path(self.tmp.name)))
        self.patch_store.start()
        experiment.auto_runner.state = "idle"

    def tearDown(self):
        self.patch_store.stop()
        self.tmp.cleanup()

    def _wait(self, client):
        for _ in range(100):
            state = client.get("/api/experiment/auto-run").json()
            if state["state"] in ("done", "error", "aborted"):
                return state
            time.sleep(0.05)
        self.fail("auto-run did not finish")

    def test_saving_settings_keeps_learned_heading(self):
        from app.main import app
        with TestClient(app) as client:
            s = client.get("/api/experiment/settings").json()
            s.update(heading_ff_forward=2.5, heading_ff_backward=-1.5)
            client.put("/api/experiment/settings", json=s)
            other = {k: v for k, v in s.items() if not k.startswith("heading_ff")}
            other["jump_g"] = 0.25
            saved = client.put("/api/experiment/settings", json=other).json()
            self.assertEqual((saved["heading_ff_forward"], saved["heading_ff_backward"], saved["jump_g"]), (2.5, -1.5, 0.25))

    def test_mock_auto_run_saves_analyzes_and_exports(self):
        from app.main import app
        with TestClient(app) as client:
            settings = client.get("/api/experiment/settings").json()
            self.assertEqual(settings["bumps"][0]["radius_mm"], 50.0)
            settings.update(wheelbase_mm=95, wheel_radius_mm=33, base_mass_g=1000)
            self.assertEqual(client.put("/api/experiment/settings", json=settings).status_code, 200)
            for output in (40, 60, 80):
                for trial in (1, 2, 3):
                    body = {"condition": {"trial": trial, "bump_id": "A", "base_mass_g": 1000, "added_mass_g": 0,
                                          "output_mode": "pwm", "output_value": output},
                            "pre_roll_s": 0.3, "run_seconds": 0.3, "tail_s": 0.1, "repeats": 1, "return_enabled": False}
                    r = client.post("/api/experiment/auto-run", json=body)
                    self.assertEqual(r.status_code, 200, r.text)
                    state = self._wait(client)
                    self.assertEqual(state["state"], "done", state["message"])
                    self.assertTrue(state["result"]["simulated"])
            listing = client.get("/api/experiment/runs").json()
            self.assertTrue(listing["simulated"])
            self.assertEqual(len(listing["runs"]), 9)
            self.assertEqual(len(listing["groups"]), 3)
            self.assertTrue(all(g["mean_speed_mps"] for g in listing["groups"]))
            fit = listing["fits"]["A"]
            self.assertIsNotNone(fit["overall"])
            self.assertGreater(fit["overall"]["n"], 0.5)

            run_id = listing["runs"][0]["id"]
            detail = client.get(f"/api/experiment/runs/{run_id}").json()
            self.assertLessEqual(len(detail["series"]["t_s"]), 1500)
            # 다운샘플 후에도 피크가 보존되어야 합니다.
            self.assertAlmostEqual(max(detail["series"]["vertical_g"]), detail["analysis"]["peak_g"], places=3)
            raw = client.get(f"/api/experiment/runs/{run_id}/raw.csv")
            self.assertEqual(raw.status_code, 200)
            self.assertIn("vertical_g", raw.text.splitlines()[0])
            self.assertIn("speed_mps", client.get("/api/experiment/export/runs.csv").text)
            self.assertIn("mean_peak_delta_g", client.get("/api/experiment/export/conditions.csv").text)

            self.assertTrue(client.patch(f"/api/experiment/runs/{run_id}", json={"excluded": True}).json()["excluded"])
            self.assertEqual(client.post("/api/experiment/reanalyze").json()["reanalyzed"], 9)
            self.assertEqual(client.delete(f"/api/experiment/runs/{run_id}").status_code, 200)
            self.assertEqual(len(client.get("/api/experiment/runs").json()["runs"]), 8)
            self.assertEqual(client.get("/api/experiment/runs/../../etc").status_code, 404)

    def test_stop_aborts_auto_run(self):
        from app.main import app
        with TestClient(app) as client:
            body = {"condition": {"base_mass_g": 1000, "output_value": 50}, "pre_roll_s": 0.3, "run_seconds": 3, "tail_s": 0.1, "repeats": 1}
            self.assertEqual(client.post("/api/experiment/auto-run", json=body).status_code, 200)
            time.sleep(0.6)
            client.post("/api/stop")
            state = self._wait(client)
            self.assertEqual(state["state"], "aborted")
            self.assertTrue(state["result"]["excluded"])

    def test_repeated_runs_with_return_and_criterion(self):
        from app.main import app
        with TestClient(app) as client:
            settings = client.get("/api/experiment/settings").json()
            self.assertEqual(settings["a_limit_preset"], "trl614")
            self.assertEqual(settings["a_limit_effective_g"], 1.7)
            settings.update(wheelbase_mm=95, wheel_radius_mm=33, a_limit_mode="manual", a_limit_preset="custom", a_limit_g=1.8)
            settings["bumps"] = [{k: b[k] for k in ("id", "name", "height_mm", "width_mm")} for b in settings["bumps"]]
            for k in ("max_output", "a_limit_effective_g", "a_limit_presets"):
                settings.pop(k)
            client.put("/api/experiment/settings", json=settings)
            body = {"condition": {"trial": 4, "base_mass_g": 1000, "output_value": 60},
                    "pre_roll_s": 0.3, "run_seconds": 0.3, "tail_s": 0.1, "repeats": 3, "return_enabled": True,
                    "return_scale": 1.0, "rest_s": 0.3}
            self.assertEqual(client.post("/api/experiment/auto-run", json=body).status_code, 200)
            phases = set()
            for _ in range(200):
                state = client.get("/api/experiment/auto-run").json()
                phases.add(state["state"])
                if state["state"] in ("done", "error", "aborted"):
                    break
                time.sleep(0.03)
            self.assertEqual(state["state"], "done", state["message"])
            self.assertIn("returning", phases)
            self.assertEqual(len(state["completed_run_ids"]), 3)
            listing = client.get("/api/experiment/runs").json()
            self.assertEqual(sorted(r["condition"]["trial"] for r in listing["runs"]), [4, 5, 6])
            ids = ",".join(r["id"] for r in listing["runs"])
            series = client.get(f"/api/experiment/series?ids={ids}").json()["series"]
            self.assertEqual(len(series), 3)
            self.assertTrue(all(abs(min(x["t"], key=abs)) < 0.01 for x in series))  # 앞바퀴 피크가 t = 0
            crit = listing["criteria"]["A"]
            self.assertEqual(crit["a_limit_g"], 1.8)
            self.assertEqual(len(crit["vmax_by_mass"]), 1)
            settings["a_limit_mode"] = "first_jump"
            client.put("/api/experiment/settings", json=settings)
            crit = client.get("/api/experiment/runs").json()["criteria"]["A"]
            self.assertIsNone(crit["a_limit_g"])  # 아직 점프한 조건이 없음
            self.assertIn("점프", crit["source"])

    def test_criterion_first_jump_uses_slowest_jumping_condition(self):
        groups = [
            {"bump_id": "A", "total_mass_g": 1000, "output_mode": "pwm", "output_value": 50, "jump_count": 0, "mean_speed_mps": 0.5, "mean_peak_g": 2.0},
            {"bump_id": "A", "total_mass_g": 1000, "output_mode": "pwm", "output_value": 80, "jump_count": 2, "mean_speed_mps": 0.9, "mean_peak_g": 3.4},
            {"bump_id": "A", "total_mass_g": 1000, "output_mode": "pwm", "output_value": 70, "jump_count": 1, "mean_speed_mps": 0.8, "mean_peak_g": 3.0},
        ]
        settings = experiment.ExperimentSettings(a_limit_mode="first_jump", wheel_radius_mm=33)
        crit = experiment.criterion_for("A", groups, None, settings)
        self.assertEqual(crit["a_limit_g"], 3.0)
        self.assertAlmostEqual(crit["critical_speed_mps"], math.sqrt(9.80665 * 0.083), places=3)

    def test_rejects_invalid_output(self):
        from app.main import app
        with TestClient(app) as client:
            r = client.post("/api/experiment/auto-run", json={"condition": {"base_mass_g": 1000, "output_value": 0}})
            self.assertEqual(r.status_code, 422)


if __name__ == "__main__":
    unittest.main()


class ReturnTapeStopTests(unittest.TestCase):
    def test_return_stops_when_start_tape_seen(self):
        import asyncio, time as _t
        from types import SimpleNamespace
        from app import experiment as ex
        from app.line_follow import LineSettings
        t0 = _t.monotonic()
        async def drive(*a, **k): return True
        adapter = SimpleNamespace(read_grayscale=lambda: [300, 300, 300] if _t.monotonic() - t0 > 0.35 else [900, 900, 900])
        ctl = SimpleNamespace(stop_generation=1, experiment_drive=drive, adapter=adapter)
        settings = SimpleNamespace(line=LineSettings(floor=[900, 900, 900], line=[300, 300, 300]))
        seen = ex._tape_detector(settings)
        out = asyncio.run(ex._drive_for(ctl, "pwm", 50, 2.0, "backward", 1, False, 0.0, stop_when=seen, min_s=0.1))
        self.assertTrue(out["stopped_on_mark"])
        self.assertLess(out["elapsed_s"], 1.0)
        self.assertIsNone(ex._tape_detector(SimpleNamespace(line=LineSettings())))


class HarmonizeTests(unittest.TestCase):
    def test_outlier_pair_is_repicked_to_match_condition(self):
        def run(i, front, rear, extra=()):
            cands = [{"t_s": front, "g": 3.0, "landing": False}, {"t_s": rear, "g": 2.0, "landing": False}] + [
                {"t_s": t, "g": g, "landing": False} for t, g in extra]
            cands.sort(key=lambda c: c["t_s"])
            dt = rear - front
            return {"id": f"r{i}", "condition": {"bump_id": "A", "total_mass_g": 600, "output_mode": "pwm", "output_value": 50},
                    "analysis": {"bump_detected": True, "pair_source": "auto", "front_t_s": front, "rear_t_s": rear, "axle_dt_s": dt,
                                 "speed_mps": 0.095 / dt, "candidates": cands, "params": {"wheelbase_mm": 95}, "flags": []}}
        runs = [run(1, 1.0, 1.38), run(2, 1.1, 1.47), run(3, 0.9, 1.29),
                run(4, 1.0, 1.95, extra=[(1.37, 1.8)])]        # 뒷바퀴를 0.95 s 뒤 진동으로 잘못 고름
        out = {r["id"]: r for r in ia.harmonize_pairs(runs)}
        fixed = out["r4"]["analysis"]
        self.assertEqual(fixed["pair_source"], "consistency")
        self.assertAlmostEqual(fixed["axle_dt_s"], 0.37, places=3)
        self.assertAlmostEqual(fixed["speed_mps"], 0.095 / 0.37, places=3)
        self.assertEqual(runs[3]["analysis"]["axle_dt_s"], 0.95)   # 원본은 그대로
        self.assertEqual(out["r1"]["analysis"]["pair_source"], "auto")


class HeadingControllerTests(unittest.TestCase):
    def _sim(self, true_sign, step_at=None):
        from app.experiment import HeadingController
        c = HeadingController(1.2, 0.8, 0.0, polarity=1)
        h, dt = 0.0, 0.05
        for i in range(int(2.5 / dt)):
            st = c.update(h, dt, 1.0)
            h += (-true_sign * st * 2.0 + 4.0) * dt   # 오른쪽 조향 → 시계 방향, 바퀴 정렬 불량으로 +4°/s
            if step_at is not None and i == step_at:
                h += 6.0                               # 방지턱 충격으로 방향이 갑자기 틀어짐
        return h, c

    def test_correct_sign_holds_and_is_not_flipped(self):
        h, c = self._sim(1, step_at=20)
        self.assertFalse(c.flipped)
        self.assertLess(abs(h), 2.0)

    def test_wrong_sign_is_detected_and_recovered(self):
        h, c = self._sim(-1)
        self.assertTrue(c.flipped)
        self.assertLess(abs(h), 3.0)


class FourImpactPatternTests(unittest.TestCase):
    def _run(self, v=0.3, L=0.095, w=0.06, amps=(1.5, 1.2, 1.3, 1.0), noise=0.03, seed=2):
        import math, random
        rng = random.Random(seed)
        t0 = 1.2
        times = [t0, t0 + w / v, t0 + L / v, t0 + (L + w) / v]
        t, g = [], []
        for i in range(3000):
            x = i / 1000
            y = 1 + rng.gauss(0, noise)
            for c, a in zip(times, amps):
                y += a * math.exp(-((x - c) / 0.006) ** 2)
            t.append(x); g.append(y)
        return {"t_s": t, "vertical_g": g, "dynamic_g": [0.0] * len(t)}, times

    def test_narrow_bump_four_impacts_gives_true_speed(self):
        cols, times = self._run(v=0.3, w=0.03)
        r = ia.analyze_run(cols, wheelbase_mm=95, mass_g=600, bump_width_mm=30, detect_level="sensitive")
        self.assertEqual(r["pair_source"], "pattern")
        self.assertAlmostEqual(r["speed_mps"], 0.3, delta=0.02)
        self.assertAlmostEqual(r["rear_t_s"], times[2], delta=0.01)

    def test_wide_bump_rear_climbs_before_front_descends(self):
        cols, times = self._run(v=0.4, w=0.25)
        r = ia.analyze_run(cols, wheelbase_mm=95, mass_g=600, bump_width_mm=250, detect_level="sensitive")
        self.assertAlmostEqual(r["speed_mps"], 0.4, delta=0.03)
        self.assertAlmostEqual(r["rear_t_s"], times[2], delta=0.01)


class PitchTimingTests(unittest.TestCase):
    def _cols(self, wide: bool, v=0.24, L=0.095, w=0.15, seed=4):
        import math, random
        rng = random.Random(seed)
        tf = 1.5
        tr = tf + L / v
        fd = tf + w / v          # 앞바퀴 내려옴
        rd = fd + L / v          # 뒷바퀴 내려옴
        def pitch(x):            # 앞만 위 +6°, 뒤만 위 −6°, 둘 다 위/아래 0°
            front_on = tf <= x < fd
            rear_on = tr <= x < rd
            return 6.0 * (front_on - rear_on)
        t, g, gy, pv = [], [], [], 0.0
        prev = 0.0
        for i in range(3500):
            x = i / 1000
            p = sum(pitch(x - k * 0.002) for k in range(25)) / 25   # 50 ms에 걸쳐 부드럽게 기울어짐
            gy.append((p - prev) * 1000 + rng.gauss(0, 0.3)); prev = p
            y = 1 + rng.gauss(0, 0.05)
            for c, a in ((tf, 1.2), (fd, 1.0), (tr, 0.9), (rd, 0.8)):
                y += a * math.exp(-((x - c) / 0.006) ** 2)
            y += 0.15 * math.sin(2 * math.pi * 30 * x) * (x > 1.0)
            t.append(x); g.append(y)
        n = len(t)
        cols = {"t_s": t, "vertical_g": g, "dynamic_g": [0.0] * n, "ax_g": [0.0] * n, "ay_g": [0.0] * n, "az_g": [1.0] * n,
                "gx_dps": [rng.gauss(0, 0.3) for _ in t], "gy_dps": gy, "gz_dps": [rng.gauss(0, 0.3) for _ in t]}
        return cols, tf, tr

    def test_wide_bump_speed_from_pitch(self):
        cols, tf, tr = self._cols(wide=True, w=0.15)
        r = ia.analyze_run(cols, wheelbase_mm=95, mass_g=600, bump_width_mm=150)
        self.assertEqual(r["pair_source"], "pitch")
        self.assertAlmostEqual(r["speed_mps"], 0.24, delta=0.03)

    def test_narrow_bump_speed_from_pitch(self):
        cols, tf, tr = self._cols(wide=False, w=0.03)
        r = ia.analyze_run(cols, wheelbase_mm=95, mass_g=600, bump_width_mm=30)
        self.assertEqual(r["pair_source"], "pitch")
        self.assertAlmostEqual(r["speed_mps"], 0.24, delta=0.03)

    def test_summary_export(self):
        from app.main import app
        with TestClient(app) as client:
            r = client.get("/api/experiment/export/summary.csv")
            self.assertEqual(r.status_code, 200)
            self.assertIn("통과 속도", r.text)


class BumpPassWatcherTests(unittest.TestCase):
    def _feed(self, profile):
        from app.experiment import BumpPassWatcher
        w = BumpPassWatcher()
        for i in range(400):
            t = i * 0.01
            if w.update((profile(t), 0.3 * profile(t)), t):
                return t, w
        return None, w

    def test_wide_and_narrow_bumps_end_after_rear_wheel(self):
        def wide(t):   # 앞 오름 1.0, 뒤 오름 1.4(수평), 앞 내림 1.6(−), 뒤 내림 2.0(수평)
            return 6.0 if 1.0 <= t < 1.4 else (-6.0 if 1.6 <= t < 2.0 else 0.0)
        def narrow(t):  # 앞 오름 1.0, 앞 내림 1.1, 뒤 오름 1.4, 뒤 내림 1.5
            return 6.0 if 1.0 <= t < 1.1 else (-6.0 if 1.4 <= t < 1.5 else 0.0)
        tw, _ = self._feed(wide)
        tn, _ = self._feed(narrow)
        self.assertTrue(2.0 <= tw <= 2.2, tw)
        self.assertTrue(1.5 <= tn <= 1.7, tn)

    def test_flat_floor_never_stops(self):
        import math
        t, _ = self._feed(lambda t: 0.8 * math.sin(7 * t))
        self.assertIsNone(t)

    def test_negative_mount_sign(self):
        t, w = self._feed(lambda t: -5.0 if 1.0 <= t < 1.3 else (5.0 if 1.5 <= t < 1.8 else 0.0))
        self.assertIsNotNone(t)
        self.assertEqual(w.sign, -1.0)


class BackwardMeasureTests(unittest.TestCase):
    def test_backward_runs_are_analyzed_separately(self):
        from app.main import app
        import time as _t
        with TestClient(app) as client:
            s = client.get("/api/experiment/settings").json()
            s.update(wheelbase_mm=95, wheel_radius_mm=25, base_mass_g=600, analysis_direction="backward")
            client.put("/api/experiment/settings", json=s)
            for out in (50, 90):
                body = {"condition": {"trial": 1, "bump_id": "A", "base_mass_g": 600, "added_mass_g": 0, "output_mode": "pwm",
                                      "output_value": out, "direction": "backward"},
                        "pre_roll_s": 0.3, "run_seconds": 0.3, "tail_s": 0.1, "repeats": 1, "return_enabled": True}
                self.assertEqual(client.post("/api/experiment/auto-run", json=body).status_code, 200)
                for _ in range(200):
                    st = client.get("/api/experiment/auto-run").json()
                    if st["state"] in ("done", "error", "aborted"):
                        break
                    _t.sleep(0.05)
                self.assertEqual(st["state"], "done", st["message"])
            data = client.get("/api/experiment/runs").json()
            self.assertTrue(data["groups"])
            self.assertTrue(all(g["direction"] == "backward" for g in data["groups"]))
            s["analysis_direction"] = "forward"
            client.put("/api/experiment/settings", json=s)
            data = client.get("/api/experiment/runs").json()
            self.assertFalse(any(g["direction"] == "backward" for g in data["groups"]))
