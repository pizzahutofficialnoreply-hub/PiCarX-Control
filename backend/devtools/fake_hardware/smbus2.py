"""가짜 smbus2(MPU-6050 응답만 흉내). 개발·시험 전용."""
import random
import time

import fake_state as fs


def _word(value: float, scale: float) -> list[int]:
    raw = max(-32768, min(32767, int(round(value * scale)))) & 0xFFFF
    return [raw >> 8, raw & 0xFF]


class SMBus:
    def __init__(self, bus=1):
        pass

    def write_byte_data(self, addr, reg, value):
        pass

    def read_i2c_block_data(self, addr, reg, length):
        time.sleep(0.0004)  # 실제 I²C 읽기 시간 흉내(400 kHz)
        now = time.monotonic()
        fs.update(now)
        az = fs.vertical_g(now)
        gz = fs.state["yaw_rate"] + fs.GYRO_BIAS_DPS + random.gauss(0, 0.3)
        ax = fs.state["accel"] / 9.80665 + random.gauss(0, 0.01)
        out = _word(ax, 4096) + _word(random.gauss(0, 0.01), 4096) + _word(az, 4096)
        out += [0, 0]
        gy = fs.state.get("pitch_rate", 0.0) + random.gauss(0, 0.3)
        out += _word(random.gauss(0, 0.3), 65.5) + _word(gy, 65.5) + _word(gz, 65.5)
        return out[:length]

    def close(self):
        pass
