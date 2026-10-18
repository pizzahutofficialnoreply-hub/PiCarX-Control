import random
import time
import unittest
from unittest.mock import patch

from app.mpu6050 import MPU6050Service


class FakeBus:
    """정지 상태(센서가 뒤집혀 Z = −1 g)에서 가끔 충돌 피크를 내는 가짜 MPU-6050."""
    def __init__(self):
        self.calls = 0
        self.spike = False

    def write_byte_data(self, *args):
        pass

    def read_i2c_block_data(self, addr, reg, length):
        self.calls += 1
        az = -1.0 - (2.0 if self.spike else 0.0)  # 뒤집힌 장착: 충돌 시 더 음수
        # 실제 센서처럼 몇 LSB 잡음을 넣습니다(정확히 0인 값은 통신 오류로 걸러지므로).
        n = lambda: random.randint(3, 9)  # noqa: E731
        w = lambda v: [(v & 0xFFFF) >> 8, v & 0xFF]  # noqa: E731
        raw = int(az * 4096) + n()
        g = int(getattr(self, "gz", 0.0) * 65.5) + n()
        return w(n()) + w(n()) + w(raw) + [0, 0] + w(n()) + w(n()) + w(g)

    def close(self):
        pass


class MPU6050ServiceTests(unittest.TestCase):
    def test_heading_integrates_yaw_after_reset(self):
        bus = FakeBus()
        bus.gz = 0.0
        service = MPU6050Service()
        with patch.object(MPU6050Service, "_open_bus", return_value=bus):
            service.start()
            service.calibrate()
            time.sleep(0.5)
            self.assertTrue(service.heading_reset())
            bus.gz = -20.0  # 뒤집힌 장착: 센서 −20 °/s = 실제 반시계 +20 °/s
            time.sleep(0.5)
            heading = service.heading_deg()
            service.close()
        self.assertGreater(heading, 7)
        self.assertLess(heading, 13)

    def test_inverted_mount_still_reports_vertical_one_g_and_peaks(self):
        bus = FakeBus()
        service = MPU6050Service()
        with patch.object(MPU6050Service, "_open_bus", return_value=bus):
            self.assertTrue(service.start())
            status = service.calibrate()
            self.assertTrue(status["calibrated"], status["error"])
            self.assertEqual(status["vertical_axis"], "z")
            service.start_recording()
            time.sleep(0.15)
            bus.spike = True
            time.sleep(0.01)
            bus.spike = False
            time.sleep(0.15)
            service.stop_recording()
            data = service.recording_columns()
            service.close()
        cols = data["columns"]
        self.assertGreater(data["count"], 50)
        self.assertAlmostEqual(cols["vertical_g"][0], 1.0, places=3)
        self.assertAlmostEqual(max(cols["vertical_g"]), 3.0, places=2)
        self.assertEqual(cols["t_s"][0] >= 0, True)
        live = service.status()
        self.assertLessEqual(len(live["recorded_samples"]), 400)
        csv_text = service.recording_csv()
        self.assertEqual(len(csv_text.strip().splitlines()) - 1, data["count"])
        self.assertLessEqual(len(live["samples"]), MPU6050Service.LIVE_POINTS)


if __name__ == "__main__":
    unittest.main()
