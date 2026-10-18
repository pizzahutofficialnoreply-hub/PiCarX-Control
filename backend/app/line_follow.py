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
import math
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
    mode: str = Field(default="gentle", description="gentle: 조향 최소(테이프가 가운데 근처면 직진, 벗어날 때만 조금), normal: 적극적으로 따라감")
    deadband: float = Field(default=0.35, ge=0, le=0.9, description="이 위치(−1~1) 안쪽이면 조향하지 않음(gentle)")
    gentle_max_deg: float = Field(default=8, ge=2, le=30, description="gentle 모드 최대 조향각")
    rate_limit_dps: float = Field(default=60, ge=10, le=600, description="조향각이 1초에 바뀔 수 있는 최대치(급하게 꺾지 않게)")


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
        self.steer_prev = 0.0
        self.t_prev: float | None = None

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
        if self.s.mode == "gentle":
            # 조향 최소: 테이프가 가운데 근처(데드밴드 안)면 바퀴를 똑바로 두고, 벗어난 만큼만 작게 꺾습니다.
            lim = min(self.s.gentle_max_deg, self.s.max_steer_deg)
            mag = max(0.0, abs(pos_used) - self.s.deadband) / max(1e-6, 1.0 - self.s.deadband)
            e = mag if pos_used >= 0 else -mag
            frac = self.s.gain * e + (0.5 * self.s.damping * self.rate if mag > 0 else 0.0)
            target = max(-lim, min(lim, frac * lim)) * self.sign
        else:
            lim = self.s.max_steer_deg
            frac = self.s.gain * pos_used + self.s.damping * self.rate
            target = max(-lim, min(lim, frac * lim)) * self.sign
        # 조향각 변화 속도 제한(덜컥 꺾지 않게)
        dt = max(0.0, min(0.2, now - self.t_prev)) if self.t_prev is not None else 0.02
        step = self.s.rate_limit_dps * dt if self.s.mode == "gentle" else 1e9
        steer = self.steer_prev + max(-step, min(step, target - self.steer_prev))
        self.steer_prev, self.t_prev = steer, now
        # 조향 방향 점검(전진, 자동 판정 켜짐): 테이프 쪽으로 크게 꺾는데 0.35초가 지나도 테이프가 가운데로 오지 않거나
        # 그 사이 테이프를 놓치면 센서 순서가 반대인 것으로 보고 조향 방향을 뒤집습니다.
        if self.s.auto_sign and direction == "forward" and not self.flipped:
            side = pos if pos is not None else self.last_pos
            hard = abs(steer) >= 0.5 * lim and abs(side) >= 0.6
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
                    self.steer_prev = steer
            else:
                self.wrong_since = None
        if direction == "backward":
            steer = -steer
        self.last = {"raw": raw, "weights": w, "position": pos, "steer_deg": round(steer, 2), "lost": pos is None}
        return steer, ok


class TiltGate:
    """'지금 방지턱 위인가'를 자이로 기울기로 판단(적분 기울기의 느린 흐름은 빼고 봄).

    기울기는 자이로를 적분한 값이라 방지턱을 지난 뒤 정확히 0으로 돌아오지 않고 1~3°씩 남거나 흐를 수 있습니다.
    그대로 쓰면 '계속 방지턱 위'로 판단해 유도선·정지선 판정이 막히므로, 평지에서는 기준값을 천천히(약 1초) 따라가게 하고
    기준에서 ``limit``° 넘게 벗어나면 방지턱 위로 봅니다. 방지턱 위라도 ``max_s``초가 지나면 기준을 지금 값으로 다시 잡습니다.
    """

    def __init__(self, limit_deg: float = 1.5, tau_s: float = 1.0, max_s: float = 1.5) -> None:
        self.limit, self.tau, self.max_s = limit_deg, tau_s, max_s
        self.base: list[float] | None = None
        self.since: float | None = None
        self.t_prev: float | None = None

    def update(self, tilt: tuple[float, float] | None, now: float | None = None) -> bool:
        if tilt is None:
            return False
        now = time.monotonic() if now is None else now
        dt = max(0.0, min(0.2, now - self.t_prev)) if self.t_prev is not None else 0.0
        self.t_prev = now
        if self.base is None:
            self.base = [tilt[0], tilt[1]]
        dev = max(abs(tilt[0] - self.base[0]), abs(tilt[1] - self.base[1]))
        if dev > self.limit:
            self.since = self.since if self.since is not None else now
            if now - self.since > self.max_s:
                self.base = [tilt[0], tilt[1]]   # 너무 오래 '기울어짐' → 적분이 흐른 것으로 보고 기준을 다시 잡음
                self.since = None
                return False
            return True
        self.since = None
        k = min(1.0, dt / self.tau)
        self.base[0] += (tilt[0] - self.base[0]) * k
        self.base[1] += (tilt[1] - self.base[1]) * k
        return False


