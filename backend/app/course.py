"""코스 반복 측정: 출발선 → (방지턱) → 정지선을 바닥 테이프로 인식해 왕복을 자동으로 되풀이합니다.

바닥 구성
  - 유도선: 진행 방향으로 붙인 검은 테이프(가운데 센서만 검게 봄)
  - 출발선·정지선: 유도선과 직각으로 붙인 가로 테이프(센서 3개가 모두 검게 봄 → 유도선과 구분)

한 구간(leg)
  1) (측정 시) 정지 기록 → 출발: 즉시 출력 또는 정해진 시간 동안 순차 가속
  2) 유도선을 '조향 최소' 방식으로 따라감(가운데 근처면 바퀴를 똑바로)
  3) 출발선을 벗어난 뒤 가로 테이프를 보면(2번 연속, 차가 기울지 않았을 때) 그 순간부터 순차 감속 → 정지
  4) 기록을 저장(1회차)
복귀 방식
  - reverse: 정지 후 더 센 출력으로 후진해 출발선을 다시 볼 때까지(방지턱을 후진으로 넘음)
  - uturn  : 정지 후 제자리 180° 회전 → 정지선이 센서 밑에 올 때까지 살짝 후진 → 다음 구간은 반대 방향으로 측정
  - none   : 한 구간만
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
from .line_follow import LineSettings, LineTracker, weights
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


class Stopped(Exception):
    pass


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
ex.extra_busy.append(lambda: runner.busy)


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

    def start(self, ready_after: float) -> None:
        self.ready_after = ready_after
        self.task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        while not self.seen.is_set():
            raw = await asyncio.to_thread(self.ctl.adapter.read_grayscale)
            self.raw = raw
            c = is_cross(raw, self.line, self.thr)
            if not self.armed:
                # 출발할 때 센서가 가로 테이프 위에 있으므로, 테이프에서 확실히 벗어난 뒤부터 봅니다:
                # 세 센서 중 하나라도 바닥 쪽(테이프 정도 < 기준의 절반)인 상태가 25번(약 50~100 ms) 연속이어야 함.
                w = weights(list(raw or []), self.line)
                off = bool(w) and min(w) < 0.5 * self.thr
                self.cleared = self.cleared + 1 if off else 0
                if self.cleared >= 25 and time.monotonic() >= self.ready_after:
                    self.armed = True
            elif c:
                if tilted(self.tilt_limit):
                    self.blocked += 1
                    self.hits = 0
                else:
                    self.hits += 1
                    if self.hits >= 2 and self.skip > 0:
                        self.skip -= 1
                        self.armed, self.cleared, self.hits = False, 0, 0
                    elif self.hits >= 2:
                        self.seen.set()
                        return
            else:
                self.hits = 0
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
    await asyncio.sleep(0.3)
    turned = abs((mpu6050_service.heading_deg() or h0) - h0)
    runner.target_deg += degrees if direction == "left" else -degrees  # 왼쪽(반시계) = +
    # 남은 방향 오차(관성으로 더 돌았거나 덜 돈 것)를 천천히 맞춤
    for _ in range(3):
        err = _wrap(course_err() or 0.0)
        if abs(err) <= 4.0:
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
                if not await ctl.experiment_spin(fix_dir, max(req.spin_output * 0.6, 30), runner.gen):
                    raise Stopped()
                await asyncio.sleep(0.01)
        finally:
            await _halt(ctl, "course_spin_fix")
        await asyncio.sleep(0.25)
    return turned


async def _find_guide(ctl, gen: int, req: CourseRequest, line: LineSettings) -> bool:
    """제자리 회전 뒤 가운데 센서가 유도선을 보도록 좌우로 조금씩 돌려 찾습니다(최대 ±25°)."""
    def center(raw: Any) -> float:
        w = weights(list(raw or []), line)
        return w[1] if w else 0.0
    raw = await asyncio.to_thread(ctl.adapter.read_grayscale)
    if center(raw) >= 0.5:
        return True
    for direction, limit in (("left", 20.0), ("right", 40.0), ("left", 40.0)):
        h0 = mpu6050_service.heading_deg()
        if h0 is None:
            return False
        t0 = time.monotonic()
        try:
            while time.monotonic() - t0 < 2.5:
                if ctl.stop_generation != runner.gen:
                    raise Stopped()
                raw = await asyncio.to_thread(ctl.adapter.read_grayscale)
                if center(raw) >= 0.5:
                    return True
                if abs((mpu6050_service.heading_deg() or h0) - h0) >= limit:
                    break
                if not await ctl.experiment_spin(direction, req.spin_output * 0.75, runner.gen):
                    raise Stopped()
                await asyncio.sleep(0.01)
        finally:
            await _halt(ctl, "course_find_line")
        await asyncio.sleep(0.15)
    return False


async def _record_leg(ctl, gen: int, req: CourseRequest, settings: ex.ExperimentSettings, line: LineSettings,
                      trial: int, leg_dir: str) -> dict[str, Any]:
    cond = req.condition.model_copy(update={"trial": trial, "direction": "forward",
                                            "note": ((req.condition.note + " · ") if req.condition.note else "") + f"코스 {leg_dir}"})
    run = ex.new_run(cond, settings, simulated=False, source="course", pre_roll_s=req.pre_roll_s, run_seconds=req.leg_timeout_s)
    run["course"] = {"leg_direction": leg_dir, "accel": req.accel, "accel_s": req.accel_s, "decel_s": req.decel_s}
    if req.record:
        from .camera import camera_service
        camera_service.paused = True
        st = await asyncio.to_thread(mpu6050_service.start_recording)
        if st.get("error") or not st.get("recording"):
            raise RuntimeError(st.get("error") or "MPU-6050 기록을 시작하지 못했습니다.")
    try:
        runner.phase = "pre_roll"
        await asyncio.sleep(req.pre_roll_s)
        if ctl.stop_generation != runner.gen:
            raise Stopped()
        runner.phase = "driving"
        out = req.condition.output_value
        info = await _drive_until_cross(ctl, 0, req, line, "forward", out, req.accel_s if req.accel == "ramp" else 0.0,
                                        req.decel_s, req.leg_timeout_s, req.min_leg_s, req.steering,
                                        polarity=ex.store.load_settings().heading_sign)
        run["course"].update(info)
        runner.phase = "tail"
        await asyncio.sleep(req.tail_s)
    finally:
        if req.record:
            from .camera import camera_service
            await asyncio.to_thread(mpu6050_service.stop_recording)
            camera_service.paused = False
    if not req.record:
        return {"run_id": None, **info}
    meta = await asyncio.to_thread(mpu6050_service.recording_columns)
    columns = meta.pop("columns")
    run["data_source"] = "mpu6050"
    run["steering_source"] = req.steering
    run = await asyncio.to_thread(ex.finalize_run, run, columns, meta, settings)
    if not info["detected"]:
        run["analysis"].setdefault("flags", []).append("정지선을 찾지 못하고 최대 시간에서 멈췄습니다.")
        ex.store.put(run)
    runner.completed.append(run["id"])
    return {"run_id": run["id"], **info}


async def _course(req: CourseRequest) -> None:
    ctl = ex.control()
    if ctl.adapter.mode != "hardware":
        runner.state, runner.message = "error", "코스 주행은 실제 차량(하드웨어 모드)에서만 됩니다."
        return
    settings = ex.store.load_settings()
    line = settings.line
    if not line.floor or not line.line:
        runner.state, runner.message = "error", "라인 추종 탭에서 바닥·테이프 센서 보정을 먼저 하세요."
        return
    runner.gen = ctl.stop_generation
    trial = req.condition.trial
    rev_out = req.reverse_output or min(100.0, req.condition.output_value * 1.3)
    try:
        # 자이로 영점은 출발선에 서 있을 때 한 번만 잡고, 이후에는 방향만 0으로 돌립니다(멈춘 직전 흔들림이 영점에 섞이지 않게).
        mpu6050_service.heading_stop()
        await asyncio.sleep(0.6)
        runner.target_deg = 0.0
        if not mpu6050_service.heading_reset():
            runner.note("자이로를 쓸 수 없습니다(센서 보정 필요) – 회전·직진 보정 없이 진행")
        raw = await asyncio.to_thread(ctl.adapter.read_grayscale)
        if not is_cross(raw, line, req.cross_threshold):
            runner.note("출발선이 센서 밑에 없습니다. 천천히 후진해 출발선을 찾습니다.")
            if not await _align_on_cross(ctl, req, line, "backward", 2.5):
                raise RuntimeError("출발선을 찾지 못했습니다. 차의 바닥 센서가 출발선(가로 테이프) 위에 오도록 놓으세요.")
        for i in range(req.legs):
            runner.leg = i + 1
            leg_dir = "A→B" if (i % 2 == 0 or req.return_mode != "uturn") else "B→A"
            runner.note(f"{i + 1}/{req.legs}구간 측정 주행 ({leg_dir})")
            if not await _on_cross(ctl, req, line):
                runner.note("가로 테이프가 센서 밑에 없어 위치를 다시 맞춥니다.")
                if not await _align_on_cross(ctl, req, line, "backward", 1.5):
                    raise RuntimeError("출발 위치(가로 테이프 위)에 차를 맞추지 못했습니다.")
            res = await _record_leg(ctl, 0, req, settings, line, trial + i, leg_dir)
            if not res["detected"]:
                runner.note(f"{i + 1}구간: 유도선을 놓쳐 멈췄습니다. 코스를 멈춥니다." if res.get("line_lost") else
                            f"{i + 1}구간: 정지선을 못 보고 {req.leg_timeout_s:g}초에서 멈췄습니다. 코스를 멈춥니다.")
                runner.state = "error"
                return
            runner.note(f"{i + 1}구간 완료 · 정지선 {res['detect_s']:.2f}초에 인식, 순차 감속 후 정지")
            if res.get("line_flipped"):
                st = ex.store.load_settings(); st.line.steer_sign = res["line_sign"]; ex.store.save_settings(st)
                line = st.line
            last = i == req.legs - 1
            if req.return_mode == "none" or (last and req.return_mode == "uturn"):
                break
            await asyncio.sleep(0.3)
            if req.return_mode == "reverse":
                runner.phase = "returning"
                # 순차 감속으로 정지선을 조금 지나쳐 멈췄다면, 후진하면서 그 정지선을 한 번 더 지나므로 하나는 건너뜁니다.
                skip = 0 if await _on_cross(ctl, req, line) else 1
                runner.note(f"출발선까지 후진 (출력 {rev_out:g}{', 방금 지난 정지선은 건너뜀' if skip else ''})")
                back = await _drive_until_cross(ctl, 0, req, line, "backward", rev_out, 0.3, max(0.1, req.decel_s * 0.6),
                                                req.leg_timeout_s * 1.5, req.min_leg_s, "gyro" if req.steering != "none" else "none",
                                                polarity=ex.store.load_settings().heading_sign, skip=skip)
                if not back["detected"]:
                    raise RuntimeError("후진 중 출발선을 찾지 못했습니다.")
                runner.note("출발선 인식 → 위치 맞춤")
                if not await _align_on_cross(ctl, req, line, "forward", 2.0):
                    raise RuntimeError("출발선 위에 차를 맞추지 못했습니다.")
            else:
                runner.phase = "turning"
                runner.note("제자리 180° 회전")
                turned = await _spin(ctl, 0, req, 180.0, req.spin_direction)
                if turned is not None:
                    # 멈춘 뒤 더 돈 만큼(관성)을 배워 다음 회전부터 그만큼 일찍 끕니다.
                    req.spin_lead_deg = max(0.0, min(60.0, req.spin_lead_deg + 0.6 * (turned - 180.0)))
                    run_lead = req.spin_lead_deg
                    st = ex.store.load_settings()
                    st.course_spin_lead_deg = round(run_lead, 1)
                    ex.store.save_settings(st)
                runner.note(f"회전 {turned:.0f}° (다음 회전은 {req.spin_lead_deg:.0f}° 일찍 끔) → 유도선 찾기")
                if not await _find_guide(ctl, 0, req, line):
                    runner.note("유도선을 바로 못 찾았습니다(다음 구간에서 따라가며 맞춤).")
                # 회전 뒤 테이프 위치는 '멈출 때 지나친 거리'에 따라 센서 뒤쪽일 수도(대부분) 앞쪽일 수도 있어 양쪽을 찾습니다.
                runner.note("가로 테이프가 센서 밑에 올 때까지 후진")
                found = await _align_on_cross(ctl, req, line, "backward", 2.0)
                if not found:
                    raise RuntimeError("회전 뒤 가로 테이프를 다시 찾지 못했습니다(회전하며 많이 밀렸거나 유도선에서 벗어남).")
            if req.rest_s:
                runner.phase = "rest"
                await asyncio.sleep(req.rest_s)
        runner.state = "done"
        runner.note(f"코스 반복 완료 · {len(runner.completed)}회 저장")
    except Stopped:
        runner.state = "stopped"
        runner.note("정지 명령으로 멈췄습니다.")
    except Exception as exc:
        runner.state = "error"
        runner.note(f"코스 주행 오류: {exc}")
        try:
            await ctl.stop(reason="course_error")
        except Exception:
            pass
    finally:
        runner.phase = ""
        mpu6050_service.heading_stop()


router = APIRouter(prefix="/api/experiment/course", tags=["course"])


@router.post("/start")
async def course_start(req: CourseRequest):
    from .line_follow import line_runner
    if runner.busy or ex.auto_runner.busy or line_runner.busy:
        raise HTTPException(409, "이미 다른 주행이 진행 중입니다.")
    runner.state, runner.leg, runner.legs, runner.log, runner.completed = "running", 0, req.legs, [], []
    runner.started = time.monotonic()
    runner.note("시작")
    runner.task = asyncio.create_task(_course(req))
    return runner.snapshot()


@router.get("/status")
async def course_status():
    return runner.snapshot()


@router.post("/stop")
async def course_stop():
    # 세대 번호를 갱신하지 않고 멈춰야 진행 중인 코스가 '사용자 정지'로 알아채고 끝납니다.
    ctl = ex.control()
    await ctl.stop(reason="course_stop")
    return runner.snapshot()


@router.get("/sensors")
async def course_sensors():
    """지금 바닥 센서가 유도선/가로 테이프 중 무엇을 보는지(설치 확인용)."""
    ctl = ex.control()
    settings = ex.store.load_settings()
    raw = await asyncio.to_thread(ctl.adapter.read_grayscale)
    w = weights(list(raw or []), settings.line)
    return {"raw": raw, "weights": w, "cross": is_cross(raw, settings.line, 0.5) if w else None,
            "guide": bool(w and w[1] >= 0.5 and not is_cross(raw, settings.line, 0.5)), "calibrated": bool(w)}
