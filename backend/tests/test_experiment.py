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