class GuidedSteer:
    """유도선 + 자이로를 함께 쓰는 조향(측정 주행용).

    테이프 위치로 '가야 할 방향(자이로 각)'을 정하고, 조향은 자이로로 그 방향을 맞춥니다.
      - 테이프가 가운데 근처(데드밴드 안): 유도선 방향(ref)을 그대로 유지 → 조향 거의 0.
        이때 ref를 지금 방향 쪽으로 천천히 맞춰 자이로가 조금씩 흐르는 것도 따라갑니다.
      - 테이프가 옆으로 벗어남: 벗어난 만큼 ref에서 최대 ``max_heading_deg``까지 테이프 쪽으로 방향을 틀어
        비스듬히 다가갑니다(바퀴를 계속 꺾는 게 아니라 '각도'를 정해 두므로 지나치게 돌지 않음).
      - 방지턱 위(차가 기울어짐) + 내려온 뒤 잠깐: 바닥 센서가 바닥에서 떠 값이 틀어지므로 테이프는 무시하고
        방지턱 직전 방향(ref)을 자이로로 유지합니다. 턱에 부딪혀 한쪽으로 틀어져도 바로 되돌립니다.
      - 테이프를 놓침: 마지막으로 본 쪽으로 최대 각도만큼 틀어 찾습니다(놓친 채 ``lost_s``가 지나면 멈춤).
    """

    SENSOR_AHEAD_M = 0.135   # 바닥 센서가 뒷축보다 앞에 있는 거리(제자리 회전 중심 기준)
    SENSOR_HALF_M = 0.02     # 가운데 센서 ~ 옆 센서 간격

    def __init__(self, settings: LineSettings, polarity: int = 1, kp: float = 1.4, max_heading_deg: float = 15.0,
                 recover_steer_deg: float = 22.0, tilt_block_deg: float = 1.5, settle_s: float = 0.25, lost_s: float = 1.5,
                 speed_mps: float = 0.4, ref_deg: float | None = None) -> None:
        self.s = settings
        self.sign = -1 if settings.steer_sign < 0 else 1
        self.polarity = -1 if polarity < 0 else 1
        self.kp, self.max_heading, self.recover_steer = kp, max_heading_deg, recover_steer_deg
        self.tilt_block, self.settle_s, self.lost_s = tilt_block_deg, settle_s, max(lost_s, settings.lost_timeout_s)
        self.gate = TiltGate(tilt_block_deg)
        self.ref: float | None = ref_deg   # 유도선 방향(자이로 각). 앞 구간에서 알아낸 값을 이어받을 수 있음
        self.centered_since: float | None = None
        self.last_dir = 0.0
        self.heading_seen: float | None = None
        self.speed = max(0.05, speed_mps)
        self.y_est: float | None = None   # 테이프가 센서 가운데에서 오른쪽(+)으로 얼마나 떨어져 있는지 추정(m)
        self.phi_prev: float | None = None
        self.lost_since: float | None = None
        self.bump_until = 0.0
        self.steer_prev = 0.0
        self.t_prev: float | None = None
        self.flipped = False
        self.mode = "line"
        self.bump_time = 0.0
        self.recover_time = 0.0
        self.max_offset_deg = 0.0
        self.last: dict[str, Any] = {}

    def update(self, raw: list[float] | None, heading: float, tilt: tuple[float, float] | None,
               now: float | None = None) -> tuple[float, bool]:
        now = time.monotonic() if now is None else now
        dt = max(0.0, min(0.2, now - self.t_prev)) if self.t_prev is not None else 0.02
        self.t_prev = now
        if self.ref is None:
            self.ref = heading
        w = weights(raw or [], self.s)
        pos = position(w, self.s)
        tilted = self.gate.update(tilt, now)
        # 테이프 옆 위치 추정(추측 항법): 차가 유도선보다 오른쪽(φ>0)을 보고 달리면 센서가 오른쪽으로 가므로 테이프는 상대적으로 왼쪽으로,
        # 차가 오른쪽으로 돌면 앞쪽 센서가 오른쪽으로 휘두르므로 역시 왼쪽으로 옮겨 갑니다.
        phi = -self.polarity * (heading - self.ref)          # 유도선 대비 오른쪽으로 튼 각(°)
        if self.y_est is not None:
            self.y_est -= self.speed * dt * math.sin(math.radians(phi))
            if self.phi_prev is not None:
                self.y_est -= self.SENSOR_AHEAD_M * math.radians(phi - self.phi_prev)
        self.phi_prev = phi
        if tilted:
            self.bump_until = now + self.settle_s
        ok = True
        theta = 0.0
        centered = False
        if now < self.bump_until:
            self.mode = "bump"            # 방지턱 위: 테이프 무시, 직전 방향 유지
            self.lost_since = None
            self.bump_time += dt
            # 턱에 부딪혀 틀어진 사이 옆으로 1.5 cm 넘게 밀렸다고 추정되면, 턱 위에서부터 조금씩(최대 60%) 그쪽으로 되돌아가기 시작
            if self.y_est is not None and abs(self.y_est) > 0.015:
                theta = max(-0.6, min(0.6, self.y_est / 0.03)) * self.max_heading
        elif pos is None:
            self.mode = "lost"
            self.lost_since = self.lost_since or now
            # 테이프가 있을 쪽(추정 위치)으로 비스듬히 다가감. 추정이 0 근처면 유도선 방향으로 곧게.
            est = self.y_est if self.y_est is not None else self.SENSOR_HALF_M * self.last_dir
            theta = max(-1.0, min(1.0, est / 0.03)) * self.max_heading
            ok = now - self.lost_since < self.lost_s
        else:
            self.lost_since = None
            d = self.sign * pos             # + = 테이프가 오른쪽(오른쪽으로 가야 함)
            self.last_dir = d
            self.heading_seen = heading
            # 센서 가장자리(|d| ≈ 1)면 실제로는 더 바깥일 수 있어 조금 더 크게 둠
            self.y_est = self.SENSOR_HALF_M * d * (1.4 if abs(d) > 0.9 else 1.0)
            mag = max(0.0, abs(d) - self.s.deadband) / max(1e-6, 1.0 - self.s.deadband)
            theta = min(1.0, 1.6 * mag) * self.max_heading * (1.0 if d >= 0 else -1.0)
            centered = mag <= 0
            self.mode = "line" if centered else "recover"
            self.centered_since = (self.centered_since or now) if centered else None
            if centered and (abs(heading - self.ref) < 3.0 or now - self.centered_since >= 0.4):
                # 테이프 가운데로 꾸준히 가는 중: 유도선 방향(ref)을 지금 방향으로 천천히(약 1초) 맞춤
                # (손으로 놓은 차가 조금 비뚤었거나, 회전 뒤 방향이 몇 도 틀렸거나, 자이로가 조금씩 흐르는 것을 따라감)
                self.ref += (heading - self.ref) * min(1.0, dt / 1.0)
        if self.mode in ("recover", "lost"):
            self.recover_time += dt
        # 오른쪽으로 가려면(theta > 0) 오른쪽 조향(+) → 그 조향이 줄이는 방향으로 목표 각을 둠
        desired = self.ref - self.polarity * theta
        err = heading - desired
        self.max_offset_deg = max(self.max_offset_deg, abs(heading - self.ref))
        gentle = min(self.s.gentle_max_deg, self.s.max_steer_deg)
        calm = centered and abs(err) < 6.0   # 테이프 가운데 + 방향도 거의 맞음 → 조향 최소
        lim = gentle if calm else min(self.recover_steer, self.s.max_steer_deg + 5)
        target = max(-lim, min(lim, self.kp * err * self.polarity))
        if calm and abs(target) < 1.0:
            target = 0.0
        # 되돌아오던 큰 조향은 빨리 풀어야 반대쪽으로 넘어가지 않음
        rate = self.s.rate_limit_dps if (calm and abs(self.steer_prev) <= gentle) else max(self.s.rate_limit_dps, 200.0)
        step = rate * dt
        steer = self.steer_prev + max(-step, min(step, target - self.steer_prev))
        self.steer_prev = steer
        self.last = {"raw": raw, "weights": w, "position": pos, "steer_deg": round(steer, 2), "lost": pos is None,
                     "mode": self.mode, "ref_deg": round(self.ref, 2), "heading_deg": round(heading, 2),
                     "y_est_mm": None if self.y_est is None else round(self.y_est * 1000, 1)}
        return steer, ok

    def summary(self) -> dict[str, Any]:
        return {"bump_hold_s": round(self.bump_time, 2), "recover_s": round(self.recover_time, 2),
                "max_offset_deg": round(self.max_offset_deg, 1), "ref_deg": None if self.ref is None else round(self.ref, 2)}


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
