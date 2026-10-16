"""바닥 테이프(라인) 추종: PiCar-X 아래 3채널 그레이스케일 센서로 조향합니다.

- 보정: 바닥 위(``floor``)와 테이프 위(``line``)에서 센서값을 한 번씩 저장합니다.
  밝은 바닥에 검은 테이프(값이 작아짐)와 어두운 바닥에 흰 테이프(값이 커짐) 모두 지원합니다.
- 위치: 각 센서가 '테이프에 얼마나 가까운지'(0~1)를 가중치로 왼쪽 −1, 가운데 0, 오른쪽 +1 위치를 냅니다.
- 조향: 조향각 = (배율 × 위치 + 감쇠 × 위치 변화율) × 최대 조향각 (테이프가 오른쪽이면 오른쪽으로 꺾음).
  감쇠 항이 없으면(P 제어) 테이프 양옆으로 점점 크게 흔들릴 수 있습니다.
- 테이프를 놓치면(모든 센서 가중치가 작음) 마지막 위치 쪽으로 계속 돌다가 ``lost_timeout_s`` 뒤 정지합니다.

자동 측정 주행에서 '조향 방식 = 라인'으로 고르면 같은 계산을 써서 테이프를 따라 방지턱으로 갑니다.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

from pydantic import BaseModel, Field


class LineSettings(BaseModel):
    floor: list[float] | None = Field(default=None, description="바닥 위 센서값 [L, C, R]")
    line: list[float] | None = Field(default=None, description="테이프 위 센서값 [L, C, R]")
    gain: float = Field(default=0.8, ge=0, le=3)
    damping: float = Field(default=0.15, ge=0, le=1, description="감쇠(미분) 계수 s: 위치가 빨리 변하면 미리 반대로 조향해 흔들림을 줄임")
    max_steer_deg: float = Field(default=25, ge=5, le=30)
    min_weight: float = Field(default=0.25, ge=0.05, le=0.9, description="이보다 작으면 테이프를 놓친 것으로 봄")
    lost_timeout_s: float = Field(default=0.6, ge=0.1, le=3)


def weights(raw: list[float], s: LineSettings) -> list[float] | None:
    if not raw or len(raw) < 3 or not s.floor or not s.line:
        return None
    out = []
    for value, floor, line in zip(raw[:3], s.floor[:3], s.line[:3]):
        span = line - floor
        out.append(0.0 if abs(span) < 1e-6 else max(0.0, min(1.0, (value - floor) / span)))
    return out


def position(w: list[float] | None, s: LineSettings) -> float | None:
    """테이프 위치: −1(왼쪽) ~ +1(오른쪽). 놓치면 None."""
    if w is None or max(w) < s.min_weight:
        return None
    total = sum(w)
    return (-1.0 * w[0] + 0.0 * w[1] + 1.0 * w[2]) / total if total > 0 else None


class LineTracker:
    """테이프 위치 → 조향각. 놓친 시간과 마지막 위치를 기억합니다."""

    def __init__(self, settings: LineSettings) -> None:
        self.s = settings
        self.last_pos = 0.0
        self.lost_since: float | None = None
        self.last: dict[str, Any] = {}
        self.prev: tuple[float, float] | None = None  # (시각, 위치)

    def update(self, raw: list[float] | None, direction: str = "forward", now: float | None = None) -> tuple[float, bool]:
        """(조향각, 계속 가도 되는지). 후진이면 조향 부호가 반대입니다."""
        w = weights(raw or [], self.s)
        pos = position(w, self.s)
        now = time.monotonic() if now is None else now
        if pos is None:
            self.lost_since = self.lost_since or now
            pos_used = max(-1.0, min(1.0, self.last_pos * 1.5))
            ok = now - self.lost_since < self.s.lost_timeout_s
        else:
            self.lost_since = None
            self.last_pos = pos
            pos_used = pos
            ok = True
        rate = 0.0
        if pos is not None and self.prev is not None and now > self.prev[0]:
            rate = (pos - self.prev[1]) / (now - self.prev[0])
        if pos is not None:
            self.prev = (now, pos)
        frac = self.s.gain * pos_used + self.s.damping * rate
        steer = max(-self.s.max_steer_deg, min(self.s.max_steer_deg, frac * self.s.max_steer_deg))
        if direction == "backward":
            steer = -steer
        self.last = {"raw": raw, "weights": w, "position": pos, "steer_deg": round(steer, 2), "lost": pos is None}
        return steer, ok


class LineRunner:
    """'라인 추종' 탭의 단독 주행(측정 없이 테이프만 따라감)."""

    def __init__(self) -> None:
        self.state = "idle"
        self.message = ""
        self.task: asyncio.Task | None = None
        self.tracker: LineTracker | None = None
        self.started: float | None = None
        self.log: list[dict[str, Any]] = []

    @property
    def busy(self) -> bool:
        return self.state == "running"

    def snapshot(self) -> dict[str, Any]:
        return {"state": self.state, "message": self.message,
                "elapsed_s": round(time.monotonic() - self.started, 2) if self.started and self.busy else None,
                "last": self.tracker.last if self.tracker else None,
                "log": self.log[-200:]}

    async def run(self, ctl, read: Callable[[], Any], settings: LineSettings, output_mode: str, output: float,
                  seconds: float) -> None:
        self.tracker = LineTracker(settings)
        self.state, self.message, self.started, self.log = "running", "라인 추종 중", time.monotonic(), []
        gen = ctl.stop_generation
        try:
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                if ctl.stop_generation != gen:
                    self.state, self.message = "stopped", "정지 명령으로 멈췄습니다."
                    return
                raw = await asyncio.to_thread(read)
                steer, ok = self.tracker.update(raw)
                self.log.append({"t": round(time.monotonic() - self.started, 3), **{k: self.tracker.last.get(k) for k in ("position", "steer_deg", "lost")}})
                if not ok:
                    self.state, self.message = "lost", "테이프를 놓쳐 정지했습니다. 센서 보정과 테이프 폭을 확인하세요."
                    return
                if not await ctl.experiment_drive(output_mode, output, gen, "forward", steer):
                    self.state, self.message = "stopped", "정지 명령으로 멈췄습니다."
                    return
                await asyncio.sleep(0.05)
            self.state, self.message = "done", f"{seconds:g}초 주행 완료"
        except Exception as exc:
            self.state, self.message = "error", f"라인 추종 오류: {exc}"
        finally:
            try:
                await ctl.stop(reason="line_follow_end")
            except Exception:
                pass


line_runner = LineRunner()
