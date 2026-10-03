import asyncio
import unittest
from pydantic import ValidationError
from app.main import ControlService
from app.models import DriveCommand, CalibrationUpdate, SteeringTest
from app.hardware import PiCarXAdapter

class ControlSafetyTests(unittest.TestCase):
    def test_calibration_is_saved_and_restored(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        async def scenario(path):
            with patch("app.main.SETTINGS_PATH", path):
                service = ControlService()
                await service.save_calibration(CalibrationUpdate(steering_center=4, pan_center=-12))
                self.assertEqual(service.status()["motion"], "stopped")
                restored = ControlService()
                self.assertEqual(restored.status()["calibration"], 4)
                self.assertEqual(restored.status()["pan_calibration"], -12)
        with TemporaryDirectory() as directory:
            asyncio.run(scenario(Path(directory) / "settings.json"))

    def test_mock_diagnostics_do_not_claim_hardware_was_tested(self):
        diagnostics = ControlService(mode="mock").diagnostics()
        self.assertTrue(diagnostics["server_connected"])
        self.assertEqual(diagnostics["mode"], "mock")
        self.assertIsNone(diagnostics["driver_initialized"])
        self.assertIsNone(diagnostics["motor_control_available"])
        self.assertIsNone(diagnostics["camera_device_available"])

    def test_hardware_init_failure_keeps_status_api_safe(self):
        from unittest.mock import patch
        with patch("app.main.PiCarXAdapter", side_effect=RuntimeError("driver missing")):
            service = ControlService(mode="hardware")
        self.assertFalse(service.status()["connected"])
        self.assertEqual(service.status()["mode"], "hardware")
        self.assertFalse(service.diagnostics()["driver_initialized"])
        self.assertIsNone(service.diagnostics()["camera_device_available"])
        self.assertIn("driver missing", service.diagnostics()["last_error"])
        with self.assertRaises(RuntimeError):
            asyncio.run(service.drive(DriveCommand(direction="forward", speed=10, steering=10)))

    def test_adapter_initializes_against_only_confirmed_api_methods(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from app.hardware import PiCarXAdapter
        class FakePicarx:
            def __init__(self): self.calls = []
            def forward(self, speed): self.calls.append(("forward", speed))
            def backward(self, speed): self.calls.append(("backward", speed))
            def stop(self): self.calls.append(("stop",))
            def set_motor_speed(self, motor, speed): self.calls.append(("motor", motor, speed))
            def set_dir_servo_angle(self, value): self.calls.append(("steer", value))
            def set_cam_pan_angle(self, value): self.calls.append(("pan", value))
            def set_cam_tilt_angle(self, value): self.calls.append(("tilt", value))
            def get_distance(self): return None
            def get_grayscale_data(self): return []
            def get_cliff_status(self): return None
        module = SimpleNamespace(Picarx=FakePicarx, __file__="/usr/local/lib/python3.13/dist-packages/picarx/__init__.py")
        with patch("app.hardware.importlib.util.find_spec", return_value=SimpleNamespace(origin=module.__file__)), \
             patch("app.hardware.importlib.import_module", return_value=module):
            adapter = PiCarXAdapter()
        self.assertTrue(adapter.connected)
        self.assertTrue(adapter.diagnostics()["driver_initialized"])
        self.assertTrue(adapter.diagnostics()["motor_control_available"])
        self.assertTrue(adapter.diagnostics()["steering_servo_available"])
        self.assertIsNone(adapter.diagnostics()["camera_device_available"])
        self.assertEqual(adapter._car.calls, [("stop",), ("steer", 0)])

    def test_official_adapter_maps_and_clamps_commands(self):
        class FakePicarx:
            def __init__(self): self.calls = []
            def stop(self): self.calls.append(("stop",))
            def set_dir_servo_angle(self, angle): self.calls.append(("steer", angle))
            def set_cam_pan_angle(self, angle): self.calls.append(("pan", angle))
            def set_cam_tilt_angle(self, angle): self.calls.append(("tilt", angle))
            def set_motor_speed(self, motor, speed): self.calls.append(("motor", motor, speed))
            def get_distance(self): return None
            def get_grayscale_data(self): return []
            def get_cliff_status(self): return None
            def forward(self, speed): self.calls.append(("forward", speed))
            def backward(self, speed): self.calls.append(("backward", speed))
        adapter = PiCarXAdapter.__new__(PiCarXAdapter)
        adapter._car = FakePicarx()
        adapter.connected = True
        adapter.steering_center = 0
        adapter.drive("left", 99, 35)
        self.assertEqual(adapter._car.calls, [("steer", -30), ("forward", 60)])
        adapter._car.calls.clear()
        adapter.drive("backward", 20, 10)
        self.assertEqual(adapter._car.calls, [("steer", 10), ("backward", 20)])
        adapter._car.calls.clear()
        adapter.set_calibration(4, -12, 9)
        self.assertEqual(adapter._car.calls, [("steer", 4), ("pan", -12), ("tilt", 9)])

    def test_steering_test_stops_motor_before_moving_servo(self):
        async def scenario():
            service = ControlService()
            await service.drive(DriveCommand(direction="forward", speed=20, steering=15))
            await service.test_steering(SteeringTest(direction="left", angle=15))
            self.assertEqual(service.status()["motion"], "stopped")
            self.assertEqual(service.status()["steering"], -15)
        asyncio.run(scenario())

    def test_invalid_commands_are_rejected(self):
        with self.assertRaises(ValidationError):
            DriveCommand(direction="launch", speed=20, steering=10)
        with self.assertRaises(ValidationError):
            DriveCommand(direction="forward", speed=61, steering=10)

    def test_motor_exception_attempts_stop_and_preserves_fault(self):
        async def scenario():
            service = ControlService()
            stop_calls = []
            service.adapter.drive = lambda *_: (_ for _ in ()).throw(RuntimeError("motor fault"))
            original_stop = service.adapter.stop
            service.adapter.stop = lambda: (stop_calls.append(True), original_stop())
            with self.assertRaisesRegex(RuntimeError, "motor fault"):
                await service.drive(DriveCommand(direction="forward", speed=20, steering=10))
            self.assertTrue(stop_calls)
            self.assertEqual(service.status()["motion"], "stopped")
            self.assertEqual(service.status()["fault"], "motor fault")
        asyncio.run(scenario())

    def test_stop_resets_motion_immediately(self):
        async def scenario():
            service = ControlService()
            await service.drive(DriveCommand(direction="forward", speed=30, steering=12))
            await service.stop()
            self.assertEqual(service.status()["motion"], "stopped")
            self.assertEqual(service.status()["speed"], 0)
            service.adapter.close()
        asyncio.run(scenario())

    def test_sensor_reads_use_mock_adapter_and_mark_simulated_mode(self):
        async def scenario():
            service = ControlService(mode="mock")
            values = await service.sensors()
            self.assertEqual(values["mode"], "mock")
            self.assertEqual(values["distance_cm"], 42.0)
            self.assertEqual(len(values["grayscale"]), 3)
            self.assertEqual(len(values["cliff_status"]), 3)
        asyncio.run(scenario())

    def test_signed_steering_is_preserved_for_reverse_drive(self):
        class FakePicarx:
            def __init__(self): self.calls = []
            def stop(self): self.calls.append(("stop",))
            def set_dir_servo_angle(self, angle): self.calls.append(("steer", angle))
            def backward(self, speed): self.calls.append(("backward", speed))
        adapter = PiCarXAdapter.__new__(PiCarXAdapter)
        adapter._car, adapter.connected, adapter.steering_center = FakePicarx(), True, 0
        adapter.drive("backward", 20, -12)
        self.assertEqual(adapter._car.calls, [("steer", -12), ("backward", 20)])

    def test_stop_invalidates_an_older_queued_drive(self):
        async def scenario():
            service = ControlService()
            await service.lock.acquire()
            drive = asyncio.create_task(service.drive(DriveCommand(direction="forward", speed=30, steering=12)))
            await asyncio.sleep(0)
            stop = asyncio.create_task(service.stop())
            await asyncio.sleep(0)
            service.lock.release()
            await asyncio.gather(drive, stop)
            self.assertEqual(service.status()["motion"], "stopped")
            service.adapter.close()
        asyncio.run(scenario())

    def test_watchdog_stops_stale_command(self):
        async def scenario():
            service = ControlService()
            await service.drive(DriveCommand(direction="forward", speed=30, steering=12))
            task = asyncio.create_task(service.watchdog())
            await asyncio.sleep(0.7)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(service.status()["motion"], "stopped")
            service.adapter.close()
        asyncio.run(scenario())

if __name__ == "__main__":
    unittest.main()
