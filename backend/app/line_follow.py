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
    damping: float = Field(default=0.25, ge=0, le=1, description="감쇠(미분) 계수 s: 위치가 빨리 변하면 미리 반대로 조향해 흔들림을 줄임")
    max_steer_deg: float = Field(default=25, ge=5, le=30)
    min_weight: float = Field(default=0.25, ge=0.05, le=0.9, description="이보다 작으면 테이프를 놓친 것으로 봄")
    lost_timeout_s: float = Field(default=0.6, ge=0.1, le=3)
    auto_sign: bool = Field(default=True, description="조향 방향 자동 판정 켜기")
    steer_sign: int = Field(default=1, description="테이프가 오른쪽일 때 오른쪽(+1)/왼쪽(−1)으로 꺾기. 센서 순서가 반대인 차는 −1(주행 중 자동 판정)")


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
    """테이프 위치 → 조향각. 놓친 시간과 마지막 위치를 기억합니다.

    조향 방향 자동 판정: 테이프 쪽으로 크게(최대의 50% 이상) 꺾는데도 0.3초 동안 테이프가 더 멀어지면
    센서 순서가 반대인 것이므로 조향 방향을 뒤집습니다(``flipped``).
    """

    def __init__(self, settings: LineSettings) -> None:
        self.s = settings
        self.sign = -1 if settings.steer_sign < 0 else 1
        self.last_pos = 0.0
        self.lost_since: float | None = None
        self.last: dict[str, Any] = {}
        self.prev: tuple[float, float] | None = None  # (시각, 위치)
        self.rate = 0.0
        self.wrong_since: float | None = None
        self.wrong_pos = 0.0
        self.flipped = False
        self.recover_until = 0.0

    def update(self, raw: list[float] | None, direction: str = "forward", now: float | None = None) -> tuple[float, bool]:
        """(조향각, 계속 가도 되는지). 후진이면 조향 부호가 반대입니다."""
        w = weights(raw or [], self.s)
        pos = position(w, self.s)
        now = time.monotonic() if now is None else now
        if pos is None:
            self.lost_since = self.lost_since or now
            pos_used = max(-1.0, min(1.0, self.last_pos * 1.5))
            ok = now - self.lost_since < self.s.lost_timeout_s or now < self.recover_until
        else:
            self.lost_since = None
            self.last_pos = pos
            pos_used = pos
            ok = True
        if pos is not None and self.prev is not None and now > self.prev[0]:
            inst = (pos - self.prev[1]) / (now - self.prev[0])
            self.rate = 0.6 * self.rate + 0.4 * inst  # 변화율은 살짝 부드럽게(센서 잡음 때문)
        if pos is not None:
            self.prev = (now, pos)
        frac = self.s.gain * pos_used + self.s.damping * self.rate
        steer = max(-self.s.max_steer_deg, min(self.s.max_steer_deg, frac * self.s.max_steer_deg)) * self.sign
        # 조향 방향 점검(전진, 자동 판정 켜짐): 테이프 쪽으로 크게 꺾는데 0.35초가 지나도 테이프가 가운데로 오지 않거나
        # 그 사이 테이프를 놓치면 센서 순서가 반대인 것으로 보고 조향 방향을 뒤집습니다.
        if self.s.auto_sign and direction == "forward" and not self.flipped:
            side = pos if pos is not None else self.last_pos
            hard = abs(steer) >= 0.5 * self.s.max_steer_deg and abs(side) >= 0.6
            if hard:
                if self.wrong_since is None or (side > 0) != (self.wrong_pos > 0):
                    self.wrong_since, self.wrong_pos = now, side
                elif now - self.wrong_since >= 0.35 and (pos is None or abs(pos) >= abs(self.wrong_pos) - 0.1):
                    self.sign = -self.sign
                    self.flipped = True
                    self.wrong_since = None
                    self.lost_since = None
                    self.recover_until = now + 2.0  # 반대로 꺾어 테이프를 다시 찾을 시간(최대 2초)을 줌
                    ok = True
                    steer = -steer
            else:
                self.wrong_since = None
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
                  seconds: float, on_flip: Callable[[int], None] | None = None) -> None:
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
                await asyncio.sleep(0.02)  # 50 Hz: 조향 반응을 빠르게
            self.state, self.message = "done", f"{seconds:g}초 주행 완료" + (" · 조향 방향을 반대로 바꿨습니다(센서 순서 반대)" if self.tracker.flipped else "")
        except Exception as exc:
            self.state, self.message = "error", f"라인 추종 오류: {exc}"
        finally:
            try:
                await ctl.stop(reason="line_follow_end")
            except Exception:
                pass
            if self.tracker and self.tracker.flipped and on_flip:
                on_flip(self.tracker.sign)


line_runner = LineRunner()
