"""바닥 테이프 코스 도구(반복 측정의 '정지선 모드'와 '제자리 회전 복귀'에서 씀).

바닥 구성
  - 유도선: 진행 방향으로 붙인 검은 테이프(가운데 센서만 검게 봄)
  - 출발선·정지선: 유도선과 직각으로 붙인 가로 테이프(센서 3개가 모두 검게 봄 → 유도선과 구분)

여기에는 가로 테이프 감시(CrossWatcher), 위치 맞춤(_align_on_cross), 테이프까지 주행(_drive_until_cross),
제자리 회전(_spin), 유도선 찾기(_find_guide)가 있고, 반복 측정(experiment._auto_run)이 이것들을 조합합니다.
방지턱을 지나며 차가 들리면 바닥 센서가 바닥에서 멀어져 어둡게(검은 테이프처럼) 읽힐 수 있어, 기울기가 클 때는
가로 테이프 판정을 하지 않습니다.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import experiment as ex
from . import impact_analysis as ia
from .line_follow import LineSettings, LineTracker, TiltGate, weights
from .mpu6050 import mpu6050_service


class CourseRequest(BaseModel):
    condition: ex.ExperimentCondition
    legs: int = Field(default=4, ge=1, le=40, description="측정 구간 수(왕복 2구간 = 1왕복)")
    accel: Literal["instant", "ramp"] = "ramp"
    accel_s: float = Field(default=0.6, ge=0.05, le=3, description="순차 가속 시간(초)")
    decel_s: float = Field(default=0.3, ge=0.05, le=3, description="정지선을 본 순간부터 0까지 줄이는 시간(초)")
    return_mode: Literal["reverse", "uturn", "none"] = "uturn"
    reverse_output: float | None = Field(default=None, ge=1, le=100, description="후진 복귀 출력(빈칸 = 측정 출력 × 1.3)")
    spin_output: float = Field(default=70, ge=10, le=100, description="제자리 회전 출력")
    spin_lead_deg: float = Field(default=12, ge=0, le=60, description="멈춘 뒤 미끄러지는 각을 감안해 목표보다 일찍 끔")
    spin_direction: Literal["left", "right"] = "left"
    creep_duty: float = Field(default=40, ge=15, le=100, description="선 위치 맞춤 때 실제 모터 듀티(%) – 차가 겨우 움직이는 정도가 좋음")
    record: bool = True
    leg_timeout_s: float = Field(default=8, ge=1, le=30)
    min_leg_s: float = Field(default=0.4, ge=0, le=5, description="출발 후 이 시간 동안은 가로 테이프를 무시")
    tilt_block_deg: float = Field(default=2.0, ge=0.5, le=15, description="차가 이만큼 기울면(방지턱 위) 가로 테이프 판정 안 함")
    cross_threshold: float = Field(default=0.5, ge=0.2, le=0.95, description="세 센서 모두 이 값(0~1) 이상이면 가로 테이프")
    rest_s: float = Field(default=0.8, ge=0, le=10)
    pre_roll_s: float = Field(default=0.5, ge=0.2, le=3)
    tail_s: float = Field(default=0.3, ge=0.1, le=2)
    steering: Literal["line", "gyro", "none"] = "line"


class Stopped(ex.Aborted):
    """사용자 정지(반복 측정의 Aborted로 처리)."""


async def _halt(ctl, reason: str) -> None:
    """코스 안에서 멈춤(다음 동작을 이어 가도록 세대 번호를 갱신). 사용자가 누른 정지는 그 뒤 세대가 바뀌어 감지됩니다."""
    await ctl.stop(reason=reason)
    runner.gen = ctl.stop_generation


class CourseRunner:
    def __init__(self) -> None:
        self.state = "idle"
        self.message = ""
        self.leg = 0
        self.legs = 0
        self.phase = ""
        self.task: asyncio.Task | None = None
        self.log: list[dict[str, Any]] = []
        self.completed: list[str] = []
        self.started: float | None = None
        self.gen = 0
        self.target_deg = 0.0

    @property
    def busy(self) -> bool:
        return self.state == "running"

    def snapshot(self) -> dict[str, Any]:
        return {"state": self.state, "message": self.message, "leg": self.leg, "legs": self.legs, "phase": self.phase,
                "completed_run_ids": list(self.completed), "log": self.log[-60:],
                "elapsed_s": round(time.monotonic() - self.started, 1) if self.started and self.busy else None}

    def note(self, text: str) -> None:
        self.message = text
        self.log.append({"t": round(time.monotonic() - (self.started or time.monotonic()), 2), "leg": self.leg, "msg": text})


runner = CourseRunner()


def course_err() -> float | None:
    """코스 기준 방향(출발할 때 0°, 제자리 회전마다 ±180°)에서 지금 얼마나 틀어졌는지(°)."""
    h = mpu6050_service.heading_deg()
    return None if h is None else h - runner.target_deg


def effective_duty(mode: str, value: float) -> float:
    """실제 모터 듀티(%): 라이브러리 PWM은 PWM/2 + 50, 직접 듀티는 그대로."""
    if value <= 0:
        return 0.0
    return min(100.0, value / 2 + 50) if mode == "pwm" else min(100.0, value)


def is_cross(raw: Any, line: LineSettings, thr: float) -> bool:
    w = weights(list(raw or []), line)
    return bool(w) and min(w) >= thr


def tilted(limit: float) -> bool:
    t = mpu6050_service.tilt_deg()
    return t is not None and max(abs(t[0]), abs(t[1])) > limit


class CrossWatcher:
    """바닥 센서를 수 ms 간격으로 따로 읽어 최신값을 두고, 가로 테이프를 판정합니다.

    arm(): 지금 가로 테이프 위에 있으면 일단 벗어난 뒤(연속 3번 안 보임)부터 판정합니다.
    """

    def __init__(self, ctl, line: LineSettings, thr: float, tilt_limit: float, skip: int = 0) -> None:
        self.ctl, self.line, self.thr, self.tilt_limit = ctl, line, thr, tilt_limit
        self.skip = skip  # 이만큼의 가로 테이프는 지나쳐 감(후진 복귀 때 방금 지나친 정지선)
        self.raw: Any = None
        self.armed = False
        self.cleared = 0
        self.hits = 0
        self.seen = asyncio.Event()
        self.ready_after = 0.0
        self.task: asyncio.Task | None = None
        self.blocked = 0
        self.gate = TiltGate(tilt_limit)
        self.prev_partial = False

    def start(self, ready_after: float) -> None:
        self.ready_after = ready_after
        self.task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        while not self.seen.is_set():
            raw = await asyncio.to_thread(self.ctl.adapter.read_grayscale)
            self.raw = raw
            c = is_cross(raw, self.line, self.thr)
            bumpy = self.gate.update(mpu6050_service.tilt_deg())  # 방지턱 위(기울기 흐름은 빼고 판단)
            if not self.armed:
                # 출발할 때 센서가 가로 테이프 위에 있으므로, 테이프에서 확실히 벗어난 뒤부터 봅니다:
                # 세 센서 중 하나라도 바닥 쪽(테이프 정도 < 기준의 절반)인 상태가 25번(약 50~100 ms) 연속이어야 함.
                w = weights(list(raw or []), self.line)
                off = bool(w) and min(w) < 0.5 * self.thr
                self.cleared = self.cleared + 1 if off else 0
                if self.cleared >= 25 and time.monotonic() >= self.ready_after:
                    self.armed = True
            else:
                # 가로 테이프: 세 센서가 모두 기준 이상인 읽기 2번 연속, 또는 1번 + 바로 앞/뒤 읽기가 절반 이상(빨리 지나가거나
                # 읽기가 잠깐 밀려 테이프 위 읽기가 한 번뿐인 경우)
                w = weights(list(raw or []), self.line)
                m = min(w) if w else 0.0
                partial = m >= 0.5 * self.thr
                if c and bumpy:
                    self.blocked += 1
                    self.hits = 0
                elif c or (partial and self.hits >= 1):
                    self.hits += 1 if c else 0
                    if self.hits >= 2 or (self.hits >= 1 and (partial and not c or self.prev_partial)):
                        if self.skip > 0:
                            self.skip -= 1
                            self.armed, self.cleared, self.hits = False, 0, 0
                        else:
                            self.seen.set()
                            return
                else:
                    self.hits = 0
                self.prev_partial = partial and not bumpy
            await asyncio.sleep(0.002)

    def stop(self) -> None:
        if self.task:
            self.seen.set()
            self.task.cancel()


async def _drive_until_cross(ctl, gen: int, req: CourseRequest, line: LineSettings, direction: str, output: float,
                             ramp_s: float, decel_s: float, timeout_s: float, min_s: float, steering: str,
                             polarity: int = 1, skip: int = 0) -> dict[str, Any]:
    """출력(순차 가속)으로 달리다 가로 테이프를 보면 그 순간부터 decel_s 동안 0까지 줄이고 멈춥니다."""
    mode = req.condition.output_mode
    eff = effective_duty(mode, output)
    tracker = LineTracker(line) if steering == "line" and direction == "forward" else None
    hold = ex.HeadingController(1.2, 0.8, 0.0, polarity=polarity) if (steering in ("gyro", "line") and tracker is None) else None
    have_heading = mpu6050_service.heading_deg() is not None
    watch = CrossWatcher(ctl, line, req.cross_threshold, req.tilt_block_deg, skip)
    t0 = time.monotonic()
    watch.start(t0 + min_s)
    sign = 1.0 if direction == "forward" else -1.0
    prev = t0
    detected_at: float | None = None
    steer = 0.0
    lost = False
    try:
        while True:
            if ctl.stop_generation != runner.gen:
                raise Stopped()
            now = time.monotonic()
            el = now - t0
            if detected_at is None and watch.seen.is_set():
                detected_at = now
            # 가속·감속 구간은 '실제 듀티'로 비례해서 바꿉니다. 라이브러리 PWM은 듀티 = PWM/2 + 50(%)이라
            # PWM 숫자를 줄여도 듀티가 50% 아래로 안 내려가 감속이 거의 안 되기 때문입니다.
            if detected_at is not None:
                k = 1.0 - (now - detected_at) / max(0.01, decel_s)
                if k <= 0:
                    break
            else:
                if el > timeout_s:
                    break
                k = min(1.0, el / ramp_s) if ramp_s > 0 else 1.0
            if tracker is not None and watch.raw is not None:
                steer, ok = tracker.update(watch.raw, direction)
                if not ok and detected_at is None:
                    lost = True
                    break
            elif hold is not None and have_heading:
                h = course_err()
                if h is not None:
                    steer = hold.update(h, now - prev, sign)
            prev = now
            if k >= 0.999:
                cmd_mode, cmd_out = mode, output
            else:
                cmd_mode, cmd_out = "duty", max(0.0, eff * k)
            if not await ctl.experiment_drive(cmd_mode, cmd_out, runner.gen, direction, steer):
                raise Stopped()
            await asyncio.sleep(0.02)
    finally:
        watch.stop()
        await _halt(ctl, "course_leg_end")
    return {"detected": detected_at is not None, "line_lost": lost, "elapsed_s": round(time.monotonic() - t0, 3),
            "detect_s": round(detected_at - t0, 3) if detected_at else None, "tilt_blocked": watch.blocked,
            "line_flipped": bool(tracker and tracker.flipped), "line_sign": tracker.sign if tracker else None}


async def _creep_to_cross(ctl, gen: int, req: CourseRequest, line: LineSettings, direction: str, timeout_s: float = 3.0,
                          duty: float | None = None) -> bool:
    """천천히 움직여 가로 테이프가 센서 바로 밑에 오면 멈춥니다(이미 위에 있으면 그대로)."""
    raw = await asyncio.to_thread(ctl.adapter.read_grayscale)
    if is_cross(raw, line, req.cross_threshold):
        return True
    t0 = time.monotonic()
    hits = 0
    peak = 0.0
    hold = ex.HeadingController(1.2, 0.0, 0.0, polarity=ex.store.load_settings().heading_sign) if mpu6050_service.heading_deg() is not None else None
    prev = t0
    try:
        while time.monotonic() - t0 < timeout_s:
            if ctl.stop_generation != runner.gen:
                raise Stopped()
            now = time.monotonic()
            h = course_err() if hold is not None else None
            steer = hold.update(h, now - prev, 1.0 if direction == "forward" else -1.0) if (hold is not None and h is not None) else 0.0
            prev = now
            if not await ctl.experiment_drive("duty", duty or req.creep_duty, runner.gen, direction, steer):
                raise Stopped()
            for _ in range(5):
                raw = await asyncio.to_thread(ctl.adapter.read_grayscale)
                hits = hits + 1 if is_cross(raw, line, req.cross_threshold) else 0
                if hits >= 2:
                    return True
                w = weights(list(raw or []), line)
                m = min(w) if w else 0.0
                peak = max(peak, m)
                if peak >= 0.6 * req.cross_threshold and m < 0.15:
                    # 테이프 위를 지나쳐 버림(읽기가 잠깐 밀린 사이): 바로 멈추고, 다음 시도에서 반대로 더 천천히 맞춤
                    return False
                await asyncio.sleep(0.003)
    finally:
        await _halt(ctl, "course_creep_end")
    return False


async def _on_cross(ctl, req: CourseRequest, line: LineSettings, reads: int = 5) -> bool:
    hits = 0
    for _ in range(reads):
        raw = await asyncio.to_thread(ctl.adapter.read_grayscale)
        hits += 1 if is_cross(raw, line, req.cross_threshold) else 0
        await asyncio.sleep(0.004)
    return hits >= (reads + 1) // 2


async def _align_on_cross(ctl, req: CourseRequest, line: LineSettings, first: str, timeout_s: float = 2.0) -> bool:
    """가로 테이프가 센서 바로 밑에 오도록 맞춥니다. 멈출 때 미끄러져 지나치면 반대로 더 천천히 되돌아옵니다(최대 3번)."""
    other = "forward" if first == "backward" else "backward"
    plan = [(first, req.creep_duty, timeout_s), (other, req.creep_duty * 0.9, timeout_s * 0.6), (first, req.creep_duty * 0.85, timeout_s * 0.6),
            (other, req.creep_duty, timeout_s * 1.5)]
    for direction, duty, tmo in plan:
        await asyncio.sleep(0.2)  # 멈춘 뒤 흔들림이 가라앉을 때까지
        if await _on_cross(ctl, req, line):
            return True
        await _creep_to_cross(ctl, 0, req, line, direction, tmo, duty)
    await asyncio.sleep(0.2)
    return await _on_cross(ctl, req, line)


def _wrap(d: float) -> float:
    return (d + 180.0) % 360.0 - 180.0


async def _spin(ctl, gen: int, req: CourseRequest, degrees: float, direction: str, power: float | None = None,
                timeout_s: float = 7.0) -> float | None:
    """자이로로 각도를 재며 제자리 회전하고, 코스 기준 방향을 ±180° 바꿉니다. 끝나면 남은 오차를 천천히 맞춥니다."""
    h0 = mpu6050_service.heading_deg()
    if h0 is None:
        raise RuntimeError("제자리 회전은 MPU-6050 자이로가 필요합니다(센서 화면에서 정지 보정 후 사용).")
    target = max(1.0, degrees - req.spin_lead_deg * degrees / 180.0)
    t0 = time.monotonic()
    turned = 0.0
    try:
        while time.monotonic() - t0 < timeout_s:
            if ctl.stop_generation != runner.gen:
                raise Stopped()
            turned = abs((mpu6050_service.heading_deg() or h0) - h0)
            if turned >= target:
                break
            if not await ctl.experiment_spin(direction, power or req.spin_output, runner.gen):
                raise Stopped()
            await asyncio.sleep(0.02)
    finally:
        await _halt(ctl, "course_spin_end")
    await asyncio.sleep(0.2)
    turned = abs((mpu6050_service.heading_deg() or h0) - h0)
    runner.target_deg += degrees if direction == "left" else -degrees  # 왼쪽(반시계) = +
    # 남은 방향 오차(관성으로 더 돌았거나 덜 돈 것)를 천천히 맞춤
    for _ in range(3):
        err = _wrap(course_err() or 0.0)
        if abs(err) <= 6.0:
            break
        fix_dir = "right" if err > 0 else "left"
        t1 = time.monotonic()
        try:
            while time.monotonic() - t1 < 1.5:
                if ctl.stop_generation != runner.gen:
                    raise Stopped()
                e = _wrap(course_err() or 0.0)
                if abs(e) <= 2.0 or (e > 0) != (err > 0):
                    break
                if not await ctl.experiment_spin(fix_dir, max(req.spin_output * 0.7, 55), runner.gen):
                    raise Stopped()
                await asyncio.sleep(0.01)
        finally:
            await _halt(ctl, "course_spin_fix")
        await asyncio.sleep(0.15)
    return turned


async def _find_guide(ctl, gen: int, req: CourseRequest, line: LineSettings) -> bool:
    """제자리 회전 뒤 바닥 센서가 유도선을 보는지 확인하고, 안 보이면 좌우로 조금만(±12°) 돌려 찾습니다.

    크게 돌리면 차가 비스듬해져 뒤로 가로 테이프를 찾을 때 빗나가므로, 못 찾으면 유도선 방향(목표 각)으로 되돌려 둡니다.
    가로 테이프는 유도선보다 훨씬 길어서 옆으로 1~3 cm 어긋나도 찾을 수 있고, 다음 구간에서 유도선으로 돌아갑니다.
    """
    def seen(raw: Any) -> bool:
        w = weights(list(raw or []), line)
        return bool(w) and max(w) >= 0.5
    raw = await asyncio.to_thread(ctl.adapter.read_grayscale)
    if seen(raw):
        return True
    found = False
    for direction, limit in (("left", 12.0), ("right", 24.0)):
        h0 = mpu6050_service.heading_deg()
        if h0 is None:
            return False
        t0 = time.monotonic()
        try:
            while time.monotonic() - t0 < 1.5:
                if ctl.stop_generation != runner.gen:
                    raise Stopped()
                raw = await asyncio.to_thread(ctl.adapter.read_grayscale)
                if seen(raw):
                    found = True
                    break
                if abs((mpu6050_service.heading_deg() or h0) - h0) >= limit:
                    break
                if not await ctl.experiment_spin(direction, max(55.0, req.spin_output * 0.7), runner.gen):
                    raise Stopped()
                await asyncio.sleep(0.01)
        finally:
            await _halt(ctl, "course_find_line")
        await asyncio.sleep(0.12)
        if found:
            return True
    await _face_target(ctl, req)
    return False


async def _face_target(ctl, req: CourseRequest, tol: float = 4.0) -> None:
    """코스 목표 방향(runner.target_deg)으로 제자리에서 조금씩 돌려 맞춤."""
    for _ in range(3):
        err = _wrap(course_err() or 0.0)
        if abs(err) <= tol:
            return
        fix_dir = "right" if err > 0 else "left"
        t1 = time.monotonic()
        try:
            while time.monotonic() - t1 < 1.5:
                if ctl.stop_generation != runner.gen:
                    raise Stopped()
                e = _wrap(course_err() or 0.0)
                if abs(e) <= tol / 2 or (e > 0) != (err > 0):
                    break
                if not await ctl.experiment_spin(fix_dir, max(req.spin_output * 0.7, 55), runner.gen):
                    raise Stopped()
                await asyncio.sleep(0.01)
        finally:
            await _halt(ctl, "course_face")
        await asyncio.sleep(0.15)


router = APIRouter(prefix="/api/experiment/course", tags=["course"])


@router.get("/sensors")
async def course_sensors():
    """지금 바닥 센서가 유도선/가로 테이프 중 무엇을 보는지(설치 확인용)."""
    ctl = ex.control()
    settings = ex.store.load_settings()
    raw = await asyncio.to_thread(ctl.adapter.read_grayscale)
    w = weights(list(raw or []), settings.line)
    return {"raw": raw, "weights": w, "cross": is_cross(raw, settings.line, 0.5) if w else None,
            "guide": bool(w and w[1] >= 0.5 and not is_cross(raw, settings.line, 0.5)), "calibrated": bool(w)}
