import math
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import experiment, model_fit as mf
from app.line_follow import LineSettings, LineTracker, position, weights


class ModelFitTests(unittest.TestCase):
    def pts(self, n=1.0, p=0.0, noise=0.0):
        import random
        rng = random.Random(1)
        out = []
        for m in (1.0, 1.15, 1.3):
            for v in (0.3, 0.45, 0.6, 0.75, 0.9):
                out.append((v, m, 1.5 * v ** n * m ** p * (1 + rng.gauss(0, noise))))
        return out

    def test_recovers_exponents_with_confidence_interval(self):
        cmp = mf.compare_models(self.pts(n=1.7, p=-0.2, noise=0.03))
        power = next(m for m in cmp["models"] if m["id"] == "power_vm")
        n = next(p for p in power["params"] if p["name"] == "n")
        self.assertLess(n["ci95"][0], 1.7)
        self.assertGreater(n["ci95"][1], 1.7)
        self.assertIsNotNone(power["loocv_rmse"])
        self.assertIn(cmp["best"], [m["id"] for m in cmp["models"]])

    def test_only_report_formulas_and_mass_formula_chosen(self):
        cmp = mf.compare_models(self.pts(n=2.0, p=0.0, noise=0.0))
        self.assertEqual({m["id"] for m in cmp["models"]}, {"power_vm", "power_v"})
        self.assertEqual(mf.choose(cmp, "auto")["id"], "power_vm")
        one = mf.compare_models([(v, 0.6, 3 * v ** 2) for v in (0.2, 0.25, 0.3, 0.35)])
        self.assertEqual([m["id"] for m in one["models"]], ["power_v"])
        self.assertAlmostEqual(mf.choose(one, "auto")["coef"]["n"], 2.0, places=6)

    def test_output_speed_uses_measured_table(self):
        groups = [{"bump_id": "b", "output_mode": "pwm", "output_value": o, "total_mass_g": 600, "mean_speed_mps": v}
                  for o, v in ((40, 0.24), (60, 0.31), (80, 0.355), (100, 0.377))]
        models = mf.speed_models(groups)
        self.assertAlmostEqual(mf.speed_from_output(models, "b", "pwm", 90, 600), 0.366, places=3)
        self.assertAlmostEqual(mf.output_from_speed(models, "b", "pwm", 0.366, 600), 90, places=3)
        self.assertAlmostEqual(mf.output_from_speed(models, "b", "pwm", 0.275, 600), 50, places=3)

    def test_solve_speed_inverts_prediction(self):
        model = {"id": "power_vm", "kind": "power", "coef": {"k0": 2.0, "n": 1.5, "p": -0.2}}
        v = mf.solve_speed(model, 1.0, 1.15)
        self.assertAlmostEqual(mf.predict(model, v, 1.15), 1.0, places=3)
        lin = {"id": "linear", "kind": "linear", "coef": {"a": 0.2, "b": 2.0, "c": 0.0}}
        self.assertAlmostEqual(mf.solve_speed(lin, 1.0, 1.0), 0.4, places=3)

    def test_output_for_speed(self):
        groups = [{"bump_id": "A", "total_mass_g": 1000, "output_mode": "pwm", "output_value": o, "mean_speed_mps": 0.01 * o}
                  for o in (40, 60, 80)]
        maps = mf.speed_maps(groups)
        rec = mf.output_for_speed(maps, "A", 1075, "pwm", 0.5)
        self.assertAlmostEqual(rec["output"], 50, places=1)
        self.assertFalse(rec["extrapolated"])

    def test_interpretation_mentions_numbers(self):
        pts = self.pts(n=1.7, p=-0.2, noise=0.02)
        groups = [{"bump_id": "A", "total_mass_g": round(m * 1000), "mean_speed_mps": v, "mean_peak_delta_g": d, "jump_count": 0,
                   "output_mode": "pwm", "output_value": 0} for v, m, d in pts]
        cmp = mf.compare_models(pts)
        lines = mf.interpret("A", groups, cmp, mf.choose(cmp, "power_vm"), {"critical_speed_mps": 0.9, "a_limit_g": None})
        self.assertTrue(any("n =" in line for line in lines))
        self.assertTrue(any("점프는 없었다" in line for line in lines))


