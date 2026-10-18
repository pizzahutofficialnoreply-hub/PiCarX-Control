"""가짜 smbus2(MPU-6050 응답만 흉내). 개발·시험 전용."""
import random
import threading
import time

import fake_state as fs


def _word(value: float, scale: float) -> list[int]:
    raw = max(-32768, min(32767, int(round(value * scale)))) & 0xFFFF
    return [raw >> 8, raw & 0xFF]


def _frame(now, with_temp=True):
    fs.update(now)
    az = fs.vertical_g(now)
    gz = fs.state["yaw_rate"] + fs.GYRO_BIAS_DPS + random.gauss(0, 0.3)
    ax = fs.state["accel"] / 9.80665 + random.gauss(0, 0.01)
    gy = fs.state.get("pitch_rate", 0.0) + random.gauss(0, 0.3)
    out = _word(ax, 4096) + _word(random.gauss(0, 0.01), 4096) + _word(az, 4096)
    if with_temp:
        out += [0, 0]
    return out + _word(random.gauss(0, 0.3), 65.5) + _word(gy, 65.5) + _word(gz, 65.5)


class SMBus:
    """MPU-6050 흉내: 0x3B 직접 읽기와 FIFO(0x72 개수, 0x74 데이터)를 지원합니다."""

    def __init__(self, bus=1):
        self.div = 0
        self.fifo_on = False
        self.buf = []
        self.fifo_t = time.monotonic()
        self._lock = threading.Lock()
        self._thread = None

    def _run(self):
        # 실제 센서처럼 '그 순간의 상태'로 매 표본을 쌓음(읽으러 올 때 몰아서 만들면 시간축이 뒤틀림)
        while self.fifo_on:
            with self._lock:
                self._fill()
            time.sleep(0.0005)

    def write_byte_data(self, addr, reg, value):
        if reg == 0x19:
            self.div = value
        elif reg == 0x23:
            self.fifo_on = value != 0
            self.fifo_t = time.monotonic()
            if self.fifo_on and (self._thread is None or not self._thread.is_alive()):
                self._thread = threading.Thread(target=self._run, daemon=True)
                self._thread.start()
        elif reg == 0x6A and value & 0x04:
            with self._lock:
                self.buf = []
                self.fifo_t = time.monotonic()

    def _fill(self):
        if not self.fifo_on:
            return
        dt = 0.001 * (1 + self.div)
        now = time.monotonic()
        while self.fifo_t + dt <= now:
            self.fifo_t += dt
            if len(self.buf) + 12 > 1024:
                continue  # 넘침: 버림(실제 센서처럼)
            self.buf += _frame(self.fifo_t, with_temp=False)

    def read_i2c_block_data(self, addr, reg, length):
        if reg == 0x72:
            with self._lock:
                self._fill()
            n = 1024 if len(self.buf) + 12 > 1024 else len(self.buf)
            return [n >> 8, n & 0xFF]
        if reg == 0x74:
            time.sleep(0.0002 * length / 12)
            with self._lock:
                out, self.buf = self.buf[:length], self.buf[length:]
            return out + [0] * (length - len(out))
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
