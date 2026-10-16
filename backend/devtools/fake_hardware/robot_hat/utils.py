"""가짜 배터리 전압: 시작 8.2 V에서 분당 0.05 V씩 감소."""
import time

_T0 = time.monotonic()


def get_battery_voltage() -> float:
    return round(8.2 - (time.monotonic() - _T0) / 60 * 0.05, 2)