class LineTests(unittest.TestCase):
    def test_position_and_steering(self):
        s = LineSettings(floor=[1200, 1200, 1200], line=[300, 300, 300])
        self.assertAlmostEqual(position(weights([1200, 300, 1200], s), s), 0.0)
        self.assertGreater(position(weights([1200, 900, 300], s), s), 0.5)   # 테이프가 오른쪽
        self.assertIsNone(position(weights([1200, 1200, 1200], s), s))       # 놓침
        tracker = LineTracker(s)
        steer, ok = tracker.update([1200, 900, 300])
        self.assertGreater(steer, 0)
        self.assertTrue(ok)
        steer_back, _ = tracker.update([1200, 900, 300], "backward")
        self.assertLess(steer_back, 0)


class SymmetryAnalysisTests(unittest.TestCase):
    def test_integrated_speeds_and_ratio(self):
        cols = {k: [] for k in ("t_s", "ax_g", "ay_g", "az_g")}
        dt = 0.001
        v = 0.0
        t = 0.0
        phases = {"forward": (0.5, 1.5), "backward": (2.5, 3.5)}
        while t < 4.5:
            target = 0.5 if 0.5 <= t < 1.5 else -0.65 if 2.5 <= t < 3.5 else 0.0
            a = (target - v) / 0.15
            v += a * dt
            cols["t_s"].append(t); cols["ax_g"].append(a / 9.80665); cols["ay_g"].append(0.0); cols["az_g"].append(1.0)
            t += dt
        res = experiment.symmetry_analysis(cols, [0, 0, 1.0], "z", phases)
        self.assertEqual(res["axis"], "x")
        self.assertAlmostEqual(res["forward"]["peak_speed_mps"], 0.5, delta=0.03)
        self.assertAlmostEqual(res["backward"]["peak_speed_mps"], 0.65, delta=0.04)
        self.assertAlmostEqual(res["ratio_backward_to_forward"], 1.3, delta=0.08)


class DataManagementApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.p = patch.object(experiment, "store", experiment.ExperimentStore(Path(self.tmp.name)))
        self.p.start()
        experiment.auto_runner.state = "idle"

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def _run(self, client, **cond):
        body = {"condition": {"base_mass_g": 1000, "output_value": 60, **cond}, "pre_roll_s": 0.3, "run_seconds": 0.3,
                "tail_s": 0.1, "repeats": 1, "return_enabled": False}
        self.assertEqual(client.post("/api/experiment/auto-run", json=body).status_code, 200)
        for _ in range(100):
            st = client.get("/api/experiment/auto-run").json()
            if st["state"] in ("done", "error", "aborted"):
                return st
            time.sleep(0.03)

    def test_override_bulk_delete_reset_restore_validation(self):
        from app.main import app
        with TestClient(app) as client:
            s = client.get("/api/experiment/settings").json()
            s.update(wheelbase_mm=95, wheel_radius_mm=33)
            s["bumps"] = [{k: b[k] for k in ("id", "name", "height_mm", "width_mm")} for b in s["bumps"]]
            s.pop("max_output")
            client.put("/api/experiment/settings", json=s)
            for out in (40, 60, 80):
                for _ in range(2):
                    self._run(client, output_value=out)
            st = self._run(client, output_value=70, purpose="validation")
            runs = client.get("/api/experiment/runs").json()
            self.assertEqual(len(runs["runs"]), 7)
            self.assertEqual(sum(g["n_runs"] for g in runs["groups"]), 6)  # 검증 시행은 회귀에서 제외
            self.assertIn("A", runs["models"])
            self.assertTrue(runs["interpretation"]["A"])
            self.assertEqual(runs["validation"]["summary"]["count"], 1)
            self.assertIsNotNone(runs["validation"]["rows"][0]["predicted_delta_g"])
            self.assertTrue(runs["speed_maps"])
            self.assertEqual(runs["progress"]["need"], 2 * 3 * 5 * 5)

            run = runs["runs"][1]
            a = run["analysis"]
            self.assertEqual(a["pair_source"], "auto")
            self.assertGreaterEqual(len(a["candidates"]), 2)
            body = {"peak_override": {"front_t_s": a["front_t_s"], "rear_t_s": a["rear_t_s"]}}
            res = client.patch(f"/api/experiment/runs/{run['id']}", json=body).json()
            self.assertEqual(res["analysis"]["pair_source"], "manual")
            self.assertAlmostEqual(res["analysis"]["speed_mps"], a["speed_mps"], places=3)
            res = client.patch(f"/api/experiment/runs/{run['id']}", json={"clear_peak_override": True}).json()
            self.assertEqual(res["analysis"]["pair_source"], "auto")
            bad = client.patch(f"/api/experiment/runs/{run['id']}", json={"peak_override": {"front_t_s": 2, "rear_t_s": 1}})
            self.assertEqual(bad.status_code, 422)

            ids = [r["id"] for r in runs["runs"][:2]]
            self.assertEqual(len(client.post("/api/experiment/runs/delete", json={"ids": ids}).json()["deleted"]), 2)
            self.assertEqual(client.post("/api/experiment/reset", json={"confirm": "no"}).status_code, 422)
            moved = client.post("/api/experiment/reset", json={"confirm": "초기화"}).json()
            self.assertEqual(moved["moved"], 5)
            self.assertEqual(client.get("/api/experiment/runs").json()["runs"], [])
            batch = client.get("/api/experiment/trash").json()["batches"][0]["batch"]
            self.assertEqual(client.post(f"/api/experiment/trash/{batch}/restore").json()["restored"], 5)
            self.assertEqual(len(client.get("/api/experiment/runs").json()["runs"]), 5)
            zip_res = client.get("/api/experiment/backup.zip")
            self.assertEqual(zip_res.headers["content-type"], "application/zip")
            check = client.get("/api/experiment/data-check").json()
            self.assertEqual(check["simulated_runs"], 5)
            self.assertFalse(check["checks"][0]["ok"])
            self.assertIn("평가 수", client.get("/api/experiment/export/validation.csv").text)

    def test_auto_run_with_line_steering(self):
        from app.main import app
        with TestClient(app) as client:
            body = {"condition": {"base_mass_g": 1000, "output_value": 60}, "pre_roll_s": 0.3, "run_seconds": 0.3,
                    "tail_s": 0.1, "repeats": 1, "return_enabled": False, "steering_source": "line"}
            client.post("/api/experiment/auto-run", json=body)
            st = self._wait(client)
            self.assertEqual(st["state"], "error")
            self.assertIn("보정", st["message"])
            client.put("/api/experiment/line/settings", json={"floor": [1200, 1200, 1200], "line": [300, 300, 300]})
            status = client.get("/api/experiment/line/status").json()
            self.assertTrue(status["calibrated"])
            self.assertIsNotNone(status["position"])
            client.post("/api/experiment/auto-run", json=body)
            st = self._wait(client)
            self.assertEqual(st["state"], "done", st["message"])
            self.assertEqual(st["result"]["steering_source"], "line")

    def _wait(self, client):
        for _ in range(100):
            st = client.get("/api/experiment/auto-run").json()
            if st["state"] in ("done", "error", "aborted"):
                return st
            time.sleep(0.03)

    def test_drive_settings_and_camera_view(self):
        from app.main import app
        with TestClient(app) as client:
            r = client.put("/api/settings/drive", json={"mapping": "duty", "trim_backward": 0.8}).json()
            self.assertEqual(r["mapping"], "duty")
            self.assertEqual(r["max_pwm"], 100)
            client.post("/api/drive", json={"direction": "backward", "speed": 100, "steering": 0})
            status = client.get("/api/status").json()
            self.assertEqual(status["last_motor_command"]["applied"], 80)
            self.assertEqual(status["last_motor_command"]["mapping"], "duty")
            client.put("/api/settings/drive", json={"mapping": "library", "trim_backward": 1.0})
            v = client.post("/api/camera/view", json={"pan_delta": 4}).json()
            self.assertEqual(v["pan"], 4)
            v = client.post("/api/camera/view", json={"pan_delta": 4, "tilt_delta": -3}).json()
            self.assertEqual((v["pan"], v["tilt"]), (8, -3))
            self.assertEqual(client.post("/api/drive", json={"direction": "forward", "speed": 101, "steering": 0}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
