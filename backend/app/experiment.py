"""방지턱 충격 실험 API: 설정, 자동 측정 주행, 저장, 분석, 내보내기.

실험 기록은 라즈베리파이 디스크(backend/data/experiments/)에 저장합니다.
브라우저 localStorage(약 5 MB)로는 1 kHz 원자료 75회분을 담을 수 없기 때문입니다.

- 시행마다 ``<id>.json``(조건·분석 요약)과 ``<id>.csv``(원자료)를 남깁니다.
- 서버가 모의 모드이면 합성 데이터를 만들고 ``simulated: true``로 표시합니다.
  집계·회귀는 서버 모드와 같은 종류의 데이터만 사용하므로 연구 데이터와 섞이지 않습니다.
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from . import impact_analysis as ia
from .mpu6050 import mpu6050_service
from .services.event_log import record_event

DATA_DIR = Path(os.getenv("PICARX_DATA_DIR") or Path(__file__).resolve().parent.parent / "data")
EXPERIMENT_MAX_OUTPUT = max(1, min(100, int(os.getenv("PICARX_EXPERIMENT_MAX_OUTPUT", "100"))))
RUN_COLUMNS = ("t_s", "ax_g", "ay_g", "az_g", "gx_dps", "gy_dps", "gz_dps", "vertical_g", "dynamic_g")
ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{6,80}$")


# --------------------------------------------------------------------------- 모델
class BumpProfile(BaseModel):
    id: str = Field(min_length=1, max_length=12, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(default="", max_length=40)
    height_mm: float = Field(gt=0, le=100)
    width_mm: float = Field(gt=0, le=500)


class ExperimentSettings(BaseModel):
    wheelbase_mm: float | None = Field(default=None, gt=10, le=500, description="앞·뒤 바퀴 중심 사이 거리(실측)")
    wheel_radius_mm: float | None = Field(default=None, gt=5, le=100)
    base_mass_g: float | None = Field(default=None, gt=0, le=10000)
    threshold_delta_g: float = Field(default=0.3, ge=0.02, le=6)
    min_gap_s: float = Field(default=0.03, ge=0.005, le=1)
    jump_g: float = Field(default=0.2, ge=0, le=0.9)
    a_limit_g: float | None = Field(default=None, gt=1, le=8)
    pre_roll_s: float = Field(default=1.0, ge=0.3, le=5)
    run_seconds: float = Field(default=2.5, ge=0.3, le=6)
    tail_s: float = Field(default=0.6, ge=0.1, le=3)
    bumps: list[BumpProfile] = Field(default_factory=lambda: [
        BumpProfile(id="A", name="A형 (완만)", height_mm=10, width_mm=60),
        BumpProfile(id="B", name="B형 (급함)", height_mm=10, width_mm=30),
    ])


class ExperimentCondition(BaseModel):
    trial: int = Field(default=1, ge=1, le=999)
    bump_id: str = Field(default="A", max_length=12)
    base_mass_g: float = Field(gt=0, le=10000, description="저울로 잰 차체+센서 질량")
    added_mass_g: float = Field(default=0, ge=0, le=5000)
    output_mode: Literal["pwm", "duty"] = "pwm"
    output_value: float = Field(ge=0, le=100)
    video_speed_mps: float | None = Field(default=None, ge=0, le=10, description="영상으로 잰 속도(교차 검증용)")
    note: str = Field(default="", max_length=200)


class AutoRunRequest(BaseModel):
    condition: ExperimentCondition
    run_seconds: float | None = Field(default=None, ge=0.3, le=6)
    pre_roll_s: float | None = Field(default=None, ge=0.3, le=5)
    tail_s: float | None = Field(default=None, ge=0.1, le=3)


class SaveRecordingRequest(BaseModel):
    condition: ExperimentCondition


class RunUpdate(BaseModel):
    excluded: bool | None = None
    note: str | None = Field(default=None, max_length=200)


# --------------------------------------------------------------------------- 저장소
class ExperimentStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    @property
    def runs_dir(self) -> Path:
        return self.root / "experiments"

    @property
    def settings_path(self) -> Path:
        return self.root / "experiment_settings.json"

    def load_settings(self) -> ExperimentSettings:
        try:
            return ExperimentSettings.model_validate_json(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ExperimentSettings()

    def save_settings(self, settings: ExperimentSettings) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.settings_path.with_suffix(".tmp")
        tmp.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(self.settings_path)

    def _json_path(self, run_id: str) -> Path:
        if not ID_PATTERN.match(run_id):
            raise HTTPException(404, "실험 기록을 찾을 수 없습니다.")
        return self.runs_dir / f"{run_id}.json"

    def csv_path(self, run_id: str) -> Path:
        return self._json_path(run_id).with_suffix(".csv")

    def list_runs(self) -> list[dict[str, Any]]:
        if not self.runs_dir.is_dir():
            return []
        runs = []
        for path in self.runs_dir.glob("*.json"):
            try:
                runs.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return sorted(runs, key=lambda r: r.get("created_at", ""), reverse=True)

    def get(self, run_id: str) -> dict[str, Any]:
        try:
            return json.loads(self._json_path(run_id).read_text(encoding="utf-8"))
        except OSError:
            raise HTTPException(404, "실험 기록을 찾을 수 없습니다.")

    def put(self, run: dict[str, Any]) -> None:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        path = self._json_path(run["id"])
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(run, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)

    def write_columns(self, run_id: str, columns: dict[str, list[float]]) -> None:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        with self.csv_path(run_id).open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(RUN_COLUMNS)
            n = len(columns.get("t_s", []))
            for i in range(n):
                w.writerow([columns[k][i] if k in columns and i < len(columns[k]) else "" for k in RUN_COLUMNS])

    def read_columns(self, run_id: str) -> dict[str, list[float]]:
        cols: dict[str, list[float]] = {k: [] for k in RUN_COLUMNS}
        try:
            with self.csv_path(run_id).open(newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    for k in RUN_COLUMNS:
                        try:
                            cols[k].append(float(row.get(k) or 0))
                        except ValueError:
                            cols[k].append(0.0)
        except OSError:
            raise HTTPException(404, "원자료 파일이 없습니다.")
        return cols

    def delete(self, run_id: str) -> None:
        for path in (self._json_path(run_id), self.csv_path(run_id)):
            try:
                path.unlink()
            except FileNotFoundError:
                pass


store = ExperimentStore(DATA_DIR)


# --------------------------------------------------------------------------- 분석 도우미
def bump_snapshot(settings: ExperimentSettings, bump_id: str) -> dict[str, Any]:
    bump = next((b for b in settings.bumps if b.id == bump_id), None)
    if bump is None:
        return {"id": bump_id, "name": bump_id, "height_mm": None, "width_mm": None, "radius_mm": None}
    radius = ia.bump_radius_mm(bump.height_mm, bump.width_mm)
    return {"id": bump.id, "name": bump.name or bump.id, "height_mm": bump.height_mm, "width_mm": bump.width_mm,
            "radius_mm": round(radius, 3) if radius else None}


def condition_dict(condition: ExperimentCondition) -> dict[str, Any]:
    data = condition.model_dump()
    data["total_mass_g"] = round(condition.base_mass_g + condition.added_mass_g, 2)
    return data


def run_analysis(run: dict[str, Any], columns: dict[str, list[float]], settings: ExperimentSettings) -> dict[str, Any]:
    bump = run.get("bump") or {}
    window = (max(0.0, (run.get("pre_roll_s") or 0) - 0.1), None)
    return ia.analyze_run(
        columns,
        wheelbase_mm=settings.wheelbase_mm,
        mass_g=run["condition"].get("total_mass_g"),
        bump_radius=bump.get("radius_mm"),
        wheel_radius_mm=settings.wheel_radius_mm,
        threshold_delta_g=settings.threshold_delta_g,
        min_gap_s=settings.min_gap_s,
        jump_g=settings.jump_g,
        window=window,
    )


def new_run(condition: ExperimentCondition, settings: ExperimentSettings, *, simulated: bool,
            source: str, pre_roll_s: float, run_seconds: float | None) -> dict[str, Any]:
    stamp = datetime.now(timezone.utc)
    return {
        "id": stamp.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6],
        "created_at": stamp.isoformat(),
        "simulated": simulated,
        "source": source,
        "aborted": False,
        "excluded": False,
        "condition": condition_dict(condition),
        "bump": bump_snapshot(settings, condition.bump_id),
        "pre_roll_s": pre_roll_s,
        "run_seconds": run_seconds,
        "recording": {},
        "analysis": {},
    }


def finalize_run(run: dict[str, Any], columns: dict[str, list[float]], meta: dict[str, Any], settings: ExperimentSettings) -> dict[str, Any]:
    run["recording"] = {k: meta.get(k) for k in ("started_at", "count", "sample_rate_hz", "vertical_axis", "vertical_sign", "calibration_stability_g")}
    run["analysis"] = run_analysis(run, columns, settings)
    if run.get("aborted"):
        run["excluded"] = True
    store.write_columns(run["id"], columns)
    store.put(run)
    return run


def run_summary(run: dict[str, Any]) -> dict[str, Any]:
    return {k: run.get(k) for k in ("id", "created_at", "simulated", "source", "aborted", "excluded", "condition", "bump", "analysis", "recording")}


def runs_for_mode(simulated: bool) -> list[dict[str, Any]]:
    return [r for r in store.list_runs() if bool(r.get("simulated")) == simulated]


def analysis_bundle(runs: list[dict[str, Any]], settings: ExperimentSettings) -> dict[str, Any]:
    groups = ia.aggregate(runs)
    bump_ids = sorted({g["bump_id"] for g in groups})
    fits = {bump_id: ia.fit_power_law(groups, bump_id) for bump_id in bump_ids}
    return {"groups": groups, "fits": fits}


# --------------------------------------------------------------------------- 자동 측정 주행
class AutoRunner:
    def __init__(self) -> None:
        self.state = "idle"
        self.message = ""
        self.run_id: str | None = None
        self.started_at: float | None = None
        self.phase_ends_at: float | None = None
        self.task: asyncio.Task | None = None
        self.last_result: dict[str, Any] | None = None

    @property
    def busy(self) -> bool:
        return self.state in ("pre_roll", "driving", "tail", "analyzing")

    def snapshot(self) -> dict[str, Any]:
        remaining = None
        if self.phase_ends_at is not None and self.busy:
            remaining = max(0.0, round(self.phase_ends_at - time.monotonic(), 2))
        return {"state": self.state, "message": self.message, "run_id": self.run_id,
                "phase_remaining_s": remaining, "result": self.last_result}

    def _phase(self, state: str, seconds: float | None, message: str) -> None:
        self.state, self.message = state, message
        self.phase_ends_at = time.monotonic() + seconds if seconds else None


auto_runner = AutoRunner()
_get_control: Callable[[], Any] | None = None


def bind_control(getter: Callable[[], Any]) -> None:
    global _get_control
    _get_control = getter


def control():
    if _get_control is None:
        raise HTTPException(503, "제어 서비스가 준비되지 않았습니다.")
    return _get_control()


async def _auto_run(request: AutoRunRequest, settings: ExperimentSettings) -> None:
    ctl = control()
    simulated = ctl.adapter.mode != "hardware"
    cond = request.condition
    pre_roll = request.pre_roll_s or settings.pre_roll_s
    run_seconds = request.run_seconds or settings.run_seconds
    tail = request.tail_s or settings.tail_s
    run = new_run(cond, settings, simulated=simulated, source="auto", pre_roll_s=pre_roll, run_seconds=run_seconds)
    auto_runner.run_id = run["id"]
    try:
        if not simulated:
            status = await asyncio.to_thread(mpu6050_service.start_recording)
            if status.get("error") or not status.get("recording"):
                raise RuntimeError(status.get("error") or "MPU-6050 기록을 시작하지 못했습니다.")
        auto_runner._phase("pre_roll", pre_roll, "정지 상태 기록 중 (차량을 움직이지 마세요)")
        await asyncio.sleep(pre_roll)

        auto_runner._phase("driving", run_seconds, f"직진 주행 중 · {cond.output_mode.upper()} {cond.output_value:g}")
        generation = ctl.stop_generation
        loop = asyncio.get_running_loop()
        end = loop.time() + run_seconds
        try:
            while loop.time() < end:
                if ctl.stop_generation != generation:
                    run["aborted"] = True
                    break
                ok = await ctl.experiment_drive(cond.output_mode, cond.output_value, generation)
                if not ok:
                    run["aborted"] = True
                    break
                await asyncio.sleep(0.1)
        finally:
            await ctl.stop(reason="experiment_auto_run_end")

        auto_runner._phase("tail", tail, "정지 후 진동 기록 중")
        await asyncio.sleep(tail)
        auto_runner._phase("analyzing", None, "분석·저장 중")
        if simulated:
            wheelbase = settings.wheelbase_mm or 95.0
            columns = ia.synthesize_run(
                output_mode=cond.output_mode, output_value=cond.output_value,
                mass_g=cond.base_mass_g + cond.added_mass_g, base_mass_g=cond.base_mass_g,
                wheelbase_mm=wheelbase, bump_radius=run["bump"].get("radius_mm"),
                wheel_radius_mm=settings.wheel_radius_mm or 33.0, pre_roll_s=pre_roll)
            meta = {"started_at": run["created_at"], "count": len(columns["t_s"]), "sample_rate_hz": 1000.0,
                    "vertical_axis": "z", "vertical_sign": 1.0, "calibration_stability_g": None}
        else:
            await asyncio.to_thread(mpu6050_service.stop_recording)
            meta = await asyncio.to_thread(mpu6050_service.recording_columns)
            columns = meta.pop("columns")
            if not columns["t_s"]:
                raise RuntimeError("센서 표본이 없어 저장하지 않았습니다.")
        run = await asyncio.to_thread(finalize_run, run, columns, meta, settings)
        auto_runner.last_result = run_summary(run)
        auto_runner._phase("aborted" if run["aborted"] else "done", None,
                           "정지 명령으로 중단됨 – 기록은 제외 상태로 저장했습니다." if run["aborted"] else "측정·저장 완료")
        record_event("experiment", "Auto run saved", details={"id": run["id"], "simulated": simulated, "aborted": run["aborted"]})
    except Exception as exc:
        try:
            await ctl.stop(reason="experiment_auto_run_error")
        except Exception:
            pass
        if not simulated:
            await asyncio.to_thread(mpu6050_service.stop_recording)
        auto_runner._phase("error", None, f"자동 측정 실패: {exc}")
        record_event("experiment", "Auto run failed", level="ERROR", details={"error": str(exc)})


# --------------------------------------------------------------------------- 라우터
router = APIRouter(prefix="/api/experiment", tags=["experiment"])


def _csv_response(rows: list[list[Any]], filename: str) -> Response:
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    # 엑셀에서 한글이 깨지지 않도록 UTF-8 BOM을 붙입니다.
    return Response(content="﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/settings")
async def get_settings():
    s = store.load_settings()
    return {**s.model_dump(), "bumps": [bump_snapshot(s, b.id) for b in s.bumps],
            "max_output": EXPERIMENT_MAX_OUTPUT}


@router.put("/settings")
async def put_settings(settings: ExperimentSettings):
    if len({b.id for b in settings.bumps}) != len(settings.bumps):
        raise HTTPException(422, "방지턱 ID가 중복되었습니다.")
    store.save_settings(settings)
    return await get_settings()


@router.get("/runs")
async def list_runs():
    ctl = control()
    simulated = ctl.adapter.mode != "hardware"
    settings = store.load_settings()
    runs = runs_for_mode(simulated)
    return {"simulated": simulated, "runs": [run_summary(r) for r in runs], **analysis_bundle(runs, settings)}


@router.get("/runs/{run_id}")
async def get_run(run_id: str, points: int = 1500):
    run = store.get(run_id)
    cols = await asyncio.to_thread(store.read_columns, run_id)
    series = ia.decimate_columns({k: cols[k] for k in ("t_s", "vertical_g", "dynamic_g")}, max(200, min(5000, points)))
    return {**run_summary(run), "series": series}


@router.get("/runs/{run_id}/raw.csv")
async def run_raw_csv(run_id: str):
    run = store.get(run_id)
    path = store.csv_path(run_id)
    if not path.is_file():
        raise HTTPException(404, "원자료 파일이 없습니다.")
    c = run["condition"]
    name = f"run_{c.get('bump_id')}_m{int(c.get('total_mass_g') or 0)}_{c.get('output_mode')}{int(c.get('output_value') or 0)}_t{c.get('trial')}_{run_id}.csv"
    return Response(content="﻿" + path.read_text(encoding="utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.patch("/runs/{run_id}")
async def update_run(run_id: str, update: RunUpdate):
    run = store.get(run_id)
    if update.excluded is not None:
        run["excluded"] = update.excluded
    if update.note is not None:
        run["condition"]["note"] = update.note
    store.put(run)
    return run_summary(run)


@router.delete("/runs/{run_id}")
async def delete_run(run_id: str):
    store.get(run_id)
    store.delete(run_id)
    return {"deleted": run_id}


@router.post("/reanalyze")
async def reanalyze():
    """설정(축간거리·검출 기준 등)을 바꾼 뒤 저장된 원자료로 다시 분석합니다."""
    settings = store.load_settings()
    count = 0
    for run in store.list_runs():
        try:
            cols = await asyncio.to_thread(store.read_columns, run["id"])
        except HTTPException:
            continue
        run["analysis"] = run_analysis(run, cols, settings)
        store.put(run)
        count += 1
    return {"reanalyzed": count}


@router.post("/save-recording")
async def save_recording(request: SaveRecordingRequest):
    """수동 측정(센서 화면의 측정 시작/정지)으로 기록한 데이터를 조건과 함께 저장합니다."""
    ctl = control()
    if ctl.adapter.mode != "hardware":
        raise HTTPException(409, "모의 모드에서는 자동 측정 주행을 사용하세요(합성 데이터).")
    if auto_runner.busy:
        raise HTTPException(409, "자동 측정 주행이 진행 중입니다.")
    await asyncio.to_thread(mpu6050_service.stop_recording)
    meta = await asyncio.to_thread(mpu6050_service.recording_columns)
    columns = meta.pop("columns")
    if not columns["t_s"]:
        raise HTTPException(409, "저장할 센서 표본이 없습니다.")
    settings = store.load_settings()
    run = new_run(request.condition, settings, simulated=False, source="manual", pre_roll_s=0.0, run_seconds=None)
    run = await asyncio.to_thread(finalize_run, run, columns, meta, settings)
    return run_summary(run)


@router.post("/auto-run")
async def start_auto_run(request: AutoRunRequest):
    ctl = control()
    if auto_runner.busy:
        raise HTTPException(409, "이미 자동 측정 주행이 진행 중입니다.")
    if not ctl.adapter.connected:
        raise HTTPException(409, ctl.adapter.connection_error or "PiCar-X가 연결되어 있지 않습니다.")
    if request.condition.output_value > EXPERIMENT_MAX_OUTPUT:
        raise HTTPException(422, f"실험 출력 상한은 {EXPERIMENT_MAX_OUTPUT}입니다.")
    if request.condition.output_value <= 0:
        raise HTTPException(422, "출력값은 0보다 커야 합니다.")
    if ctl.adapter.mode == "hardware":
        status = await asyncio.to_thread(mpu6050_service.status)
        if not status.get("available") or not status.get("calibrated"):
            raise HTTPException(409, status.get("error") or "센서 화면에서 MPU-6050 정지 상태 보정을 먼저 하세요.")
        if status.get("recording"):
            raise HTTPException(409, "수동 측정이 진행 중입니다. 먼저 정지하세요.")
    settings = store.load_settings()
    auto_runner.last_result = None
    auto_runner._phase("pre_roll", request.pre_roll_s or settings.pre_roll_s, "준비 중")
    auto_runner.task = asyncio.create_task(_auto_run(request, settings))
    return auto_runner.snapshot()


@router.get("/auto-run")
async def auto_run_status():
    return auto_runner.snapshot()


@router.get("/export/runs.csv")
async def export_runs():
    ctl = control()
    runs = runs_for_mode(ctl.adapter.mode != "hardware")
    head = ["run_id", "created_at", "simulated", "source", "excluded", "aborted", "trial", "bump_id", "bump_h_mm", "bump_w_mm",
            "bump_R_mm", "base_mass_g", "added_mass_g", "total_mass_g", "output_mode", "output_value",
            "sample_rate_hz", "peak_g", "peak_delta_g", "front_t_s", "rear_t_s", "axle_dt_s", "speed_mps",
            "video_speed_mps", "min_g", "jump", "peak_force_n", "dynamic_force_n", "pi", "critical_speed_mps", "flags", "note"]
    rows: list[list[Any]] = [head]
    for r in sorted(runs, key=lambda r: r.get("created_at", "")):
        c, a, b = r["condition"], r.get("analysis") or {}, r.get("bump") or {}
        rows.append([r["id"], r["created_at"], r.get("simulated"), r.get("source"), r.get("excluded"), r.get("aborted"),
                     c.get("trial"), c.get("bump_id"), b.get("height_mm"), b.get("width_mm"), b.get("radius_mm"),
                     c.get("base_mass_g"), c.get("added_mass_g"), c.get("total_mass_g"), c.get("output_mode"), c.get("output_value"),
                     a.get("sample_rate_hz"), a.get("peak_g"), a.get("peak_delta_g"), a.get("front_t_s"), a.get("rear_t_s"),
                     a.get("axle_dt_s"), a.get("speed_mps"), c.get("video_speed_mps"), a.get("min_g"), a.get("jump"),
                     a.get("peak_force_n"), a.get("dynamic_force_n"), a.get("pi"), a.get("critical_speed_mps"),
                     " / ".join(a.get("flags") or []), c.get("note")])
    return _csv_response(rows, "picarx_runs.csv")


@router.get("/export/conditions.csv")
async def export_conditions():
    ctl = control()
    settings = store.load_settings()
    bundle = analysis_bundle(runs_for_mode(ctl.adapter.mode != "hardware"), settings)
    head = ["bump_id", "total_mass_g", "output_mode", "output_value", "n_runs", "n_used", "mean_speed_mps",
            "speed_min_mps", "speed_max_mps", "mean_peak_g", "mean_peak_delta_g", "kept_min_delta_g", "kept_max_delta_g",
            "dropped_delta_g", "mean_force_n", "mean_pi", "jump_count"]
    rows: list[list[Any]] = [head]
    for g in bundle["groups"]:
        rows.append([g.get(k) if k != "dropped_delta_g" else " / ".join(str(x) for x in g[k]) for k in head])
    rows.append([])
    rows.append(["# 회귀식 (조건 평균, 로그 선형회귀)"])
    for bump_id, fit in bundle["fits"].items():
        rows.append([f"방지턱 {bump_id}", fit.get("formula") or fit.get("note") or ""])
        if fit.get("overall"):
            o = fit["overall"]
            rows.append(["k0", o["k0"], "n", o["n"], "p", o["p"], "R2", o["r2"]])
    return _csv_response(rows, "picarx_conditions.csv")
