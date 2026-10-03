"""자율주행 확장 API가 준비되지 않은 기능을 과장하지 않는지 확인합니다."""
import unittest
from fastapi import HTTPException
from app import main


class AutonomyApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_future_modes_are_explicitly_unavailable_and_manual_remains_ready(self):
        old_control = getattr(main, "control", None)
        main.control = main.ControlService("mock")
        try:
            modes = await main.autonomy_modes()
            by_name = {mode["mode"]: mode for mode in modes["modes"]}
            self.assertTrue(by_name["manual"]["available"])
            for name in ("line_following", "obstacle_avoidance", "object_following", "camera_tracking", "autonomous"):
                self.assertFalse(by_name[name]["available"])
                self.assertEqual(by_name[name]["state"], "unavailable")

            capabilities = await main.autonomy_capabilities()
            self.assertFalse(capabilities["camera_stream"])
            self.assertFalse(capabilities["gps"])
            self.assertFalse(capabilities["speed_sensor"])
            self.assertFalse(capabilities["line_following"])
            with self.assertRaises(HTTPException) as rejected:
                await main.autonomy_start(main.AutonomyStart(mode="line_following"))
            self.assertEqual(rejected.exception.status_code, 409)
            self.assertEqual((await main.autonomy_stop())["state"], "stopped")
            self.assertIn("events", await main.logs())
        finally:
            main.control.adapter.close()
            if old_control is not None:
                main.control = old_control


if __name__ == "__main__":
    unittest.main()
