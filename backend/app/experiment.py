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
import math
import os
import re
import shutil
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from . import impact_analysis as ia
from . import model_fit as mf
from .distance import DistanceLogger
from .vision import BumpCamera, LineCam, LineCamSettings, VisionSettings
from . import vision as vis
from . import distance as dist
from .line_follow import GuidedSteer, LineSettings, LineTracker, calibration_check, clean_raw
from .mpu6050 import mpu6050_service
from .services.event_log import record_event

DATA_DIR = Path(os.getenv("PICARX_DATA_DIR") or Path(__file__).resolve().parent.parent / "data")
EXPERIMENT_MAX_OUTPUT = max(1, min(100, int(os.getenv("PICARX_EXPERIMENT_MAX_OUTPUT", "100"))))
RUN_COLUMNS = ("t_s", "ax_g", "ay_g", "az_g", "gx_dps", "gy_dps", "gz_dps", "vertical_g", "dynamic_g")
ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{6,80}$")


# 허용 최대 수직 가속도 증가분(Δa, g) – 해석 가이드의 선행 연구에서 가져온 값
A_LIMIT_PRESETS: dict[str, dict[str, Any]] = {
    "trl614": {"delta_g": 0.7, "label": "0.7 g · TRL 614 (2004)",
               "source": "TRL 614(Kennedy 외, 2004): 차량 5종·탑승자 더미로 방지턱을 측정한 영국 교통부 연구. 탑승자가 받아들이지 않는 최대 수직 가속도 약 0.7 g."},
    "watts1973": {"delta_g": 0.9, "label": "0.9 g · Watts (1973)",
                  "source": "Watts(1973, TRRL LR597): 6명이 방지턱을 넘으며 불편 점수를 매긴 연구. 약 0.9 g부터 '불편'."},
    "kjemtrup04": {"delta_g": 0.4, "label": "0.4 g · Kjemtrup (환자·노약자)",
                   "source": "Kjemtrup 기준(Kyrollos 외 2020 인용, 구급차): 0.4 g에서 상당히 불편."},
    "kjemtrup02": {"delta_g": 0.2, "label": "0.2 g · Kjemtrup (매우 엄격)",
                   "source": "Kjemtrup 기준(Kyrollos 외 2020 인용, 구급차): 0.2 g에서 약간 불편."},
}


def effective_a_limit(settings: "ExperimentSettings") -> float | None:
    preset = A_LIMIT_PRESETS.get(settings.a_limit_preset)
    return round(1.0 + preset["delta_g"], 4) if preset else settings.a_limit_g


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
    filter_hz: float = Field(default=0, ge=0, le=100, description="진동 거르기(저역 통과) 기준 주파수 Hz, 0이면 끔")
    detect_level: Literal["strict", "normal", "sensitive"] = Field(default="normal", description="방지턱 감지 민감도")
    outlier_method: Literal["trim", "sigma2", "iqr", "none"] = Field(default="trim", description="조건별 평균의 이상치 제거 방식")
    a_limit_mode: Literal["manual", "first_jump"] = Field(default="manual", description="manual: 직접 입력, first_jump: 점프가 처음 나타난 조건의 평균 최대 가속도")
    a_limit_preset: Literal["trl614", "watts1973", "kjemtrup04", "kjemtrup02", "custom"] = Field(
        default="trl614", description="허용 기준 출처(선행 연구). custom이면 a_limit_g를 직접 씀")
    a_limit_g: float | None = Field(default=1.7, gt=1, le=8, description="허용 최대 수직 가속도(정지 1 g 포함)")
    pre_roll_s: float = Field(default=1.0, ge=0.3, le=5)
    run_seconds: float = Field(default=2.5, ge=0.3, le=6)
    tail_s: float = Field(default=0.6, ge=0.1, le=3)
    repeats: int = Field(default=5, ge=1, le=20)
    return_enabled: bool = True
    stop_after_bump: bool = Field(default=True, description="방지턱을 다 넘으면(차 기울기가 +, −를 거쳐 다시 수평) 자동 정지. 못 알아내면 주행 시간까지 달림")
    after_bump_s: float = Field(default=0.3, ge=0.0, le=2.0, description="다 넘은 뒤 더 달리는 시간(s)")
    return_stop_on_tape: bool = Field(default=False, description="후진 복귀 때 바닥의 출발선 테이프를 그레이스케일 센서가 보면 멈춤(라인 보정 필요)")
    return_output: float | None = Field(default=None, ge=1, le=100, description="복귀 후진 출력(없으면 측정 출력과 같음)")
    return_scale: float = Field(default=1.0, ge=0.3, le=3, description="복귀 후진 시간 = 주행 시간 × 배율")
    rest_s: float = Field(default=1.5, ge=0.3, le=10)
    heading_hold: bool = True
    heading_gain: float = Field(default=1.2, ge=0, le=5, description="조향 보정 각도 = 배율 × 방향 오차")
    heading_ki: float = Field(default=0.8, ge=0, le=5, description="쌓인 방향 오차를 없애는 적분 계수(1/s)")
    heading_learn: bool = Field(default=True, description="측정할 때마다 차가 한쪽으로 쏠리는 정도를 학습해 다음 출발부터 미리 보정")
    heading_ff_forward: float = Field(default=0.0, ge=-12, le=12, description="학습된 전진 기본 조향(°)")
    heading_ff_backward: float = Field(default=0.0, ge=-12, le=12, description="학습된 후진 기본 조향(°)")
    heading_sign: Literal[1, -1] = Field(default=1, description="자이로 보정 조향 방향(센서 장착 방향에 따라 반대일 수 있어 주행 중 자동 판정)")
    steering_source: Literal["gyro", "line", "none"] = Field(default="gyro", description="측정 직진 중 조향 방식")
    kick_ms: int = Field(default=0, ge=0, le=400, description="출발 순간 최대 출력으로 밀어 주는 시간(정지마찰 극복)")
    ultrasonic_log: bool = Field(default=False, description="측정 주행 중 초음파 거리 기록(벽까지 거리 → 속력)")
    ultrasonic_hz: float = Field(default=20.0, ge=5, le=40)
    wall_stop_cm: float = Field(default=0.0, ge=0, le=200, description="벽까지 이 거리(cm)보다 가까워지면 정지(0 = 끔)")
    vision: "VisionSettings" = Field(default_factory=lambda: VisionSettings())
    linecam: "LineCamSettings" = Field(default_factory=lambda: LineCamSettings())
    course_spin_lead_deg: float = Field(default=12.0, ge=0, le=60, description="코스 제자리 회전에서 배운 '일찍 끄는 각'")
    # 반복 측정의 '끝내기·복귀' 방식(바닥 테이프 코스)
    end_mode: Literal["time", "stop_line"] = Field(default="time", description="time: 주행 시간(또는 방지턱 통과 자동 정지), stop_line: 정지선(가로 테이프)을 보면 서서히 멈춤")
    accel_mode: Literal["instant", "ramp"] = Field(default="instant", description="출발: 바로 목표 출력 / 정해진 시간 동안 순차 가속")
    accel_s: float = Field(default=0.6, ge=0.05, le=3)
    decel_s: float = Field(default=0.3, ge=0.05, le=3, description="정지선을 본 순간부터 0까지 줄이는 시간")
    return_mode: Literal["none", "reverse", "uturn"] | None = Field(default=None, description="복귀: 없음 / 후진 / 제자리 180° 회전(비우면 return_enabled로 정함)")
    spin_output: float = Field(default=100, ge=30, le=100, description="제자리 회전 출력(앞바퀴가 끌려 돌기 때문에 높게)")
    spin_direction: Literal["left", "right"] = "left"
    creep_duty: float = Field(default=40, ge=15, le=100, description="선 위치 맞춤 때 실제 모터 듀티(%)")
    leg_timeout_s: float = Field(default=8.0, ge=1, le=30, description="정지선 모드에서 정지선을 못 볼 때 최대 주행 시간")
    cross_threshold: float = Field(default=0.5, ge=0.2, le=0.95, description="세 센서 모두 이 값(0~1) 이상이면 가로 테이프")
    model_id: str = Field(default="auto", max_length=20, description="관계식 모델(auto = 교차검증 오차 최소)")
    plan_added_masses: list[float] = Field(default_factory=lambda: [0.0, 150.0, 300.0])
    plan_outputs: list[float] = Field(default_factory=lambda: [40.0, 55.0, 70.0, 85.0, 100.0])
    plan_repeats: int = Field(default=5, ge=1, le=20)
    analysis_direction: Literal["forward", "backward"] = Field(default="forward", description="분석·관계식에 쓸 측정 방향(전진·후진 결과는 섞지 않음)")
    plan_bumps: list[str] | None = Field(default=None, description="진행표에 넣을 방지턱 ID(비우면 전체)")
    line: LineSettings = Field(default_factory=LineSettings)
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
    direction: Literal["forward", "backward"] = Field(default="forward", description="측정 주행 방향(후진이면 구동 바퀴가 먼저 턱을 오름)")
    video_speed_mps: float | None = Field(default=None, ge=0, le=10, description="영상으로 잰 속도(교차 검증용)")
    purpose: Literal["main", "validation", "pilot"] = Field(default="main", description="main: 식 도출, validation: 식 검증(회귀에서 제외), pilot: 예비")
    note: str = Field(default="", max_length=200)


class AutoRunRequest(BaseModel):
    condition: ExperimentCondition
    run_seconds: float | None = Field(default=None, ge=0.3, le=12)
    pre_roll_s: float | None = Field(default=None, ge=0.3, le=5)
    tail_s: float | None = Field(default=None, ge=0.1, le=3)
    repeats: int | None = Field(default=None, ge=1, le=20)
    return_enabled: bool | None = None
    return_stop_on_tape: bool | None = None
    return_output: float | None = Field(default=None, ge=1, le=100)
    return_scale: float | None = Field(default=None, ge=0.3, le=3)
    rest_s: float | None = Field(default=None, ge=0.3, le=10)
    heading_hold: bool | None = None
    steering_source: Literal["gyro", "line", "none"] | None = None
    kick_ms: int | None = Field(default=None, ge=0, le=400)
    ultrasonic: bool | None = None
    wall_stop_cm: float | None = Field(default=None, ge=0, le=200)
    slowdown: "SlowdownRequest | None" = None
    end_mode: Literal["time", "stop_line"] | None = None
    accel_mode: Literal["instant", "ramp"] | None = None
    accel_s: float | None = Field(default=None, ge=0.05, le=3)
    decel_s: float | None = Field(default=None, ge=0.05, le=3)
    return_mode: Literal["none", "reverse", "uturn"] | None = None
    spin_output: float | None = Field(default=None, ge=30, le=100)
    spin_direction: Literal["left", "right"] | None = None
    creep_duty: float | None = Field(default=None, ge=15, le=100)
    leg_timeout_s: float | None = Field(default=None, ge=1, le=30)
    start_delay_s: float | None = Field(default=None, ge=0, le=60, description="측정 시작을 누른 뒤 첫 회 출발까지 기다리는 시간(s)")


class SlowdownRequest(BaseModel):
    """목표 2: 관계식으로 '허용 기준을 넘지 않는 속도'를 구해, 방지턱 앞에서 그 속도에 맞는 출력으로 줄입니다."""
    trigger: Literal["time", "tape", "distance", "distance_auto", "camera", "camera_distance"] = "time"
    at_s: float = Field(default=0.6, ge=0.0, le=6, description="출발 후 이 시간(초)에 감속(trigger=time)")
    at_cm: float = Field(default=60.0, ge=5, le=300, description="벽까지 이 거리(cm)가 되면 감속(trigger=distance)")
    safety: float = Field(default=0.9, ge=0.3, le=1.0, description="v_max에 곱하는 여유율(0.9 = 10% 느리게)")
    min_output: float = Field(default=10.0, ge=1, le=100, description="이보다 낮은 출력으로는 줄이지 않음(멈춤 방지)")
    target_g: float | None = Field(default=None, gt=1.0, le=8, description="허용 기준(g)을 따로 줄 때. 비우면 분석 탭 기준")
    margin: Literal["ratio", "sigma"] = Field(default="ratio", description="ratio: v_max × 여유율, sigma: 기준 − 2×예측오차(교차검증 RMSE)로 목표 충격을 낮춤")
    staged: bool = Field(default=False, description="단계별: 앞바퀴(오름·내림) 구간과 뒷바퀴 구간을 각각의 식으로 정한 속도로")
    resume: bool = Field(default=True, description="방지턱을 다 넘으면 원래 출력으로 다시 가속")
    resume_after_s: float = Field(default=2.0, ge=0, le=10, description="기울기로 '다 넘음'을 못 알아채도 감속 후 이 시간(초)이 지나면 다시 가속(0 = 기울기만)")
    lead_cm: float = Field(default=30.0, ge=5, le=150, description="distance_auto: 지난 회차의 '앞바퀴 충격 때 벽까지 거리'보다 이만큼 먼저 감속")
    output: float | None = Field(default=None, ge=1, le=100, description="시험용: 감속 출력을 직접 지정(비우면 관계식으로 계산)")
    target_speed: float | None = Field(default=None, gt=0.01, le=2, description="시험용: 감속 목표 속도(m/s)를 직접 지정 → 속도식으로 출력 환산")
    exact_duty: bool = Field(default=True, description="감속 출력을 라이브러리 PWM(2단위로 잘림) 대신 같은 값의 듀티(PWM/2+50, 소수점까지)로 직접 명령")


AutoRunRequest.model_rebuild()


class SaveRecordingRequest(BaseModel):
    condition: ExperimentCondition


class PeakOverride(BaseModel):
    front_t_s: float = Field(ge=0)
    rear_t_s: float = Field(ge=0)


class RunUpdate(BaseModel):
    excluded: bool | None = None
    note: str | None = Field(default=None, max_length=200)
    purpose: Literal["main", "validation", "pilot"] | None = None
    peak_override: PeakOverride | None = None
    clear_peak_override: bool = False


class BulkDelete(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=2000)


class ResetRequest(BaseModel):
    scope: Literal["current", "all"] = "current"
    confirm: str


class SymmetryRequest(BaseModel):
    output_mode: Literal["pwm", "duty"] = "pwm"
    output_value: float = Field(default=60, gt=0, le=100)
    seconds: float = Field(default=1.0, ge=0.3, le=3)


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

    @property
    def trash_dir(self) -> Path:
        return self.root / "experiments_trash"

    def move_to_trash(self, run_ids: list[str], label: str) -> str:
        """초기화는 바로 지우지 않고 휴지통 폴더로 옮겨 되돌릴 수 있게 합니다."""
        batch = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + re.sub(r"[^A-Za-z0-9_-]", "", label)[:20]
        dest = self.trash_dir / batch
        dest.mkdir(parents=True, exist_ok=True)
        for run_id in run_ids:
            for path in (self._json_path(run_id), self.csv_path(run_id)):
                if path.exists():
                    shutil.move(str(path), str(dest / path.name))
        return batch

    def trash_batches(self) -> list[dict[str, Any]]:
        if not self.trash_dir.is_dir():
            return []
        out = []
        for d in sorted(self.trash_dir.iterdir(), reverse=True):
            if d.is_dir():
                out.append({"batch": d.name, "runs": len(list(d.glob("*.json")))})
        return out

    def _batch_dir(self, batch: str) -> Path:
        if not re.match(r"^[A-Za-z0-9_-]{6,60}$", batch):
            raise HTTPException(404, "휴지통 묶음을 찾을 수 없습니다.")
        d = self.trash_dir / batch
        if not d.is_dir():
            raise HTTPException(404, "휴지통 묶음을 찾을 수 없습니다.")
        return d

    def restore_batch(self, batch: str) -> int:
        d = self._batch_dir(batch)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        count = 0
        for path in list(d.iterdir()):
            target = self.runs_dir / path.name
            if not target.exists():
                shutil.move(str(path), str(target))
                count += path.suffix == ".json"
        shutil.rmtree(d, ignore_errors=True)
        return count

    def purge_batch(self, batch: str) -> None:
        shutil.rmtree(self._batch_dir(batch), ignore_errors=True)


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


def motor_summary(run: dict[str, Any], a: dict[str, Any]) -> dict[str, Any] | None:
    """좌우 모터 차이 기록(엔코더가 없어 간접 측정):
    - 좌우 바퀴에 실제로 보낸 PWM·듀티(출력 × 전·후진 배율 × 좌우 배율, 라이브러리 계산 int(PWM/2)+50)
    - 출발 0.3초 뒤 ~ 앞바퀴가 방지턱에 닿기 0.1초 전까지 자이로 방향의 기울기 = 차가 도는 속도(°/s, + 왼쪽, − 오른쪽).
      조향이 없거나 고정이면 이 값이 곧 좌우 모터 힘 차이(와 바퀴·바닥 차이)의 크기입니다.
    - 방지턱에 닿을 때 출발 방향에서 틀어진 각도(°)"""
    out: dict[str, Any] = {}
    c = run.get("condition") or {}
    drv = ((run.get("applied") or {}).get("drive")) or None
    if drv and (c.get("output_mode") or "pwm") == "pwm":
        trim = float(drv.get("trim_backward" if c.get("direction") == "backward" else "trim_forward") or 1.0)
        base = max(0, min(100, round(float(c.get("output_value") or 0) * trim)))
        lp, rp = min(100.0, base * float(drv.get("trim_left") or 1.0)), min(100.0, base * float(drv.get("trim_right") or 1.0))
        duty = lambda x: (int(x / 2) + 50) if x > 0 else 0  # noqa: E731
        out.update(left_pwm=round(lp, 1), right_pwm=round(rp, 1), left_duty=duty(lp), right_duty=duty(rp),
                   trim_left=drv.get("trim_left"), trim_right=drv.get("trim_right"), trim_forward=drv.get("trim_forward"))
    log = run.get("drive_log") or []
    t0 = run.get("drive_t0_s") if run.get("drive_t0_s") is not None else (run.get("pre_roll_s") or 0.0)
    tb = (a.get("front_t_s") - t0) if a.get("front_t_s") is not None else None
    pts = [(row[0], row[3]) for row in log if len(row) > 3 and row[3] is not None and row[0] >= 0.3 and (tb is None or row[0] <= tb - 0.1)]
    if tb is None:
        pts = [p for p in pts if p[0] <= 2.0]
    if len(pts) >= 4:
        n = len(pts)
        mt, mh = sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n
        den = sum((p[0] - mt) ** 2 for p in pts)
        if den > 0:
            out["yaw_rate_dps"] = round(sum((p[0] - mt) * (p[1] - mh) for p in pts) / den, 2)
    if tb is not None and log:
        near = min((row for row in log if len(row) > 3 and row[3] is not None), key=lambda row: abs(row[0] - tb), default=None)
        if near is not None:
            out["heading_at_bump_deg"] = near[3]
    out["steering"] = run.get("steering_source")
    return out or None


def run_analysis(run: dict[str, Any], columns: dict[str, list[float]], settings: ExperimentSettings) -> dict[str, Any]:
    a = _analyze_columns(run, columns, settings)
    try:
        run["motor"] = motor_summary(run, a)
    except Exception:
        run["motor"] = None
    yaw = run.get("heading_max_deg")
    if yaw is not None and abs(yaw) >= 8 and a.get("flags") is not None:
        a["flags"].append(f"주행 중 차 방향이 최대 {abs(yaw):.0f}° 틀어졌습니다. 좌우 방지턱에 왼쪽·오른쪽 바퀴가 다른 시각에 닿아 "
                          "피크가 갈라지고 앞·뒤 바퀴 짝(속도)이 틀릴 수 있습니다. '측정 중 조향'을 자이로 직진 보정으로 하세요.")
    # 분석에 딸린 값(초음파 속력 분석, 감속 제어 판정)도 새 분석에 맞춰 다시 계산합니다.
    if run.get("distance"):
        run["distance_summary"] = _distance_summary(run, a)
    if run.get("slowdown"):
        sd = run["slowdown"]
        sd["measured_speed_mps"] = a.get("speed_mps")
        if a.get("peak_g") is not None and sd.get("a_limit_g") and a.get("bump_detected"):
            sd["measured_peak_g"] = a["peak_g"]
            sd["pass"] = bool(a["peak_g"] <= sd["a_limit_g"])
        else:
            sd["measured_peak_g"] = sd["pass"] = None   # 방지턱을 못 찾았거나 센서 기록이 깨진 회차는 판정하지 않음
    return a


def _distance_summary(run: dict[str, Any], a: dict[str, Any]) -> dict[str, Any]:
    sd = run.get("slowdown") or {}
    ignore = sd.get("slowed_at_s") + 0.4 if sd.get("slowed_at_s") is not None else None
    return dist.analyze(run["distance"]["t_s"], run["distance"]["cm"], a.get("front_t_s"), a.get("rear_t_s"),
                        a.get("speed_mps"), (run.get("condition") or {}).get("direction") or "forward", ignore)


def _analyze_columns(run: dict[str, Any], columns: dict[str, list[float]], settings: ExperimentSettings) -> dict[str, Any]:
    bump = run.get("bump") or {}
    window = (max(0.0, (run.get("pre_roll_s") or 0) - 0.1), None)
    return ia.analyze_run(
        columns,
        wheelbase_mm=settings.wheelbase_mm,
        mass_g=run["condition"].get("total_mass_g"),
        bump_radius=bump.get("radius_mm"),
        bump_width_mm=bump.get("width_mm"),
        bump_height_mm=bump.get("height_mm"),
        wheel_radius_mm=settings.wheel_radius_mm,
        threshold_delta_g=settings.threshold_delta_g,
        min_gap_s=settings.min_gap_s,
        jump_g=settings.jump_g,
        window=window,
        override=run.get("peak_override"),
        filter_hz=settings.filter_hz,
        detect_level=settings.detect_level,
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


SERVER_STARTED = datetime.now(timezone.utc).isoformat()


def _code_version() -> str | None:
    """지금 서버가 돌리는 코드의 git 커밋(앞 7자리). 업데이트 후 재시작했는지 확인용."""
    try:
        root = Path(__file__).resolve().parents[2] / ".git"
        head = (root / "HEAD").read_text().strip()
        if head.startswith("ref: "):
            ref = head[5:]
            p = root / ref
            if p.exists():
                return p.read_text().strip()[:7]
            for line in (root / "packed-refs").read_text().splitlines():
                if line.endswith(ref):
                    return line[:7]
        return head[:7]
    except Exception:
        return None


def applied_snapshot(opts: dict[str, Any] | None = None) -> dict[str, Any]:
    """실제로 적용되는 주행·조향·학습 설정을 한곳에 모읍니다(화면 표시, 회차마다 기록)."""
    st = store.load_settings()
    snap: dict[str, Any] = {
        "server_started": SERVER_STARTED, "code_version": _code_version(),
        "heading_learn": st.heading_learn, "heading_ff_forward": st.heading_ff_forward, "heading_ff_backward": st.heading_ff_backward,
        "heading_sign": st.heading_sign, "heading_gain": st.heading_gain, "heading_ki": st.heading_ki,
        "line_steer_ff_deg": st.line.steer_ff_deg, "line_learn_steer": st.line.learn_steer,
        "steering_source_default": st.steering_source, "kick_ms_default": st.kick_ms,
    }
    try:
        ctl = control()
        snap.update(drive=dict(ctl.drive_settings), steering_center=ctl.steering_center, adapter_mode=ctl.adapter.mode,
                    wheel_trim=list(getattr(ctl.adapter, "wheel_trim", (1.0, 1.0)) or (1.0, 1.0)),
                    last_command=(ctl.adapter.last_command or None))
    except Exception:
        pass
    try:
        if snap.get("adapter_mode") == "hardware":
            snap["mpu"] = {"read_mode": "fifo" if mpu6050_service._fifo else "direct", "bad_reads": mpu6050_service.bad_reads,
                           "io_errors": mpu6050_service.io_errors, "heading_active": mpu6050_service.heading_deg() is not None}
    except Exception:
        pass
    if opts:
        snap["run"] = {k: opts.get(k) for k in ("steering_source", "heading_hold", "kick_ms", "accel_mode", "accel_s", "run_seconds",
                                                "pre_roll", "end_mode", "return_mode", "wall_stop_cm", "ultrasonic")}
    return snap


def run_summary(run: dict[str, Any]) -> dict[str, Any]:
    return {k: run.get(k) for k in ("id", "created_at", "simulated", "source", "aborted", "excluded", "condition", "bump", "analysis", "recording",
                                    "heading_hold", "heading_max_deg", "heading_end_deg", "battery_v", "steering_source",
                                    "peak_override", "data_source", "line_lost", "kick_ms", "steer_mean_late",
                                    "heading_ff_learned", "heading_flipped", "steer_saturated", "bump_passed_s", "elapsed_s",
                                    "slowdown", "distance_summary", "stopped_by_wall", "course", "guided",
                                    "camera_line", "steer_ff_learned", "pre_roll_s", "end_reason", "drive_t0_s", "applied", "motor", "heading_end_deg")}


def runs_for_mode(simulated: bool) -> list[dict[str, Any]]:
    return [r for r in store.list_runs() if bool(r.get("simulated")) == simulated]


def criterion_for(bump_id: str, groups: list[dict[str, Any]], model: dict[str, Any] | None, settings: ExperimentSettings) -> dict[str, Any]:
    """허용 최대 수직 가속도 기준과 질량별 최대 통과 속도(v_max, 선택한 관계식으로 계산)."""
    bump = bump_snapshot(settings, bump_id)
    r_eff = ia.effective_radius_m(bump.get("radius_mm"), settings.wheel_radius_mm)
    out: dict[str, Any] = {"mode": settings.a_limit_mode, "a_limit_g": None, "source": "", "vmax_by_mass": [],
                           "critical_speed_mps": round(ia.critical_speed_mps(r_eff), 4) if r_eff else None,
                           "model_id": model["id"] if model else None}
    mine = [g for g in groups if g["bump_id"] == bump_id]
    if settings.a_limit_mode == "manual":
        out["a_limit_g"] = effective_a_limit(settings)
        preset = A_LIMIT_PRESETS.get(settings.a_limit_preset)
        out["source"] = preset["source"] if preset else "직접 입력한 허용 기준"
        out["preset"] = settings.a_limit_preset
    else:
        jumped = [g for g in mine if g["jump_count"] > 0 and g.get("mean_speed_mps") and g.get("mean_peak_g")]
        if jumped:
            first = min(jumped, key=lambda g: g["mean_speed_mps"])
            out["a_limit_g"] = first["mean_peak_g"]
            out["source"] = (f"점프가 처음 나타난 조건({first['total_mass_g']} g, {first['output_mode'].upper()} "
                             f"{first['output_value']}, v ≈ {first['mean_speed_mps']:.3f} m/s)의 평균 최대 가속도")
        else:
            out["source"] = "아직 점프한 조건이 없어 기준을 정할 수 없습니다(직접 입력으로 바꾸거나 더 빠른 조건을 측정)."
    if out["a_limit_g"]:
        speeds = [g["mean_speed_mps"] for g in mine if g.get("mean_speed_mps")]
        lo = min(speeds) if speeds else None
        for mass in sorted({g["total_mass_g"] for g in mine}):
            v = mf.solve_speed(model, out["a_limit_g"] - 1.0, mass / 1000.0)
            # 측정한 가장 느린 속도의 절반보다 낮게 나오면 식을 측정 범위 밖으로 늘려 쓴 값이라 의미가 없습니다.
            below = v is not None and lo is not None and v < 0.5 * lo
            out["vmax_by_mass"].append({"mass_g": mass, "vmax_mps": None if below else v,
                                        "note": f"측정한 가장 느린 속도({lo:.2f} m/s)에서도 이미 기준을 넘음" if below else None})
    return out


def _points(groups: list[dict[str, Any]], bump_id: str) -> list[tuple[float, float, float]]:
    return [(g["mean_speed_mps"], g["total_mass_g"] / 1000.0, g["mean_peak_delta_g"]) for g in groups
            if g["bump_id"] == bump_id and g.get("mean_speed_mps") and g.get("mean_peak_delta_g") and g["mean_peak_delta_g"] > 0]


def purpose_of(run: dict[str, Any]) -> str:
    return (run.get("condition") or {}).get("purpose") or "main"


def validation_rows(runs: list[dict[str, Any]], models: dict[str, dict[str, Any] | None]) -> dict[str, Any]:
    """검증용 시행: 회귀에 쓰지 않은 데이터로 식의 예측과 실측을 비교합니다."""
    rows = []
    for r in runs:
        if purpose_of(r) != "validation" or r.get("excluded"):
            continue
        a, c = r.get("analysis") or {}, r["condition"]
        model = models.get(c.get("bump_id"))
        v, d = a.get("speed_mps"), a.get("peak_delta_g")
        pred = mf.predict(model, v, c["total_mass_g"] / 1000.0) if model and v and d is not None else None
        err = (d - pred) / pred * 100 if pred and d is not None else None
        rows.append({"run_id": r["id"], "created_at": r["created_at"], "bump_id": c.get("bump_id"), "total_mass_g": c["total_mass_g"],
                     "output_mode": c.get("output_mode"), "output_value": c.get("output_value"), "trial": c.get("trial"),
                     "speed_mps": v, "measured_delta_g": d, "predicted_delta_g": pred, "error_pct": err,
                     "abs_error_g": None if pred is None or d is None else d - pred, "model_id": model["id"] if model else None})
    errs = [abs(x["error_pct"]) for x in rows if x["error_pct"] is not None]
    signed = [x["error_pct"] for x in rows if x["error_pct"] is not None]
    summary = {"count": len(rows), "evaluated": len(errs),
               "mape": sum(errs) / len(errs) if errs else None,
               "bias_pct": sum(signed) / len(signed) if signed else None,
               "within_10pct": sum(1 for e in errs if e <= 10), "within_20pct": sum(1 for e in errs if e <= 20)}
    return {"rows": sorted(rows, key=lambda x: x["created_at"], reverse=True), "summary": summary}


def plan_progress(runs: list[dict[str, Any]], settings: ExperimentSettings) -> dict[str, Any]:
    """실험 계획(방지턱 × 추가 질량 × 출력) 대비 완료 횟수."""
    done: dict[tuple, int] = {}
    for r in runs:
        if r.get("excluded") or purpose_of(r) != "main":
            continue
        c = r["condition"]
        key = (c.get("bump_id"), round(float(c.get("added_mass_g") or 0)), c.get("output_mode"), round(float(c.get("output_value") or 0)))
        done[key] = done.get(key, 0) + 1
    cells = []
    chosen = [b for b in settings.bumps if not settings.plan_bumps or b.id in settings.plan_bumps] or list(settings.bumps)
    for b in chosen:
        for mass in settings.plan_added_masses:
            for out in settings.plan_outputs:
                n = sum(v for (bid, m, _mode, o), v in done.items() if bid == b.id and m == round(mass) and o == round(out))
                cells.append({"bump_id": b.id, "added_mass_g": mass, "output_value": out, "done": n, "need": settings.plan_repeats})
    total_need = len(cells) * settings.plan_repeats
    total_done = sum(min(c["done"], c["need"]) for c in cells)
    nxt = next((c for c in cells if c["done"] < c["need"]), None)
    return {"cells": cells, "done": total_done, "need": total_need, "next": nxt}


def analysis_bundle(runs: list[dict[str, Any]], settings: ExperimentSettings) -> dict[str, Any]:
    want = settings.analysis_direction
    runs = [r for r in runs if (r.get("condition", {}).get("direction") or "forward") == want]
    main_runs = [r for r in runs if purpose_of(r) == "main"]
    groups = ia.aggregate(main_runs, settings.outlier_method)
    bump_ids = sorted({g["bump_id"] for g in groups})
    fits = {bump_id: ia.fit_power_law(groups, bump_id) for bump_id in bump_ids}
    comparisons = {bump_id: mf.compare_models(_points(groups, bump_id)) for bump_id in bump_ids}
    chosen = {bump_id: mf.choose(comparisons[bump_id], settings.model_id) for bump_id in bump_ids}
    criteria = {bump_id: criterion_for(bump_id, groups, chosen[bump_id], settings) for bump_id in bump_ids}
    interpretation = {bump_id: mf.interpret(bump_id, groups, comparisons[bump_id], chosen[bump_id], criteria[bump_id]) for bump_id in bump_ids}
    return {"groups": groups, "fits": fits, "models": comparisons,
            "chosen_models": {k: (v["id"] if v else None) for k, v in chosen.items()},
            "criteria": criteria, "interpretation": interpretation,
            "speed_maps": mf.speed_maps(groups), "speed_models": mf.speed_models(groups), "event_models": mf.event_models(groups), "validation": validation_rows(runs, chosen),
            "progress": plan_progress(runs, settings)}


# --------------------------------------------------------------------------- 자동 측정 주행
BUSY_STATES = ("pre_roll", "driving", "tail", "analyzing", "returning", "turning", "aligning", "rest")
MAX_HEADING_CORRECTION_DEG = 12.0


class AutoRunner:
    def __init__(self) -> None:
        self.state = "idle"
        self.message = ""
        self.run_id: str | None = None
        self.phase_ends_at: float | None = None
        self.task: asyncio.Task | None = None
        self.last_result: dict[str, Any] | None = None
        self.cycle = 0
        self.cycles = 0
        self.completed: list[str] = []
        # 주행 중 실시간 값(지금 낸 출력·감속 여부·카메라 점수·초음파 거리). 화면에서 0.3초마다 읽음
        self.live: dict[str, Any] | None = None

    @property
    def busy(self) -> bool:
        return self.state in BUSY_STATES

    def snapshot(self) -> dict[str, Any]:
        remaining = None
        if self.phase_ends_at is not None and self.busy:
            remaining = max(0.0, round(self.phase_ends_at - time.monotonic(), 2))
        heading = mpu6050_service.heading_deg() if self.busy else None
        return {"state": self.state, "message": self.message, "run_id": self.run_id,
                "phase_remaining_s": remaining, "result": self.last_result,
                "cycle": self.cycle, "cycles": self.cycles, "completed_run_ids": list(self.completed),
                "heading_deg": round(heading, 2) if heading is not None else None,
                "live": self.live if self.busy else None}

    def _phase(self, state: str, seconds: float | None, message: str) -> None:
        self.state, self.message = state, message
        self.phase_ends_at = time.monotonic() + seconds if seconds else None


auto_runner = AutoRunner()
extra_busy: list[Callable[[], bool]] = []  # 다른 주행(코스 반복 등)이 진행 중인지
_get_control: Callable[[], Any] | None = None


def bind_control(getter: Callable[[], Any]) -> None:
    global _get_control
    _get_control = getter


def control():
    if _get_control is None:
        raise HTTPException(503, "제어 서비스가 준비되지 않았습니다.")
    return _get_control()


class Aborted(Exception):
    """사용자 정지 명령으로 반복 측정을 멈춥니다."""


async def _wait(ctl, seconds: float, generation: int) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + seconds
    while loop.time() < end:
        if ctl.stop_generation != generation:
            raise Aborted()
        await asyncio.sleep(min(0.05, max(0.0, end - loop.time())))


class BumpPassWatcher:
    """차 기울기로 '방지턱을 다 넘었는지' 실시간 판단합니다.

    앞바퀴가 오르면 한쪽으로 기울고(+), 이후 반대쪽(−)을 거쳐(앞바퀴가 내려오거나 뒷바퀴가 오름),
    마지막에 뒷바퀴가 내려오면 다시 수평이 됩니다. 방지턱 폭과 상관없이 '+ → − → 수평' 순서입니다.
    """

    def __init__(self, up_deg: float = 2.5, down_deg: float = 1.5, level_deg: float = 1.0, level_s: float = 0.12) -> None:
        self.up, self.down, self.level, self.level_s = up_deg, down_deg, level_deg, level_s
        self.axis: int | None = None
        self.sign = 1.0
        self.stage = "wait_up"
        self.level_since: float | None = None
        self.events: dict[str, float] = {}

    def update(self, tilt: tuple[float, float] | None, now: float) -> bool:
        if tilt is None:
            return False
        if self.axis is None:
            for n in (0, 1):
                if abs(tilt[n]) >= self.up:
                    self.axis, self.sign = n, (1.0 if tilt[n] > 0 else -1.0)
                    self.stage = "wait_down"
                    self.events["up"] = now
                    break
            return False
        a = self.sign * tilt[self.axis]
        if self.stage == "wait_down" and "front_level" not in self.events and "up" in self.events and abs(a) <= self.level \
                and now - self.events["up"] >= 0.03:
            self.events["front_level"] = now  # 좁은 방지턱: 앞바퀴가 다 내려와 수평으로 돌아온 순간(FD)
        if self.stage == "wait_down" and a <= -self.down:
            self.stage = "wait_level"
            self.events["down"] = now
        elif self.stage == "wait_level":
            if abs(a) <= self.level:
                self.level_since = self.level_since if self.level_since is not None else now
                if now - self.level_since >= self.level_s:
                    self.stage = "passed"
                    self.events["passed"] = now
                    return True
            else:
                self.level_since = None
        return self.stage == "passed"


class HeadingController:
    """자이로 방향 유지(비례 + 적분 + 학습된 기본 조향).

    - 비례: 지금 틀어진 만큼 반대로 조향
    - 적분: 계속 한쪽으로 틀어져 있으면 조향을 조금씩 더함. 조향이 한계에 붙어 있거나 오차 부호가 바뀌면
      적분을 쌓지 않거나 줄여서(와인드업 방지) 한쪽으로 계속 꺾인 채 남지 않게 합니다.
    - 기본 조향(ff): 지난 측정에서 배운 '직진하려면 필요한 조향'(최대 ±5°)
    - 방향 자동 판정: 조향을 한쪽으로 크게 주는데도 오차가 계속 커지면(센서 장착 방향 때문에 보정이
      거꾸로 걸린 것) 조향 방향을 뒤집습니다. ``flipped``가 True가 됩니다.
    """

    def __init__(self, kp: float, ki: float, ff: float, limit: float = MAX_HEADING_CORRECTION_DEG, polarity: int = 1) -> None:
        self.kp, self.ki, self.limit = kp, ki, limit
        self.ff = max(-5.0, min(5.0, ff))
        self.polarity = polarity
        self.integral = 0.0
        self.prev_err: float | None = None
        self.wrong_since: float | None = None
        self.wrong_start_err = 0.0
        self.elapsed = 0.0
        self.flipped = False

    def update(self, heading: float, dt: float, sign: float) -> float:
        self.elapsed += max(0.0, dt)
        err = heading * sign * self.polarity  # 왼쪽으로 돈 상태(+)에서 전진이면 오른쪽(+) 조향, 후진이면 왼쪽(−)
        if self.prev_err is not None and err * self.prev_err < 0:
            self.integral *= 0.3  # 목표를 지나쳤으면 쌓인 보정을 크게 덜어 냄
        raw = self.ff + self.kp * err + self.ki * self.integral
        saturated = abs(raw) >= self.limit
        if self.ki > 0 and not (saturated and raw * err > 0):
            self.integral = max(-self.limit / self.ki, min(self.limit / self.ki, self.integral + err * dt))
        steer = max(-self.limit, min(self.limit, self.ff + self.kp * err + self.ki * self.integral))
        # 보정 방향 점검: 오차를 줄이는 쪽으로 6° 이상 꺾고 있는데 0.6초 동안 오차가 3° 넘게 더 커지면 거꾸로 걸린 것
        if abs(steer) >= 6 and steer * err > 0 and abs(err) > 3:
            if self.wrong_since is None:
                self.wrong_since, self.wrong_start_err = self.elapsed, abs(err)
            elif self.elapsed - self.wrong_since >= 0.6 and abs(err) - self.wrong_start_err > 3:
                self.polarity *= -1
                self.flipped = True
                self.integral = 0.0
                self.wrong_since = None
                err = -err
                steer = max(-self.limit, min(self.limit, self.ff + self.kp * err))
        else:
            self.wrong_since = None
        self.prev_err = err
        return steer


async def _drive_for(ctl, mode: str, value: float, seconds: float, direction: str, generation: int,
                     heading_hold: bool, gain: float, track: bool | None = None,
                     line: LineTracker | None = None, kick_ms: int = 0,
                     ki: float = 0.0, ff: float = 0.0,
                     stop_when: Callable[[Any], bool] | None = None, min_s: float = 0.0, polarity: int = 1,
                     bump_watch: "BumpPassWatcher | None" = None, after_bump_s: float = 0.3,
                     slowdown: dict[str, Any] | None = None, distance: "DistanceLogger | None" = None,
                     wall_stop_cm: float = 0.0, slow_tape: Callable[[Any], bool] | None = None,
                     camera: "BumpCamera | None" = None, stop_on_pass: bool = True,
                     ramp_s: float = 0.0, decel_s: float = 0.0, cross: Any = None, guided: Any = None) -> dict[str, Any]:
    """정해진 시간 동안 일정한 출력으로 직진/후진.

    출력은 처음 한 번 정하면 바꾸지 않습니다(관계식이나 방지턱 위치와 무관). 조향만 바뀝니다.
    조향: 라인 추종(테이프) > 자이로 방향 유지 > 조향 없음 순서로 사용합니다.
    ``kick_ms`` 동안은 최대 출력으로 밀어 정지마찰을 넘긴 뒤 설정 출력으로 바꿉니다(기본 끔).
    ``ramp_s``: 출발할 때 그 시간 동안 실제 듀티를 0→목표로 순차 가속.
    ``cross``(course.CrossWatcher): 정지선(가로 테이프)을 보면 그 순간부터 ``decel_s`` 동안 0까지 줄여 멈춤.
    ``guided``(GuidedSteer): 유도선 + 자이로 조향(방지턱 위에서는 자이로로 방향 유지).
    """
    loop = asyncio.get_running_loop()
    start = loop.time()
    end = start + seconds
    sign = 1.0 if direction == "forward" else -1.0
    auto_runner.live = None
    worst = 0.0
    last = None
    line_lost = False
    controller = HeadingController(gain, ki, ff, polarity=polarity) if heading_hold and line is None else None
    log: list[list[float]] = []
    prev_t = start
    stopped_on_mark = False
    passed_at: float | None = None
    mark_seen = asyncio.Event()
    poller: asyncio.Task | None = None
    if stop_when is not None:
        # 정지선(테이프)은 폭이 1.5~2 cm라 0.3 m/s면 약 0.06초만 센서 아래에 있습니다. 주행 루프(0.05초)와 따로
        # 바닥 센서를 쉬지 않고 읽어(수 ms 간격) 두 번 연속 보이면 표시합니다.
        async def poll() -> None:
            hits = 0
            while not mark_seen.is_set():
                if loop.time() - start >= min_s:
                    raw = await asyncio.to_thread(ctl.adapter.read_grayscale)
                    hits = hits + 1 if stop_when(raw) else 0
                    if hits >= 2:
                        mark_seen.set()
                        return
                    await asyncio.sleep(0.002)
                else:
                    await asyncio.sleep(0.01)
        poller = asyncio.create_task(poll())
    stage_now, stage_prev, stage_log = "approach", None, []
    resume_at: float | None = None
    resume_by: str | None = None
    decel_at: float | None = None
    if cross is not None:
        cross.start(time.monotonic() + min_s)
    slow_seen = asyncio.Event()
    slow_poller: asyncio.Task | None = None
    slowed_at: float | None = None
    slowed_by: str | None = None
    stopped_by_wall = False
    wall_hits, wall_last = 0, -1
    tilt_base: tuple[float, float] | None = None
    if slowdown and slowdown.get("trigger") == "tape" and slow_tape is not None:
        # 방지턱 앞에 붙인 '감속 표시 테이프'를 정지선과 같은 방식(수 ms 간격, 2번 연속)으로 찾습니다.
        async def poll_slow() -> None:
            hits = 0
            while not slow_seen.is_set():
                raw = await asyncio.to_thread(ctl.adapter.read_grayscale)
                hits = hits + 1 if slow_tape(raw) else 0
                if hits >= 2:
                    slow_seen.set()
                    return
                await asyncio.sleep(0.002)
        slow_poller = asyncio.create_task(poll_slow())
    try:
      while loop.time() < end:
          if ctl.stop_generation != generation:
              raise Aborted()
          now = loop.time()
          if cross is not None and decel_at is None and cross.seen.is_set():
              decel_at = now
              end = max(end, now + decel_s + 0.05)
          steer = 0.0
          heading = mpu6050_service.heading_deg() if (heading_hold if track is None else track) else None
          if heading is not None:
              last = heading
              worst = max(worst, abs(heading))
              if controller is not None:
                  steer = controller.update(heading, now - prev_t, sign)
          if guided is not None and heading is not None:
              raw = cross.raw if cross is not None and cross.raw is not None else await asyncio.to_thread(ctl.adapter.read_grayscale)
              steer, ok = guided.update(raw, heading, mpu6050_service.tilt_deg(), now)
              if not ok and decel_at is None:
                  line_lost = True
                  break
          elif line is not None:
              raw = cross.raw if cross is not None and cross.raw is not None else await asyncio.to_thread(ctl.adapter.read_grayscale)
              steer, ok = line.update(raw, direction)
              if not ok and decel_at is None:
                  line_lost = True
                  break
          # 감속 회차는 감속을 시작하고 0.3초 뒤부터 '다 넘음'을 봅니다. 출발할 때 차가 뒤로 젖혔다 돌아오는 기울기
          # (+ → − → 수평)를 방지턱으로 착각해 출발 직후 멈추던 문제를 막기 위해서입니다.
          # 감속 회차: 출발 직후 0.35초(차가 뒤로 젖혔다 돌아오는 출발 흔들림)가 지나면 그 순간 기울기를 0으로 잡고
          # 방지턱 통과(앞바퀴 오름 + → 뒷바퀴 오름 − → 수평)를 자이로로 봅니다. 예전에는 감속 0.3초 뒤부터 봐서,
          # 감속이 늦으면 앞바퀴 오름을 놓쳐 '다 넘음'을 못 알아채고 시간으로만 다시 가속했습니다.
          watch_armed = slowdown is None or now - start >= 0.35
          if slowdown is not None and slowed_at is not None and now - slowed_at < 0.3 and bump_watch is not None and bump_watch.axis is None:
              # 방지턱 전 감속 순간에 차 앞이 살짝 숙여지는 흔들림은 무시하고, 가라앉은 뒤 기울기를 다시 0으로
              watch_armed, tilt_base = False, None
          tl_now = mpu6050_service.tilt_deg() if bump_watch is not None else None
          if slowdown is not None and watch_armed and tl_now is not None and tilt_base is None:
              tilt_base = tl_now   # 지켜보기 시작한 순간의 기울기를 0으로(자이로 적분이 조금 흘러도 오판 안 하게)
          if tilt_base is not None and tl_now is not None:
              tl_now = (tl_now[0] - tilt_base[0], tl_now[1] - tilt_base[1])
          if bump_watch is not None and watch_armed and passed_at is None and bump_watch.update(tl_now, now - start):
              passed_at = now
              if stop_on_pass:
                  end = min(end, now + after_bump_s)
          if mark_seen.is_set():
              stopped_on_mark = True
              break
          near = distance.latest() if distance is not None else None
          # 초음파가 바닥·방지턱에 튄 값 한 번으로 멈추지 않도록 새 값이 2번 연속 가까울 때만 정지
          if wall_stop_cm and near is not None and near <= wall_stop_cm and distance is not None:
              key = len(distance.samples)
              if key != wall_last:
                  wall_last = key
                  wall_hits += 1
              if wall_hits >= 2:
                  stopped_by_wall = True
                  break
          elif wall_stop_cm:
              wall_hits = 0
          if slowdown and slowed_at is None and slowdown.get("output") is not None:
              trig = slowdown.get("trigger")
              by_dist = trig in ("distance", "camera_distance") and near is not None and near <= float(slowdown.get("at_cm") or 0)
              by_cam = trig in ("camera", "camera_distance") and camera is not None and camera.seen_at is not None
              if (trig == "time" and now - start >= float(slowdown.get("at_s") or 0)) or by_dist or by_cam \
                      or (trig == "tape" and slow_seen.is_set()):
                  slowed_at = now
                  slowed_by = "camera" if by_cam and not by_dist else ("distance" if by_dist else trig)
          prev_t = now
          cur = float(slowdown["output"]) if slowed_at is not None and slowdown else value
          if slowed_at is not None and slowdown and stage_now == "approach":
              stage_now = "slow"
          st = (slowdown or {}).get("staged") if slowed_at is not None else None
          if st and bump_watch is not None:
              # 단계별: 앞바퀴가 다 내려와 수평으로 돌아오면 뒷바퀴 구간 출력으로
              if "front_level" in bump_watch.events and st.get("output_rear") is not None:
                  cur = float(st["output_rear"])
                  stage_now = "rear"
              elif st.get("output_front") is not None:
                  cur = float(st["output_front"])
                  stage_now = "front"
          if slowdown and slowdown.get("resume") and resume_at is None and slowed_at is not None:
              # 다시 가속: 기울기로 '다 넘음'을 알아챈 순간. 못 알아채도 감속 후 정한 시간이 지나면 가속(멈춘 채 남지 않게)
              ra = float(slowdown.get("resume_after_s") or 0)
              if passed_at is not None:
                  resume_at, resume_by = now, "tilt"
              elif ra > 0 and now - slowed_at >= ra:
                  resume_at, resume_by = now, "timer"
          if slowdown and resume_at is not None:
              # 원래 출력으로 0.4초에 걸쳐 다시 가속
              k = min(1.0, (now - resume_at) / 0.4)
              cur = cur + (value - cur) * k
              stage_now = "resume"
          if stage_now != stage_prev:
              stage_log.append([round(now - start, 3), stage_now, round(cur, 1)])
              stage_prev = stage_now
          out = 100.0 if kick_ms and not ramp_s and (now - start) * 1000 < kick_ms else cur
          k = 1.0
          if decel_at is not None:
              k = 1.0 - (now - decel_at) / max(0.01, decel_s)
              if k <= 0:
                  break
          elif ramp_s > 0:
              k = min(1.0, max(0.05, (now - start) / ramp_s))
          if guided is not None and guided.mode == "lost" and decel_at is None:
              k = min(k, 0.75)   # 테이프를 찾는 동안은 조금 천천히(방지턱을 이미 지난 뒤라 측정에는 영향 없음)
          cmd_mode, cmd_out = mode, out
          if k < 0.999:
              # 순차 가속·감속: 라이브러리 PWM이면 PWM 값에 비율을 곱해 그대로 라이브러리로(직접 듀티로 바꾸지 않음).
              # 직접 듀티 모드면 듀티에 비율을 곱합니다.
              cmd_out = max(0.0, out * k)
          if not await ctl.experiment_drive(cmd_mode, cmd_out, generation, direction, steer):
              raise Aborted()
          row = [round(now - start, 3), out, round(steer, 2), None if heading is None else round(heading, 2)]
          if guided is not None and guided.last:
              p = guided.last.get("position")
              tl = mpu6050_service.tilt_deg()
              row += [None if p is None else round(p, 2), guided.last.get("mode"), guided.last.get("ref_deg"), guided.last.get("y_est_mm"),
                      None if tl is None else round(max(abs(tl[0]), abs(tl[1])), 1)]
          log.append(row)
          cs = camera.scores[-1] if camera is not None and camera.scores else None
          auto_runner.live = {"t": round(now - start, 2), "output": round(out, 1), "mode": mode, "duty": round(effective_duty(mode, out * k), 1),
                              "base_output": value, "slowed": slowed_at is not None, "slowed_by": slowed_by,
                              "slowed_at": round(slowed_at - start, 2) if slowed_at is not None else None, "stage": stage_now,
                              "passed": passed_at is not None, "camera": [cs[1], cs[2]] if cs else None,
                              "camera_seen": camera is not None and camera.seen_at is not None,
                              "distance_cm": round(near, 1) if near is not None else None, "seconds": seconds}
          await asyncio.sleep(0.02 if line is not None else 0.05)  # 라인 추종은 50 Hz
    finally:
        if auto_runner.live is not None:
            auto_runner.live = {**auto_runner.live, "ended": True}
        if poller is not None:
            mark_seen.set()
            poller.cancel()
        if slow_poller is not None:
            slow_seen.set()
            slow_poller.cancel()
        if cross is not None:
            cross.stop()

    half = [row[2] for row in log if row[0] >= seconds / 2]
    return {"heading_max_deg": round(worst, 2) if last is not None else None,
            "heading_end_deg": round(last, 2) if last is not None else None,
            "line_lost": line_lost if (line is not None or guided is not None) else None,
            "drive_log": log[:400], "stopped_on_mark": stopped_on_mark, "elapsed_s": round(loop.time() - start, 3),
            "bump_passed_s": round(passed_at - start, 3) if passed_at is not None else None,
            "bump_events": dict(bump_watch.events) if bump_watch is not None else None,
            "steer_mean_late": round(sum(half) / len(half), 3) if half and controller is not None else None,
            "heading_flipped": bool(controller and controller.flipped),
            "slowed_at_s": round(slowed_at - start, 3) if slowed_at is not None else None,
            "slowed_by": slowed_by, "start_mono": start,
            "resumed_at_s": round(resume_at - start, 3) if resume_at is not None else None, "resumed_by": resume_by,
            "stage_log": stage_log,
            "camera_seen_s": round(camera.seen_at - start, 3) if camera is not None and camera.seen_at is not None else None,
            "stopped_by_wall": stopped_by_wall,
            "end_reason": "wall" if stopped_by_wall else "line_lost" if line_lost else "mark" if stopped_on_mark
                          else "bump_passed" if passed_at is not None and stop_on_pass else "cross" if decel_at is not None else "time",
            "cross_detected": decel_at is not None if cross is not None else None,
            "cross_detect_s": round(decel_at - start, 3) if decel_at is not None else None,
            "cross_tilt_blocked": getattr(cross, "blocked", None),
            "guided": guided.summary() if guided is not None else None,
            "steer_saturated": bool(controller and sum(1 for row in log if abs(row[2]) >= MAX_HEADING_CORRECTION_DEG - 0.01) > len(log) * 0.3)}


def effective_duty(mode: str, value: float) -> float:
    """실제 모터 듀티(%): 라이브러리 PWM은 라이브러리 계산 그대로 int(PWM/2) + 50, 직접 듀티는 그대로."""
    if value <= 0:
        return 0.0
    return float(min(100, int(min(100.0, value) / 2) + 50)) if mode == "pwm" else min(100.0, value)


def _tape_detector(settings: "ExperimentSettings") -> Callable[[Any], bool] | None:
    """출발선 테이프 감지 함수(그레이스케일 3개 중 하나라도 테이프 쪽으로 60% 이상). 라인 보정이 없으면 None."""
    from .line_follow import weights
    if not settings.line.floor or not settings.line.line:
        return None

    def seen(raw: Any) -> bool:
        w = weights(list(raw or []), settings.line)
        return bool(w) and max(w) >= 0.6
    return seen


def _learn_heading(direction: str, steer_mean: float | None, info: dict[str, Any] | None = None) -> float | None:
    """측정이 끝나면 후반부 평균 조향을 '직진에 필요한 기본 조향'으로 반쯤 반영합니다(최대 ±5°).

    조향이 한계에 오래 붙어 있었거나 방향을 뒤집은 회차는 정상 직진이 아니었으므로 배우지 않습니다.
    방향을 뒤집었으면 저장된 방향(heading_sign)을 바꾸고 학습값을 0으로 되돌립니다.
    """
    settings = store.load_settings()
    if info and info.get("heading_flipped"):
        settings.heading_sign = -settings.heading_sign  # type: ignore[assignment]
        settings.heading_ff_forward = settings.heading_ff_backward = 0.0
        store.save_settings(settings)
        return 0.0
    if steer_mean is None or not settings.heading_learn or (info and info.get("steer_saturated")):
        return None
    key = "heading_ff_forward" if direction == "forward" else "heading_ff_backward"
    new = max(-5.0, min(5.0, 0.5 * getattr(settings, key) + 0.5 * steer_mean))
    setattr(settings, key, round(new, 3))
    store.save_settings(settings)
    return new


def slowdown_plan(cond: ExperimentCondition, settings: ExperimentSettings, req: "SlowdownRequest", simulated: bool) -> dict[str, Any]:
    """목표 2 감속 계획: 관계식(선택된 식)과 '출력·질량 → 속도' 식으로 방지턱에서 쓸 출력을 정합니다.

    1) 지금 출력으로 들어가면 속도 v₀ = 속도식(출력₀, m), 예측 충격 Δa(v₀, m)
    2) 목표 충격 Δa* : 여유 방식 ratio면 기준(a_lim − 1), sigma면 기준 − 2×예측오차(교차검증 RMSE)
       → '기준 바로 밑(0.9999…)'이 아니라 '측정 오차를 감안해도 넘지 않을 값'을 목표로 함
    3) Δa(v, m) = Δa* 를 풀어 v_max(m), ratio면 v* = v_max × 여유율, sigma면 v* = v_max
    4) 단계별(staged): 앞바퀴 구간(FU·FD)과 뒷바퀴 구간(RU·RD)을 각각의 단계식으로 풀어 v_앞 = min(v_FU, v_FD),
       v_뒤 = min(v_RU, v_RD). 방지턱 전에는 v_앞의 출력, 앞바퀴가 다 내려오면(기울기 수평 복귀) v_뒤의 출력,
       다 넘으면(resume) 원래 출력으로 다시 가속
    5) 출력 = 속도식을 거꾸로 풀어 그 속도가 나오는 출력(최소 출력 아래로는 안 내림)
    """
    runs = runs_for_mode(simulated)
    bundle = analysis_bundle(runs, settings)
    bump = cond.bump_id
    cmp = bundle["models"].get(bump)
    model = None
    if cmp:
        mid = bundle["chosen_models"].get(bump)
        model = next((m for m in cmp["models"] if m["id"] == mid), None)
    if model is None:
        raise RuntimeError(f"방지턱 {bump}의 관계식이 아직 없습니다. 본 실험(여러 출력·질량)을 먼저 측정하세요.")
    crit = bundle["criteria"].get(bump) or {}
    a_lim = req.target_g or crit.get("a_limit_g")
    if not a_lim or a_lim <= 1:
        raise RuntimeError("허용 기준(g)이 없습니다. 분석 탭에서 기준을 고르거나 감속 설정에 직접 넣으세요.")
    m_g = cond.base_mass_g + cond.added_mass_g
    mk = m_g / 1000.0
    models = bundle["speed_models"]
    v0 = mf.speed_from_output(models, bump, cond.output_mode, cond.output_value, m_g)
    if v0 is None:
        raise RuntimeError("출력 → 속도 식이 없습니다. 같은 방지턱·출력 방식으로 출력을 2단계 이상 측정하세요.")
    err = model.get("loocv_rmse") or model.get("rmse") or 0.0
    d_target = (a_lim - 1.0) - (2.0 * err if req.margin == "sigma" else 0.0)
    if d_target <= 0:
        raise RuntimeError(f"예측 오차(±{err:.2f} g)가 허용 여유보다 커서 목표 충격을 정할 수 없습니다. 반복 측정을 늘려 식을 더 정확하게 하세요.")
    vmax = mf.solve_speed(model, d_target, mk)
    if vmax is None:
        raise RuntimeError("관계식으로 v_max를 풀지 못했습니다(기준이 너무 낮거나 식이 맞지 않음).")
    factor = req.safety if req.margin == "ratio" else 1.0
    target_v = vmax * factor

    def out_for(v: float) -> tuple[float | None, bool]:
        o = mf.output_from_speed(models, bump, cond.output_mode, v, m_g)
        if o is None:
            return None, False
        clipped = o < req.min_output
        return min(max(o, req.min_output), cond.output_value), clipped

    stages: dict[str, Any] | None = None
    if req.staged:
        em = (bundle.get("event_models") or {}).get(bump) or {}
        if not all(k in em for k in ("FU", "FD", "RU", "RD")):
            raise RuntimeError("단계별 식(앞·뒤 바퀴 오름·내림)이 아직 없습니다. 기울기로 네 순간이 찾아진 회차(분석 방식 '기울기 4단계')가 출력 2단계 이상 필요합니다.")
        vs = {k: mf.event_solve(em[k], d_target, mk) for k in ("FU", "FD", "RU", "RD")}
        if any(v is None for v in vs.values()):
            raise RuntimeError("단계별 식으로 속도를 풀지 못했습니다.")
        v_front = min(vs["FU"], vs["FD"]) * factor
        v_rear = min(vs["RU"], vs["RD"]) * factor
        of, cf = out_for(v_front)
        orr, cr = out_for(v_rear)
        stages = {"v_by_event": {k: round(v, 4) for k, v in vs.items()}, "v_front_mps": round(v_front, 4), "v_rear_mps": round(v_rear, 4),
                  "output_front": None if of is None else round(of, 1), "output_rear": None if orr is None else round(orr, 1),
                  "clipped": cf or cr,
                  "pred_by_event_g": {k: round(mf.event_predict(em[k], v_front if k in ("FU", "FD") else v_rear, mk), 4) for k in em}}
        target_v = v_front
    need = v0 > target_v
    out_slow, clipped = out_for(target_v) if need else (cond.output_value, False)
    if req.output is None and req.target_speed is not None:
        o = mf.output_from_speed(models, bump, cond.output_mode, req.target_speed, m_g)
        if o is None:
            raise RuntimeError("목표 속도를 출력으로 바꾸지 못했습니다(속도식 없음).")
        req = req.model_copy(update={"output": max(1.0, min(100.0, o))})
    manual = req.output is not None
    if manual:
        # 시험용으로 감속 출력을 직접 넣은 경우: 그 출력으로 줄이고, 목표 속도는 속도식으로 환산해 기록
        out_slow, clipped, need, stages = float(req.output), False, True, None
        target_v = mf.speed_from_output(models, bump, cond.output_mode, out_slow, m_g) or target_v
    if out_slow is None:
        raise RuntimeError("목표 속도에 맞는 출력을 계산하지 못했습니다.")
    at_cm = req.at_cm
    trigger = req.trigger
    if trigger in ("distance_auto", "camera_distance"):
        dists = sorted(r["distance_summary"]["distance_at_front_cm"] for r in runs
                       if r.get("condition", {}).get("bump_id") == bump and (r.get("distance_summary") or {}).get("distance_at_front_cm"))
        if dists:
            at_cm = round(dists[len(dists) // 2] + req.lead_cm, 1)
        elif trigger == "distance_auto":
            raise RuntimeError("'거리 자동'은 초음파를 켜고 측정한 회차가 있어야 합니다(앞바퀴 충격 때 벽까지 거리를 배움).")
        if trigger == "distance_auto":
            trigger = "distance"
    return {"trigger": trigger, "trigger_requested": req.trigger, "at_s": req.at_s, "at_cm": at_cm, "safety": req.safety,
            "margin": req.margin, "model_error_g": round(err, 4), "target_delta_g": round(d_target, 4),
            "a_limit_g": round(a_lim, 4), "model_id": model["id"], "model_formula": model.get("formula"),
            "v0_pred_mps": round(v0, 4), "pred_delta_v0_g": round(mf.predict(model, v0, mk), 4),
            "vmax_mps": round(vmax, 4), "target_v_mps": round(target_v, 4),
            "pred_delta_target_g": round(mf.predict(model, target_v, mk), 4),
            "needed": need or bool(stages), "output": round(out_slow, 1) if (need or stages) else None, "output_initial": cond.output_value,
            "output_clipped": clipped, "staged": stages, "resume": req.resume, "manual_output": manual,
            "target_speed_manual": req.target_speed, "resume_after_s": req.resume_after_s,
            "duty_initial": round(effective_duty(cond.output_mode, cond.output_value), 2),
            "duty_slow": round(effective_duty(cond.output_mode, out_slow), 2) if (need or stages) else None,
            "ratio": round(target_v / v0, 4) if v0 else None}


async def _measure_once(ctl, cond: ExperimentCondition, settings: ExperimentSettings, opts: dict[str, Any],
                        simulated: bool) -> dict[str, Any]:
    """한 번의 측정: 정지 기록 → 직진(기록) → 정지 후 기록 → 분석·저장."""
    run = new_run(cond, settings, simulated=simulated, source="auto", pre_roll_s=opts["pre_roll"], run_seconds=opts["run_seconds"])
    auto_runner.run_id = run["id"]
    run["battery_v"] = await asyncio.to_thread(ctl.adapter.read_battery) if ctl.adapter.connected else None
    run["steering_source"] = opts["steering_source"]
    run["kick_ms"] = opts["kick_ms"]
    try:
        run["applied"] = applied_snapshot(opts)   # 이 회차에 실제로 적용된 설정(서버 재시작·학습으로 바뀌었는지 추적)
    except Exception:
        pass
    if opts.get("slowdown_plan"):
        run["slowdown"] = dict(opts["slowdown_plan"])
    logger: DistanceLogger | None = None
    if not simulated:
        from .camera import camera_service
        camera_service.paused = True
        status = await asyncio.to_thread(mpu6050_service.start_recording)
        if status.get("error") or not status.get("recording"):
            raise RuntimeError(status.get("error") or "MPU-6050 기록을 시작하지 못했습니다.")
        if opts.get("ultrasonic") or opts.get("wall_stop_cm") or (opts.get("slowdown_plan") or {}).get("trigger") in ("distance", "camera_distance"):
            logger = DistanceLogger(ctl.adapter.read_distance, settings.ultrasonic_hz)
            logger.start()
    gen = ctl.stop_generation
    aborted = False
    drive_info: dict[str, Any] = {}
    try:
        auto_runner._phase("pre_roll", opts["pre_roll"], f"{auto_runner.cycle}/{auto_runner.cycles}회 · 정지 상태 기록 중 (차량을 움직이지 마세요)")
        await _wait(ctl, opts["pre_roll"], gen)
        # 방향은 항상 측정해 직진도를 기록하고, 보정은 설정이 켜져 있을 때만 적용합니다.
        # 코스(정지선) 모드는 출발 전에 영점을 한 번 잡았으므로 방향만 0으로(멈춘 직후 흔들림이 영점에 섞이지 않게).
        track = not simulated and (mpu6050_service.heading_zero() if opts.get("keep_bias") else mpu6050_service.heading_reset())
        hold = bool(opts["steering_source"] == "gyro" and track)
        line = LineTracker(settings.line) if opts["steering_source"] == "line" and settings.line.floor and settings.line.line else None
        guided = None
        if line is not None and track and cond.direction != "backward":
            # 유도선 + 자이로: 방지턱 위에서는 테이프 대신 자이로로 방향을 유지하고, 벗어나면 정해진 각도로 되돌아감
            # 속도 추정(테이프를 놓쳤을 때 옆으로 밀린 양 계산용): 같은 반복의 지난 회차 실측, 없으면 0.4 m/s
            last_v = ((auto_runner.last_result or {}).get("analysis") or {}).get("speed_mps") if auto_runner.cycle > 1 else None
            if not last_v:
                # 첫 회차: 같은 방지턱·출력으로 예전에 잰 실제 속도(중앙값)를 씀(테이프를 놓쳤을 때 옆으로 밀린 거리 추정용)
                prev = sorted(r["analysis"]["speed_mps"] for r in runs_for_mode(simulated)
                              if not r.get("excluded") and (r.get("analysis") or {}).get("speed_mps")
                              and r["condition"].get("bump_id") == cond.bump_id and r["condition"].get("output_mode") == cond.output_mode
                              and abs(float(r["condition"].get("output_value") or 0) - cond.output_value) < 0.5)[-15:]
                if prev:
                    last_v = prev[len(prev) // 2]
            fresh = store.load_settings()   # 직전 회차에서 배운 직진 조향을 바로 씀
            guided = GuidedSteer(fresh.line, polarity=fresh.heading_sign, speed_mps=float(last_v or 0.4),
                                 ref_deg=opts.pop("ref_offset", None))
            line = None
        stop_line = opts.get("end_mode") == "stop_line" and not simulated
        lcam = opts.get("lcam")
        own_cam = False
        if lcam is None and settings.line.camera_assist and not simulated and (guided is not None or stop_line):
            own_cam = True
            # 카메라 보조: 앞쪽 바닥의 검은 테이프(유도선 위치, 가로선)를 함께 봄. 카메라를 아래로 숙임.
            from .camera import camera_service
            try:
                ctl.adapter.set_camera_view(0, int(round(settings.linecam.tilt_deg)))
            except Exception:
                pass
            lcam = LineCam(lambda: camera_service.capture_array(step=4), settings.linecam).start()
        if lcam is not None:
            lcam.reset_cross()
            if guided is not None:
                guided.cam = lcam
        cross = None
        if stop_line:
            from .course import CrossWatcher
            cross = CrossWatcher(ctl, settings.line, opts["cross_threshold"], 2.0, cam=lcam,
                                 cam_wait_s=settings.linecam.cross_wait_s)
        ramp_s = opts["accel_s"] if opts.get("accel_mode") == "ramp" else 0.0
        if opts["steering_source"] == "line" and line is None and guided is None:
            raise RuntimeError("라인 추종 탭에서 바닥·테이프 센서 보정을 먼저 하세요.")
        run["heading_hold"] = hold
        auto_runner._phase("driving", None if stop_line else opts["run_seconds"],
                           f"{auto_runner.cycle}/{auto_runner.cycles}회 · {opts.get('leg_label') or ''}{'후진' if cond.direction == 'backward' else '직진'} 측정 중 · {cond.output_mode.upper()} {cond.output_value:g}"
                           f"{' · 정지선까지' if stop_line else ''}{' · 유도선+자이로' if guided else ' · 직진 보정' if hold else ''}")
        try:
            mdir = cond.direction
            if mdir == "backward":
                line = None  # 테이프 센서는 차 앞쪽에 있어 후진 측정에서는 자이로로 방향을 유지합니다.
                hold = bool(opts["steering_source"] in ("gyro", "line") and track)
                run["heading_hold"] = hold
            ff_key = "heading_ff_forward" if mdir == "forward" else "heading_ff_backward"
            plan = opts.get("slowdown_plan") or {}
            plan_staged = bool(plan.get("staged"))
            cam = None
            if plan.get("trigger") in ("camera", "camera_distance") and not simulated:
                from .camera import camera_service
                vs = store.load_settings().vision
                if not vis.ready(vs):
                    raise RuntimeError("카메라 방지턱 감지는 '이론·예측·제어 → 감속 제어'에서 카메라 보정(바닥 보정 또는 방지턱 학습)을 먼저 하세요.")
                if vs.tilt_deg is not None:
                    try:
                        ctl.adapter.set_camera_view(0, int(round(vs.tilt_deg)))
                    except Exception:
                        pass
                cam = BumpCamera(camera_service.capture_array, vs)
                cam.start()
            # 감속 회차: 방지턱 앞에서 출력만 줄이고(멈추지 않음), 다 넘으면(resume) 원래 출력으로 다시 가속.
            # 주행 시간은 감속 제어 화면에서 정한 값(run_seconds)을 그대로 씁니다.
            sp = plan if plan.get("needed") else None
            drive_s = opts["leg_timeout"] if stop_line else opts["run_seconds"]
            if sp:
                run["slowdown"]["drive_s"] = round(drive_s, 2)
            drive_info = await _drive_for(ctl, cond.output_mode, cond.output_value, drive_s, mdir, gen, hold,
                                          settings.heading_gain, track, line, opts["kick_ms"],
                                          ki=settings.heading_ki, ff=getattr(store.load_settings(), ff_key) if hold else 0.0,
                                          polarity=store.load_settings().heading_sign,
                                          bump_watch=BumpPassWatcher() if (((settings.stop_after_bump and not stop_line) or plan_staged or sp) and track) else None,
                                          stop_on_pass=settings.stop_after_bump and not stop_line and not sp,
                                          ramp_s=ramp_s, decel_s=opts.get("decel_s") or 0.3, cross=cross, guided=guided,
                                          min_s=0.3 if stop_line else 0.0,
                                          camera=cam,
                                          after_bump_s=settings.after_bump_s,
                                          slowdown=sp,
                                          distance=logger, wall_stop_cm=opts.get("wall_stop_cm") or 0.0,
                                          slow_tape=_tape_detector(settings) if not simulated else None)
            if cam is not None:
                cam.stop()
                drive_info["camera_scores"] = cam.scores[-80:]
            if lcam is not None:
                if own_cam:
                    lcam.stop()
                drive_info["camera_line"] = {"frames": lcam.frames, "last": lcam.latest,
                                             "stop_by_camera": bool(cross is not None and cross.by_camera)}
            if guided is not None and settings.line.learn_steer and not drive_info.get("line_lost"):
                # 직진 조향 학습: 유도선 가운데로 곧게 가던 동안의 평균 조향을 다음 회차부터 기본 조향으로(반씩 반영)
                gm = guided.summary().get("steer_calm_mean")
                if gm is not None:
                    st = store.load_settings()
                    st.line.steer_ff_deg = round(max(-10.0, min(10.0, 0.5 * st.line.steer_ff_deg + 0.5 * gm)), 2)
                    store.save_settings(st)
                    drive_info["steer_ff_learned"] = st.line.steer_ff_deg
            auto_runner.last_forward_s = drive_info.get("elapsed_s")
            if stop_line or ramp_s or opts.get("leg_label"):
                run["course"] = {"leg": opts.get("leg_label"), "end_mode": opts.get("end_mode"), "accel": opts.get("accel_mode"),
                                 "accel_s": ramp_s or None, "decel_s": opts.get("decel_s") if stop_line else None,
                                 "detected": drive_info.get("cross_detected"), "detect_s": drive_info.get("cross_detect_s")}
            if line is not None and line.flipped:
                st = store.load_settings()
                st.line.steer_sign = line.sign
                store.save_settings(st)
            if hold and not drive_info.get("line_lost"):
                drive_info["heading_ff_learned"] = _learn_heading(mdir, drive_info.get("steer_mean_late"), drive_info)
        finally:
            await ctl.stop(reason="experiment_auto_run_end")
        gen = ctl.stop_generation
        auto_runner._phase("tail", opts["tail"], f"{auto_runner.cycle}/{auto_runner.cycles}회 · 정지 후 진동 기록 중")
        await _wait(ctl, opts["tail"], gen)
    except Aborted:
        aborted = True
        run["aborted"] = True
    auto_runner._phase("analyzing", None, f"{auto_runner.cycle}/{auto_runner.cycles}회 · 분석·저장 중")
    if simulated:
        plan = opts.get("slowdown_plan") or {}
        columns = ia.synthesize_run(
            output_mode=cond.output_mode, output_value=plan["output"] if plan.get("needed") and plan.get("output") else cond.output_value,
            mass_g=cond.base_mass_g + cond.added_mass_g, base_mass_g=cond.base_mass_g,
            wheelbase_mm=settings.wheelbase_mm or 95.0, bump_radius=run["bump"].get("radius_mm"),
            wheel_radius_mm=settings.wheel_radius_mm or 33.0, pre_roll_s=opts["pre_roll"])
        meta = {"started_at": run["created_at"], "count": len(columns["t_s"]), "sample_rate_hz": 1000.0,
                "vertical_axis": "z", "vertical_sign": 1.0, "calibration_stability_g": None}
    else:
        await asyncio.to_thread(mpu6050_service.stop_recording)
        if logger is not None:
            logger.stop()
            t0 = getattr(mpu6050_service, "_record_started_mono", None)
            if t0 is not None:
                run["distance"] = {"t_s": [round(t - t0, 4) for t, _ in logger.samples], "cm": [d for _, d in logger.samples],
                                   "errors": logger.errors}
        camera_service.paused = False
        meta = await asyncio.to_thread(mpu6050_service.recording_columns)
        columns = meta.pop("columns")
        if not columns["t_s"]:
            raise RuntimeError("센서 표본이 없어 저장하지 않았습니다.")
    # 주행 시작이 기록 시작으로부터 몇 초 뒤였는지(출발 전 기록 + 조향 서보 정렬 시간 등). 예전에는 '출발 전 기록 시간'만
    # 더해서, 서보 정렬(약 0.4초)이 들어간 뒤로 감속 표시가 실제보다 0.4초 이르게 찍혔습니다.
    rec0 = None if simulated else getattr(mpu6050_service, "_record_started_mono", None)
    t_off = round(drive_info["start_mono"] - rec0, 3) if rec0 is not None and drive_info.get("start_mono") else float(opts["pre_roll"])
    run["drive_t0_s"] = t_off
    if run.get("slowdown") and drive_info.get("slowed_at_s") is not None:
        run["slowdown"]["slowed_at_s"] = round(drive_info["slowed_at_s"] + t_off, 3)  # 기록 시작 기준
        run["slowdown"]["slowed_by"] = drive_info.get("slowed_by")
    if run.get("slowdown") and drive_info.get("resumed_at_s") is not None:
        run["slowdown"]["resumed_at_s"] = round(drive_info["resumed_at_s"] + t_off, 3)
        run["slowdown"]["resumed_by"] = drive_info.get("resumed_by")
    if run.get("slowdown") and drive_info.get("stage_log"):
        run["slowdown"]["stage_log"] = [[round(t + t_off, 3), st, o] for t, st, o in drive_info["stage_log"]]
    run.update({k: v for k, v in drive_info.items() if k not in ("slowed_at_s", "stage_log", "resumed_at_s", "resumed_by", "start_mono")})
    run["data_source"] = "synthetic" if simulated else "mpu6050"
    run = await asyncio.to_thread(finalize_run, run, columns, meta, settings)
    if simulated and opts.get("ultrasonic"):
        a = run.get("analysis") or {}
        ts, ds = dist.synthesize(a.get("front_t_s"), a.get("rear_t_s"), a.get("speed_mps"), opts["pre_roll"],
                                 columns["t_s"][-1] if columns["t_s"] else 3.0, seed=hash(run["id"]) % 1000)
        run["distance"] = {"t_s": ts, "cm": ds, "errors": 0}
    if run.get("distance"):
        a = run.get("analysis") or {}
        run["distance_summary"] = _distance_summary(run, a)
        store.put(run)
    if run.get("slowdown"):
        a = run.get("analysis") or {}
        sd = run["slowdown"]
        if a.get("peak_g") is not None:
            sd["measured_peak_g"] = a["peak_g"]
            sd["pass"] = bool(a["peak_g"] <= sd["a_limit_g"])
        sd["measured_speed_mps"] = a.get("speed_mps")
        store.put(run)
    if drive_info.get("line_lost"):
        run["analysis"].setdefault("flags", []).append("측정 중 테이프를 놓쳐 일찍 멈췄습니다.")
        store.put(run)
    if drive_info.get("cross_detected") is False and not drive_info.get("line_lost") and not aborted:
        run["analysis"].setdefault("flags", []).append("정지선을 찾지 못하고 최대 시간에서 멈췄습니다.")
        store.put(run)
    auto_runner.last_drive = drive_info
    summary = run_summary(run)
    auto_runner.last_result = summary
    auto_runner.completed.append(run["id"])
    if aborted:
        raise Aborted()
    return summary


def _line_dir() -> float:
    """방금 끝난 측정 구간에서 알아낸 유도선 방향(그 구간 자이로 각, 출발 때 0°)."""
    g = (getattr(auto_runner, "last_drive", None) or {}).get("guided") or {}
    return float(g.get("ref_deg") or 0.0)


def _next_ref_offset(target: float) -> float | None:
    """다음 구간 출발 때(방향을 0으로 돌리기 직전) 차가 유도선 방향에서 얼마나 틀어져 있는지 → 다음 구간의 유도선 방향."""
    h = mpu6050_service.heading_deg()
    return None if h is None else round((target - h + 180.0) % 360.0 - 180.0, 2)


async def _turn_around(ctl, creq: Any, line: LineSettings, cycle: int, cycles: int, cam: Any = None) -> None:
    """정지선(또는 출발선) 위에서 제자리 180° 회전 → 유도선 찾기 → 가로 테이프가 센서 밑에 올 때까지 후진."""
    from . import course as co
    co.runner.gen = ctl.stop_generation
    co.runner.target_deg = _line_dir()  # 측정 구간에서 알아낸 유도선 방향(회전 뒤 ±180°를 이 방향에 맞춤)
    await _wait(ctl, 0.25, ctl.stop_generation)
    co.runner.gen = ctl.stop_generation
    auto_runner._phase("turning", None, f"{cycle}/{cycles}회 · 제자리 180° 회전 (출력 {creq.spin_output:g})")
    turned = await co._spin(ctl, 0, creq, 180.0, creq.spin_direction)
    if turned is not None:
        # 멈춘 뒤 더 돈 만큼(관성)을 배워 다음 회전부터 그만큼 일찍 끔
        creq.spin_lead_deg = max(0.0, min(60.0, creq.spin_lead_deg + 0.6 * (turned - 180.0)))
        st = store.load_settings()
        st.course_spin_lead_deg = round(creq.spin_lead_deg, 1)
        store.save_settings(st)
    auto_runner.message = f"{cycle}/{cycles}회 · {turned or 0:.0f}° 회전 → 유도선 찾기"
    line_dir = co.runner.target_deg
    found, off = await co._find_guide(ctl, 0, creq, line, cam)
    if await co._on_cross(ctl, creq, line):
        return
    # 뒷축 근처를 중심으로 돌았으므로 가로 테이프는 대개 센서 뒤쪽(차 길이만큼)에 있습니다.
    # 유도선이 옆에 있으면, 후진하는 동안 코를 반대쪽으로 조금 틀어 차가 옆으로 유도선 쪽으로 옮겨 가게 합니다
    # (왼쪽으로 옮기려면 코를 오른쪽으로: 뒤로 가면서 왼쪽으로 감). 약 27 cm 후진하는 동안 최대 7 cm.
    tilt_deg = 0.0
    if off is not None and abs(off) > 0.008:
        tilt_deg = max(-15.0, min(15.0, math.degrees(math.asin(max(-0.95, min(0.95, off / 0.27))))))
        co.runner.target_deg = line_dir - tilt_deg
        await co._face_target(ctl, creq, tol=3.0)
    auto_runner._phase("aligning", None, f"{cycle}/{cycles}회 · 가로 테이프가 센서 밑에 올 때까지 후진"
                       + (f" (유도선이 {'왼' if off and off > 0 else '오른'}쪽 {abs(off or 0) * 100:.0f} cm → 비스듬히)" if tilt_deg else ""))
    duty_req = creq.model_copy(update={"condition": creq.condition.model_copy(update={"output_mode": "duty"})})
    pol = store.load_settings().heading_sign
    duty = min(100.0, creq.creep_duty * 1.25)
    # 뒤로는 '센서 ~ 뒷축 거리의 2배 + 여유'만큼만 찾고(약 1.4초), 없으면(정지선을 많이 지나쳐 멈췄던 경우) 앞으로 찾습니다.
    back = await co._drive_until_cross(ctl, 0, duty_req, line, "backward", duty, 0.15, 0.12, 1.4, 0.0, "gyro", polarity=pol, skip=0)
    first = "forward"
    if not back["detected"]:
        auto_runner.message = f"{cycle}/{cycles}회 · 뒤에 없어 앞으로 가로 테이프를 찾습니다"
        fwd = await co._drive_until_cross(ctl, 0, duty_req, line, "forward", duty, 0.15, 0.12, 3.0, 0.0, "gyro", polarity=pol, skip=0)
        first = "backward" if fwd["detected"] else "forward"
    if tilt_deg:
        co.runner.target_deg = line_dir          # 가로선에서 다시 유도선 방향으로 곧게
        await co._face_target(ctl, creq, tol=3.0)
    # 감속하며 조금 지나쳤을 수 있으므로 반대로 천천히 맞춤
    if not await co._align_on_cross(ctl, creq, line, first, 2.0):
        raise RuntimeError("회전 뒤 가로 테이프를 다시 찾지 못했습니다(회전하며 많이 밀렸거나 유도선에서 벗어남).")


async def _auto_run(request: AutoRunRequest, settings: ExperimentSettings) -> None:
    ctl = control()
    simulated = ctl.adapter.mode != "hardware"
    cond = request.condition
    pick = lambda value, default: default if value is None else value  # noqa: E731
    opts = {
        "pre_roll": pick(request.pre_roll_s, settings.pre_roll_s),
        "run_seconds": pick(request.run_seconds, settings.run_seconds),
        "tail": pick(request.tail_s, settings.tail_s),
        "repeats": pick(request.repeats, settings.repeats),
        "return_enabled": pick(request.return_enabled, settings.return_enabled),
        "return_output": pick(request.return_output, settings.return_output) or cond.output_value,
        "return_scale": pick(request.return_scale, settings.return_scale),
        "return_stop_on_tape": pick(request.return_stop_on_tape, settings.return_stop_on_tape),
        "rest": pick(request.rest_s, settings.rest_s),
        "heading_hold": pick(request.heading_hold, settings.heading_hold),
        "kick_ms": pick(request.kick_ms, settings.kick_ms),
        "ultrasonic": pick(request.ultrasonic, settings.ultrasonic_log),
        "wall_stop_cm": pick(request.wall_stop_cm, settings.wall_stop_cm),
    }
    source = request.steering_source or (settings.steering_source if request.heading_hold is None else ("gyro" if request.heading_hold else "none"))
    opts["steering_source"] = source
    opts["heading_hold"] = source == "gyro"
    # 끝내기·복귀 방식
    rm = request.return_mode
    if rm is None:
        if request.return_enabled is not None:
            rm = "reverse" if request.return_enabled else "none"
        else:
            rm = settings.return_mode or ("reverse" if settings.return_enabled else "none")
    end_mode = pick(request.end_mode, settings.end_mode)
    if rm == "uturn":
        end_mode = "stop_line"  # 회전 복귀는 가로 테이프로 위치를 맞추므로 정지선 모드가 필요
    if simulated:
        end_mode = "time"
        rm = "reverse" if rm == "uturn" else rm
    opts.update({
        "end_mode": end_mode, "return_mode": rm, "return_enabled": rm != "none",
        "accel_mode": pick(request.accel_mode, settings.accel_mode), "accel_s": pick(request.accel_s, settings.accel_s),
        "decel_s": pick(request.decel_s, settings.decel_s), "leg_timeout": pick(request.leg_timeout_s, settings.leg_timeout_s),
        "cross_threshold": settings.cross_threshold, "keep_bias": end_mode == "stop_line",
    })
    stop_line = end_mode == "stop_line"
    creq = None
    line = settings.line
    if stop_line:
        from . import course as co
        if not line.floor or not line.line:
            auto_runner._phase("error", None, "정지선 모드는 라인 추종 탭에서 바닥·테이프 센서 보정을 먼저 해야 합니다.")
            return
        bad = calibration_check(line)
        if any("범위" in m or "본 적이 없" in m for m in bad):
            auto_runner._phase("error", None, "바닥 센서 보정이 잘못되었습니다: " + bad[0])
            return
        creq = co.CourseRequest(
            condition=cond, decel_s=opts["decel_s"], creep_duty=pick(request.creep_duty, settings.creep_duty),
            spin_output=pick(request.spin_output, settings.spin_output), spin_direction=pick(request.spin_direction, settings.spin_direction),
            spin_lead_deg=settings.course_spin_lead_deg, cross_threshold=settings.cross_threshold,
            leg_timeout_s=opts["leg_timeout"], min_leg_s=0.3, steering=source if source != "line" else "gyro")
        rev_out = request.return_output or settings.return_output or min(100.0, cond.output_value * 1.3)
    auto_runner.cycles = opts["repeats"]
    auto_runner.completed = []
    try:
        if request.slowdown is not None:
            # 감속 제어 시행은 관계식을 만드는 데 쓰지 않고 '검증'으로 저장합니다(관계식 → 제어 → 실측 비교).
            opts["slowdown_plan"] = slowdown_plan(cond, settings, request.slowdown, simulated)
            cond = cond.model_copy(update={"purpose": "validation", "note": (cond.note + " · " if cond.note else "") + "감속 제어"})
        if stop_line and settings.line.camera_assist:
            from .camera import camera_service
            try:
                ctl.adapter.set_camera_view(0, int(round(settings.linecam.tilt_deg)))
            except Exception:
                pass
            opts["lcam"] = LineCam(lambda: camera_service.capture_array(step=4), settings.linecam).start()
        if stop_line:
            # 출발선 위에서 자이로 영점을 한 번 잡고(이후에는 방향만 0으로), 센서가 출발선 위에 오게 맞춥니다.
            co.runner.gen = ctl.stop_generation
            auto_runner._phase("aligning", None, "출발 준비 · 자이로 영점, 출발선 위치 확인")
            mpu6050_service.heading_stop()
            await _wait(ctl, 0.6, ctl.stop_generation)
            mpu6050_service.heading_reset()
            co.runner.target_deg = 0.0
            if not await co._on_cross(ctl, creq, line):
                auto_runner.message = "출발선이 센서 밑에 없어 천천히 후진해 찾습니다."
                if not await co._align_on_cross(ctl, creq, line, "backward", 2.5):
                    raise RuntimeError("출발선을 찾지 못했습니다. 바닥 센서가 출발선(가로 테이프) 위에 오도록 차를 놓으세요.")
        delay = float(request.start_delay_s or 0)
        if delay > 0:
            # 출발 전 대기: 차에서 손을 떼고 영상 촬영 준비할 시간
            end_wait, wgen = time.monotonic() + delay, ctl.stop_generation
            while (left := end_wait - time.monotonic()) > 0:
                auto_runner._phase("rest", left, f"출발 대기 {left:.0f}초 · 차에서 손을 떼세요")
                await _wait(ctl, min(0.5, left), wgen)
        for i in range(opts["repeats"]):
            auto_runner.cycle = i + 1
            trial_cond = cond.model_copy(update={"trial": cond.trial + i})
            label = (("A→B " if i % 2 == 0 else "B→A ") if rm == "uturn" else "")
            opts["leg_label"] = label
            if stop_line:
                co.runner.gen = ctl.stop_generation
                if not await co._on_cross(ctl, creq, line):
                    auto_runner._phase("aligning", None, f"{i + 1}/{opts['repeats']}회 · 가로 테이프 위로 위치 맞춤")
                    if not await co._align_on_cross(ctl, creq, line, "backward", 1.5):
                        raise RuntimeError("출발 위치(가로 테이프 위)에 차를 맞추지 못했습니다.")
            await _measure_once(ctl, trial_cond, settings, opts, simulated)
            last_cycle = i == opts["repeats"] - 1
            drv = getattr(auto_runner, "last_drive", {}) or {}
            if stop_line and drv.get("cross_detected") is not True:
                raise RuntimeError("측정 주행 중 유도선을 놓쳐 멈췄습니다(정지선 전)." if drv.get("line_lost")
                                   else f"정지선을 못 보고 최대 {opts['leg_timeout']:g}초에서 멈췄습니다.")
            if stop_line and rm == "reverse":
                co.runner.gen = ctl.stop_generation
                co.runner.target_deg = _line_dir()
                await _wait(ctl, 0.3, ctl.stop_generation)
                co.runner.gen = ctl.stop_generation
                # 순차 감속으로 정지선을 조금 지나쳐 멈췄으면, 후진하며 그 정지선을 한 번 더 지나므로 하나는 건너뜀
                skip = 0 if await co._on_cross(ctl, creq, line) else 1
                auto_runner._phase("returning", None, f"{i + 1}/{opts['repeats']}회 · 출발선까지 후진 (출력 {rev_out:g}, 방지턱을 뒤로 넘음)")
                back = await co._drive_until_cross(ctl, 0, creq, line, "backward", rev_out, 0.3, max(0.1, opts["decel_s"] * 0.6),
                                                   opts["leg_timeout"] * 1.5, 0.3, "gyro" if source != "none" else "none",
                                                   polarity=store.load_settings().heading_sign, skip=skip)
                if not back["detected"]:
                    raise RuntimeError(f"후진 중 출발선을 찾지 못했습니다(후진 출력을 올리거나, 방지턱에서 멈췄는지 확인). "
                                       f"[{back['elapsed_s']:.1f}초 후진, 건너뛸 선 {skip}개, 기울어 무시한 읽기 {back.get('tilt_blocked', 0)}]")
                auto_runner._phase("aligning", None, f"{i + 1}/{opts['repeats']}회 · 출발선 위치 맞춤")
                if not await co._align_on_cross(ctl, creq, line, "forward", 2.0):
                    raise RuntimeError("출발선 위에 차를 맞추지 못했습니다.")
                opts["ref_offset"] = _next_ref_offset(co.runner.target_deg)
            elif stop_line and rm == "uturn":
                await _turn_around(ctl, creq, line, i + 1, opts["repeats"], opts.get("lcam"))
                opts["ref_offset"] = _next_ref_offset(co.runner.target_deg)
            elif opts["return_enabled"]:
                gen = ctl.stop_generation
                auto_runner._phase("returning", opts["run_seconds"] * opts["return_scale"] + 0.3,
                                   f"{auto_runner.cycle}/{auto_runner.cycles}회 · 출발선으로 {'전진' if cond.direction == 'backward' else '후진'} 복귀 중")
                await _wait(ctl, 0.3, gen)
                try:
                    # 복귀(후진)는 테이프 센서가 앞쪽에 있어 라인 대신 자이로로 방향을 유지합니다.
                    hold = bool(opts["steering_source"] in ("gyro", "line") and not simulated and mpu6050_service.heading_deg() is not None)
                    # 방지턱을 넘고 일찍 멈췄으면 실제로 달린 시간만큼만 되돌아갑니다(속도마다 거리가 달라도 출발선 근처로).
                    fwd = getattr(auto_runner, "last_forward_s", None) or opts["run_seconds"]
                    planned = fwd * opts["return_scale"]
                    tape = _tape_detector(settings) if opts["return_stop_on_tape"] and not simulated else None
                    rdir = "forward" if cond.direction == "backward" else "backward"
                    back = await _drive_for(ctl, cond.output_mode, opts["return_output"], planned * (1.6 if tape else 1.0),
                                            rdir, gen, hold, settings.heading_gain,
                                            ki=settings.heading_ki,
                                            ff=getattr(store.load_settings(), "heading_ff_" + rdir) if hold else 0.0,
                                            polarity=store.load_settings().heading_sign,
                                            stop_when=tape, min_s=planned * 0.4)
                    if tape is not None:
                        auto_runner.message = (f"{auto_runner.cycle}/{auto_runner.cycles}회 · 출발선 테이프에서 정지 ({back['elapsed_s']:.2f} s)"
                                               if back.get("stopped_on_mark") else
                                               f"{auto_runner.cycle}/{auto_runner.cycles}회 · 출발선 테이프를 못 찾아 최대 시간에서 정지")
                    if hold:
                        _learn_heading(rdir, back.get("steer_mean_late"), back)
                finally:
                    await ctl.stop(reason="experiment_return_end")
            if not last_cycle:
                gen = ctl.stop_generation
                auto_runner._phase("rest", opts["rest"], f"{auto_runner.cycle}/{auto_runner.cycles}회 완료 · 진동이 가라앉을 때까지 대기")
                await _wait(ctl, opts["rest"], gen)
        n = len(auto_runner.completed)
        auto_runner._phase("done", None, f"반복 측정 완료 · {n}회 저장" if n > 1 else "측정·저장 완료")
        record_event("experiment", "Auto run finished", details={"runs": auto_runner.completed, "simulated": simulated})
    except Aborted:
        n = len(auto_runner.completed)
        auto_runner._phase("aborted", None, f"정지 명령으로 중단됨 · 완료된 {n}회는 저장했고, 측정 중이던 시행은 '제외'로 저장했습니다.")
    except Exception as exc:
        try:
            await ctl.stop(reason="experiment_auto_run_error")
        except Exception:
            pass
        if not simulated:
            await asyncio.to_thread(mpu6050_service.stop_recording)
        import traceback
        tb = traceback.extract_tb(exc.__traceback__)
        where = f" [{tb[-1].filename.rsplit('/', 1)[-1]}:{tb[-1].lineno}]" if tb else ""
        auto_runner._phase("error", None, f"자동 측정 실패: {exc}{where}")
        record_event("experiment", "Auto run traceback", level="ERROR", details={"trace": traceback.format_exc()[-2000:]})
        record_event("experiment", "Auto run failed", level="ERROR", details={"error": str(exc)})
    finally:
        mpu6050_service.heading_stop()
        if opts.get("lcam") is not None:
            opts["lcam"].stop()
        from .camera import camera_service
        camera_service.paused = False


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
            "max_output": EXPERIMENT_MAX_OUTPUT, "a_limit_effective_g": effective_a_limit(s),
            "a_limit_presets": [{"id": k, **v} for k, v in A_LIMIT_PRESETS.items()]}


@router.put("/settings")
async def put_settings(settings: ExperimentSettings):
    if len({b.id for b in settings.bumps}) != len(settings.bumps):
        raise HTTPException(422, "방지턱 ID가 중복되었습니다.")
    # 학습된 직진 보정값은 요청에 명시했을 때만 바꿉니다(다른 설정을 저장하다 학습값이 지워지지 않도록).
    current = store.load_settings()
    for key in ("heading_ff_forward", "heading_ff_backward"):
        if key not in settings.model_fields_set:
            setattr(settings, key, getattr(current, key))
    store.save_settings(settings)
    return await get_settings()


@router.get("/runs")
async def list_runs():
    ctl = control()
    simulated = ctl.adapter.mode != "hardware"
    settings = store.load_settings()
    runs = runs_for_mode(simulated)
    # 평지 오검출 판정(bump_detected)이 생기기 전에 분석된 시행은 저장된 원자료로 한 번 다시 분석합니다.
    stale = [r for r in runs if "bump_detected" not in (r.get("analysis") or {}) or (r.get("analysis") or {}).get("version", 0) < ia.ANALYSIS_VERSION
             or ((r.get("analysis") or {}).get("params") or {}).get("filter_hz") and not r["analysis"]["params"].get("peak_keep")]
    for run in stale:
        try:
            cols = await asyncio.to_thread(store.read_columns, run["id"])
        except HTTPException:
            continue
        run["analysis"] = run_analysis(run, cols, settings)
        store.put(run)
    runs = ia.harmonize_pairs(runs)
    return {"simulated": simulated, "runs": [run_summary(r) for r in runs], **analysis_bundle(runs, settings)}


@router.get("/series")
async def runs_series(ids: str, before: float = 0.15, after: float = 0.4, points: int = 400, raw: bool = False):
    """여러 시행을 앞바퀴 피크(없으면 최대 피크) 시각에 맞춰 겹쳐 보기 위한 시계열.

    한 회씩 보기와 같은 기준을 쓰도록 같은 조건 회차 맞춤(harmonize)과 진동 거르기 설정을 똑같이 적용합니다.
    ``before``/``after``를 크게 주면(예: 60) 기록 전체를 돌려줍니다. ``raw=1``이면 거르기 전 값도 함께 줍니다.
    """
    out = []
    hz = store.load_settings().filter_hz
    want = [x for x in ids.split(",") if x][:200]
    if not want:
        return {"series": [], "filter_hz": hz}
    all_runs = store.list_runs()
    by_id = {r["id"]: r for r in ia.harmonize_pairs(all_runs)}
    for run_id in want:
        run = by_id.get(run_id)
        if run is None:
            continue
        try:
            cols = await asyncio.to_thread(store.read_columns, run_id)
        except HTTPException:
            continue
        a = run.get("analysis") or {}
        t0 = a.get("front_t_s") if a.get("front_t_s") is not None else a.get("peak_t_s")
        if t0 is None:
            continue
        cols, _ = ia.remove_glitches(cols)
        g = ia.peak_preserving_filter(cols["t_s"], cols["vertical_g"], hz) if hz else cols["vertical_g"]
        idx = [i for i, t in enumerate(cols["t_s"]) if t0 - before <= t <= t0 + after]
        sub = {"t_s": [round(cols["t_s"][i] - t0, 4) for i in idx], "vertical_g": [g[i] for i in idx]}
        if raw and hz:
            sub["raw_g"] = [cols["vertical_g"][i] for i in idx]
        sub = ia.decimate_columns(sub, max(100, min(3000, points)))
        out.append({"id": run_id, "condition": run["condition"], "aligned_on": "front" if a.get("front_t_s") is not None else "peak",
                    "rear_t_s": None if a.get("rear_t_s") is None or a.get("front_t_s") is None else round(a["rear_t_s"] - a["front_t_s"], 4),
                    "peak_g": a.get("peak_g"), "front_g": a.get("front_g"), "rear_g": a.get("rear_g"),
                    "peak_rel_s": None if a.get("peak_t_s") is None else round(a["peak_t_s"] - t0, 4),
                    "events_rel_s": {e["kind"]: round(e["peak_t_s"] - t0, 4) for e in (a.get("events") or []) if e.get("peak_t_s") is not None and e.get("kind")},
                    "speed_mps": a.get("speed_mps"), "bump_detected": a.get("bump_detected"),
                    "pair_source": a.get("pair_source"), "excluded": run.get("excluded"),
                    "t": sub["t_s"], "g": sub["vertical_g"], **({"raw": sub["raw_g"]} if "raw_g" in sub else {})})
    return {"series": out, "filter_hz": hz}


class VisionUpdate(BaseModel):
    roi_top: float | None = Field(default=None, ge=0.0, le=0.95)
    roi_bottom: float | None = Field(default=None, ge=0.05, le=1.0)
    center_gap: float | None = Field(default=None, ge=0.0, le=0.8)
    k_sigma: float | None = Field(default=None, ge=1.5, le=10)
    threshold: float | None = Field(default=None, ge=0.01, le=0.9)


@router.post("/vision/calibrate")
async def vision_calibrate(body: VisionUpdate | None = None):
    """방지턱이 화면에 없을 때(출발선에서) 바닥 색을 저장합니다."""
    from .camera import camera_service
    settings = store.load_settings()
    vs = settings.vision.model_copy(update={k: v for k, v in (body.model_dump() if body else {}).items() if v is not None})
    arr = await asyncio.to_thread(camera_service.capture_array)
    if arr is None:
        raise HTTPException(503, "카메라 영상을 가져오지 못했습니다(카메라 연결·모의 모드 확인).")
    settings.vision = vis.calibrate(arr, vs)
    store.save_settings(settings)
    return {"vision": settings.vision.model_dump(), "score": vis.score(arr, settings.vision)}


class VisionTeach(BaseModel):
    x0: float = Field(ge=0, le=1)
    y0: float = Field(ge=0, le=1)
    x1: float = Field(ge=0, le=1)
    y1: float = Field(ge=0, le=1)


async def _vision_frame(step: int = 4):
    from .camera import camera_service
    vs = store.load_settings().vision
    if vs.tilt_deg is not None:
        try:
            await asyncio.to_thread(control().adapter.set_camera_view, 0, int(round(vs.tilt_deg)))
            await asyncio.sleep(0.25)
        except Exception:
            pass
    arr = await asyncio.to_thread(camera_service.capture_array, step)
    if arr is None:
        raise HTTPException(503, "카메라 영상을 가져오지 못했습니다(카메라 연결·모의 모드 확인).")
    return arr


def _vision_view(arr: Any, vs: VisionSettings, note: str | None = None) -> dict[str, Any]:
    sc = vis.score(arr, vs)
    return {"image": vis.preview_jpeg(arr), "score": sc, "threshold": vs.threshold, "detected": vis.detected(sc, vs),
            "box": vis.bump_box(arr, vs), "mode": "taught" if vs.bump_color is not None else "floor" if vs.floor_mean is not None else None,
            "calibrated": vis.ready(vs), "roi": {"top": vs.roi_top, "bottom": vs.roi_bottom, "center_gap": vs.center_gap},
            "vision": vs.model_dump(), "note": note}


@router.get("/vision/snapshot")
async def vision_snapshot():
    """카메라 보정 화면: 지금 화면 + 감지 영역 + 방지턱으로 본 곳(초록 네모) + 점수."""
    arr = await _vision_frame()
    return _vision_view(arr, store.load_settings().vision)


@router.post("/vision/teach")
async def vision_teach(body: VisionTeach):
    """화면에서 방지턱을 네모로(또는 한 점 클릭) 고르면 그 색을 방지턱 색으로 배웁니다."""
    arr = await _vision_frame()
    settings = store.load_settings()
    try:
        settings.vision, note = vis.teach(arr, (body.x0, body.y0, body.x1, body.y1), settings.vision)
    except ValueError as e:
        raise HTTPException(400, str(e))
    store.save_settings(settings)
    return _vision_view(arr, settings.vision, note)


@router.post("/vision/forget")
async def vision_forget():
    """학습한 방지턱 색을 지우고 바닥 비교 방식으로 돌아감."""
    settings = store.load_settings()
    settings.vision = settings.vision.model_copy(update={"bump_color": None, "bump_tol": None})
    store.save_settings(settings)
    return {"vision": settings.vision.model_dump()}


@router.get("/vision/test")
async def vision_test():
    """지금 화면의 방지턱 점수(왼쪽, 오른쪽). 방지턱 앞에 차를 두고 점수가 기준을 넘는지 확인하세요."""
    from .camera import camera_service
    vs = store.load_settings().vision
    arr = await asyncio.to_thread(camera_service.capture_array)
    if arr is None:
        raise HTTPException(503, "카메라 영상을 가져오지 못했습니다(카메라 연결·모의 모드 확인).")
    sc = vis.score(arr, vs)
    return {"score": sc, "threshold": vs.threshold, "detected": vis.detected(sc, vs), "calibrated": vis.ready(vs),
            "roi": {"top": vs.roi_top, "bottom": vs.roi_bottom, "center_gap": vs.center_gap}}


@router.get("/ultrasonic/test")
async def ultrasonic_test(seconds: float = 1.0):
    """초음파 센서 점검: 정지 상태로 1초 동안 읽어 평균·흩어짐·읽기 속도를 봅니다(차를 벽 앞에 두고)."""
    ctl = control()
    seconds = max(0.3, min(5.0, seconds))
    logger = DistanceLogger(ctl.adapter.read_distance, store.load_settings().ultrasonic_hz)
    logger.start()
    await asyncio.sleep(seconds)
    logger.stop()
    vals = [d for _, d in logger.samples if d is not None]
    n = len(logger.samples)
    mean = sum(vals) / len(vals) if vals else None
    sd = (sum((x - mean) ** 2 for x in vals) / (len(vals) - 1)) ** 0.5 if len(vals) > 1 and mean is not None else None
    return {"samples": n, "valid": len(vals), "mean_cm": None if mean is None else round(mean, 2),
            "sd_cm": None if sd is None else round(sd, 2), "rate_hz": round(n / seconds, 1),
            "values": [None if d is None else round(d, 1) for _, d in logger.samples][:80],
            "available": bool(vals), "note": None if vals else "거리값을 읽지 못했습니다(모의 모드이거나 센서 연결·라이브러리 확인)."}


@router.get("/export/bump_windows.zip")
async def export_bump_windows(ids: str = "", before: float = 0.15, after: float = 0.45):
    """모든(또는 고른) 회차에서 '방지턱 부분'(앞바퀴 −before ~ +after 초)의 가속도만 잘라 한 번에 내보냅니다.

    - bump_windows_long.csv : 한 줄 = 한 표본(회차 정보 + 앞바퀴 기준 시간 + 원래 값 + 거른 값). 피벗·필터용
    - bump_windows_wide.csv : 1 ms 간격 공통 시간축에 회차별 열(엑셀에서 바로 겹친 그래프)
    - bump_windows_summary.csv : 회차별 핵심값(속도, 최대 충격, 앞·뒤 바퀴 g, 충격량, 펄스 길이 등)
    """
    ctl = control()
    settings = store.load_settings()
    hz = settings.filter_hz
    runs = ia.harmonize_pairs(runs_for_mode(ctl.adapter.mode != "hardware"))
    want = {x for x in ids.split(",") if x}
    runs = [r for r in runs if (not want or r["id"] in want) and (r.get("analysis") or {}).get("front_t_s") is not None]
    if not runs:
        raise HTTPException(404, "방지턱 통과가 확인된 회차가 없습니다.")
    runs.sort(key=lambda r: (r["condition"].get("bump_id") or "", r["condition"].get("total_mass_g") or 0,
                             r["condition"].get("output_mode") or "", r["condition"].get("output_value") or 0, r["condition"].get("trial") or 0))
    head = ["run_id", "방지턱", "총질량_g", "출력방식", "출력값", "방향", "회차", "목적", "제외"]
    long_rows: list[list[Any]] = [head + ["t_rel_s", "vertical_g_raw", "vertical_g_filtered"]]
    grid = [round(-before + i * 0.001, 3) for i in range(int(round((before + after) / 0.001)) + 1)]
    wide_cols: list[tuple[str, list[Any]]] = []
    summary: list[list[Any]] = [head + ["통과속도_mps", "최대충격_g", "평소보다더_g", "앞바퀴_g", "뒷바퀴_g", "앞뒤간격_s",
                                        "충격량_Ns", "펄스길이_s", "위쪽속도변화_mps", "이론_위쪽속도변화_mps", "꼭대기최솟값_g", "이론_꼭대기_g", "Π"]]
    for r in runs:
        try:
            cols = await asyncio.to_thread(store.read_columns, r["id"])
        except HTTPException:
            continue
        a = r["analysis"]
        c = r["condition"]
        t0 = a["front_t_s"]
        cols, _ = ia.remove_glitches(cols)
        f = ia.peak_preserving_filter(cols["t_s"], cols["vertical_g"], hz) if hz else cols["vertical_g"]
        info = [r["id"], c.get("bump_id"), c.get("total_mass_g"), c.get("output_mode"), c.get("output_value"),
                c.get("direction") or "forward", c.get("trial"), c.get("purpose") or "main", "예" if r.get("excluded") else ""]
        ts, gs, fs = [], [], []
        for i, t in enumerate(cols["t_s"]):
            rel = t - t0
            if -before <= rel <= after:
                ts.append(rel); gs.append(cols["vertical_g"][i]); fs.append(f[i])
                long_rows.append(info + [round(rel, 5), cols["vertical_g"][i], round(f[i], 4)])
        col = []
        k = 0
        for x in grid:
            while k + 1 < len(ts) and ts[k + 1] <= x:
                k += 1
            if not ts or x < ts[0] - 0.0015 or x > ts[-1] + 0.0015:
                col.append("")
            elif x <= ts[0]:
                col.append(round(fs[0], 4))
            elif k + 1 < len(ts) and ts[k + 1] > ts[k]:
                w = (x - ts[k]) / (ts[k + 1] - ts[k])
                col.append(round(fs[k] + (fs[k + 1] - fs[k]) * w, 4))
            else:
                col.append(round(fs[k], 4))
        base = f"{c.get('bump_id')}_{int(c.get('total_mass_g') or 0)}g_{c.get('output_mode')}{int(c.get('output_value') or 0)}_t{c.get('trial')}"
        if (c.get("purpose") or "main") != "main":
            base += "_" + (c.get("purpose") or "")
        used = {n for n, _ in wide_cols}
        label, k2 = base, 2
        while label in used:
            label, k2 = f"{base}_{k2}", k2 + 1
        wide_cols.append((label, col))
        summary.append(info + [a.get("speed_mps"), a.get("peak_g"), a.get("peak_delta_g"), a.get("front_g"), a.get("rear_g"), a.get("axle_dt_s"),
                               a.get("impulse_ns"), a.get("pulse_s"), a.get("dvz_mps"), a.get("dvz_theory_mps"), a.get("crest_min_g"), a.get("crest_theory_g"), a.get("pi")])
    wide = [["t_rel_s"] + [n for n, _ in wide_cols]]
    for i, x in enumerate(grid):
        wide.append([x] + [col[i] for _, col in wide_cols])

    def csv_text(rows: list[list[Any]]) -> str:
        b = io.StringIO()
        csv.writer(b).writerows(rows)
        return "\ufeff" + b.getvalue()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("bump_windows_long.csv", csv_text(long_rows))
        z.writestr("bump_windows_wide.csv", csv_text(wide))
        z.writestr("bump_windows_summary.csv", csv_text(summary))
        z.writestr("README.txt", "앞바퀴가 방지턱에 닿은 순간을 0초로 맞춰 방지턱 부분만 잘라낸 가속도입니다.\n"
                   f"구간: -{before}s ~ +{after}s, 진동 거르기: {'끔' if not hz else f'{hz:g} Hz (피크 주변은 원래 값 유지)'}\n"
                   "wide 파일은 1 ms 간격으로 보간한 거른 값입니다. long 파일에는 원래 값과 거른 값이 모두 있습니다.\n")
    return Response(content=buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="picarx_bump_windows.zip"'})


@router.get("/runs/{run_id}")
async def get_run(run_id: str, points: int = 1500):
    run = store.get(run_id)
    same = [r for r in store.list_runs() if r.get("simulated") == run.get("simulated")]
    run = next((r for r in ia.harmonize_pairs(same) if r.get("id") == run_id), run)
    cols = await asyncio.to_thread(store.read_columns, run_id)
    cols, _ = ia.remove_glitches(cols)
    base = {k: cols[k] for k in ("t_s", "vertical_g", "dynamic_g")}
    hz = store.load_settings().filter_hz
    if hz:
        base["filtered_g"] = ia.peak_preserving_filter(cols["t_s"], cols["vertical_g"], hz)
    series = ia.decimate_columns(base, max(200, min(5000, points)))
    dseries = dist.series(run["distance"]["t_s"], run["distance"]["cm"], run["condition"].get("direction") or "forward") if run.get("distance") else None
    return {**run_summary(run), "series": series, "drive_log": run.get("drive_log"), "distance_series": dseries}


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
    if update.purpose is not None:
        run["condition"]["purpose"] = update.purpose
    if update.peak_override is not None or update.clear_peak_override:
        if update.peak_override is not None and update.peak_override.rear_t_s <= update.peak_override.front_t_s:
            raise HTTPException(422, "뒷바퀴 피크는 앞바퀴 피크보다 늦어야 합니다.")
        run["peak_override"] = update.peak_override.model_dump() if update.peak_override else None
        cols = await asyncio.to_thread(store.read_columns, run_id)
        run["analysis"] = run_analysis(run, cols, store.load_settings())
    store.put(run)
    return run_summary(run)


@router.delete("/runs/{run_id}")
async def delete_run(run_id: str):
    store.get(run_id)
    store.delete(run_id)
    return {"deleted": run_id}


@router.post("/runs/delete")
async def delete_runs(body: BulkDelete):
    if auto_runner.busy:
        raise HTTPException(409, "측정 중에는 삭제할 수 없습니다.")
    deleted = []
    for run_id in body.ids:
        try:
            store.get(run_id)
        except HTTPException:
            continue
        store.delete(run_id)
        deleted.append(run_id)
    return {"deleted": deleted}


@router.post("/reset")
async def reset(body: ResetRequest):
    """실험 기록 초기화. 지우지 않고 휴지통으로 옮기므로 되돌릴 수 있습니다."""
    if body.confirm.strip() != "초기화":
        raise HTTPException(422, "확인 문구 '초기화'를 입력하세요.")
    if auto_runner.busy:
        raise HTTPException(409, "측정 중에는 초기화할 수 없습니다.")
    ctl = control()
    runs = store.list_runs() if body.scope == "all" else runs_for_mode(ctl.adapter.mode != "hardware")
    batch = store.move_to_trash([r["id"] for r in runs], "all" if body.scope == "all" else ("sim" if ctl.adapter.mode != "hardware" else "real"))
    record_event("experiment", "Runs reset to trash", details={"batch": batch, "count": len(runs)})
    return {"moved": len(runs), "batch": batch}


class PurposeClear(BaseModel):
    purpose: Literal["main", "validation", "pilot"]
    confirm: str


@router.post("/runs/clear-purpose")
async def runs_clear_purpose(body: PurposeClear):
    """식 검증·예비 시행처럼 한 종류만 휴지통으로 옮깁니다(되돌릴 수 있음)."""
    if body.confirm.strip() != "삭제":
        raise HTTPException(422, "확인 문구 '삭제'를 입력하세요.")
    if auto_runner.busy:
        raise HTTPException(409, "측정 중에는 지울 수 없습니다.")
    ids = [r["id"] for r in store.list_runs() if ((r.get("condition") or {}).get("purpose") or "main") == body.purpose]
    if not ids:
        return {"moved": 0, "batch": None}
    batch = store.move_to_trash(ids, body.purpose)
    return {"moved": len(ids), "batch": batch}


@router.get("/trash")
async def trash():
    return {"batches": store.trash_batches()}


@router.post("/trash/{batch}/restore")
async def trash_restore(batch: str):
    return {"restored": store.restore_batch(batch)}


@router.delete("/trash/{batch}")
async def trash_purge(batch: str):
    store.purge_batch(batch)
    return {"purged": batch}


# --------------------------------------------------------------------------- 프로젝트 보관
# 지금까지의 실험 기록(시행 원자료)과 설정 사본을 data/projects/<id>/ 로 옮겨 영구 보관하고, 메인은 빈 상태로 둡니다.
# 초기화(휴지통)와 달리 제목·실험 날짜·메모를 붙여 두고, 나중에 '불러오기'로 메인에 복사해 다시 볼 수 있습니다.
class ProjectSave(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    date: str = Field(default="", max_length=20, description="실험 날짜(YYYY-MM-DD)")
    note: str = Field(default="", max_length=2000)
    reset_settings: bool = Field(default=False, description="설정(기준·모델 선택 등)도 기본값으로. 센서 보정·방지턱 목록은 유지")


class ProjectEdit(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=80)
    date: str | None = Field(default=None, max_length=20)
    note: str | None = Field(default=None, max_length=2000)


PROJECT_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-f0-9]{4}$")


def _projects_dir() -> Path:
    return store.root / "projects"


def _project_dir(pid: str) -> Path:
    d = _projects_dir() / pid
    if not PROJECT_ID.match(pid) or not d.is_dir():
        raise HTTPException(404, "프로젝트를 찾을 수 없습니다.")
    return d


def _project_meta(d: Path) -> dict[str, Any]:
    try:
        meta = json.loads((d / "project.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        meta = {"id": d.name, "title": d.name}
    runs = list((d / "experiments").glob("*.json")) if (d / "experiments").is_dir() else []
    meta["runs"] = len(runs)
    meta["size_kb"] = round(sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) / 1024)
    return meta


@router.get("/projects")
async def projects_list():
    root = _projects_dir()
    items = [_project_meta(d) for d in sorted(root.iterdir(), reverse=True) if d.is_dir() and PROJECT_ID.match(d.name)] if root.is_dir() else []
    return {"projects": items, "main_runs": len(store.list_runs())}


@router.post("/projects")
async def projects_save(body: ProjectSave):
    """메인의 시행 기록을 새 프로젝트 폴더로 옮기고(설정은 사본), 메인은 비웁니다."""
    if auto_runner.busy:
        raise HTTPException(409, "측정 중에는 프로젝트로 저장할 수 없습니다.")
    runs = store.list_runs()
    if not runs:
        raise HTTPException(409, "메인에 저장할 시행 기록이 없습니다.")
    pid = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    d = _projects_dir() / pid
    (d / "experiments").mkdir(parents=True, exist_ok=True)
    settings = store.load_settings()
    (d / "experiment_settings.json").write_text(settings.model_dump_json(indent=2), encoding="utf-8")
    meta = {"id": pid, "title": body.title.strip(), "date": body.date.strip() or datetime.now().strftime("%Y-%m-%d"), "note": body.note.strip(),
            "saved_at": datetime.now(timezone.utc).isoformat(), "run_count": len(runs),
            "first_run": runs[-1].get("created_at"), "last_run": runs[0].get("created_at")}
    (d / "project.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    moved = 0
    for path in list(store.runs_dir.iterdir()):
        if path.suffix in (".json", ".csv"):
            shutil.move(str(path), str(d / "experiments" / path.name))
            moved += path.suffix == ".json"
    if body.reset_settings:
        fresh = ExperimentSettings()
        keep = {k: getattr(settings, k) for k in ("bumps", "base_mass_g", "line", "linecam", "vision", "heading_sign", "heading_ff_forward", "heading_ff_backward", "wheelbase_mm", "wheel_radius_mm") if hasattr(settings, k)}
        store.save_settings(fresh.model_copy(update=keep))
    record_event("experiment", "Project saved", details={"id": pid, "runs": moved})
    return {"project": _project_meta(d), "moved": moved}


@router.patch("/projects/{pid}")
async def projects_edit(pid: str, body: ProjectEdit):
    d = _project_dir(pid)
    meta = _project_meta(d)
    meta.update({k: v.strip() for k, v in body.model_dump().items() if v is not None})
    meta.pop("runs", None); meta.pop("size_kb", None)
    (d / "project.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"project": _project_meta(d)}


@router.post("/projects/{pid}/open")
async def projects_open(pid: str, settings_too: bool = False, merge: bool = False, purposes: str = ""):
    """보관한 프로젝트를 메인으로 복사합니다(프로젝트 폴더는 그대로 남음).

    merge=false: 메인이 비어 있어야 함(섞이지 않게). merge=true: 지금 메인 기록에 더함(같은 시행은 건너뜀).
    purposes: 'main,validation,pilot' 중 고른 종류만(빈칸 = 전부). 예: 관계식용(main)만 가져와 지금 실험에 더하기.
    """
    d = _project_dir(pid)
    if auto_runner.busy:
        raise HTTPException(409, "측정 중에는 불러올 수 없습니다.")
    if not merge and store.list_runs():
        raise HTTPException(409, "메인에 시행 기록이 있습니다. '지금 기록에 더하기'로 불러오거나, 먼저 지금 기록을 프로젝트로 저장하세요.")
    want = {p for p in purposes.split(",") if p in ("main", "validation", "pilot")}
    store.runs_dir.mkdir(parents=True, exist_ok=True)
    n = skipped = 0
    for path in sorted((d / "experiments").glob("*.json")):
        try:
            run = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if want and ((run.get("condition") or {}).get("purpose") or "main") not in want:
            continue
        if (store.runs_dir / path.name).exists():
            skipped += 1
            continue
        shutil.copy2(path, store.runs_dir / path.name)
        if path.with_suffix(".csv").exists():
            shutil.copy2(path.with_suffix(".csv"), store.runs_dir / path.with_suffix(".csv").name)
        n += 1
    if settings_too and (d / "experiment_settings.json").exists():
        try:
            store.save_settings(ExperimentSettings.model_validate_json((d / "experiment_settings.json").read_text(encoding="utf-8")))
        except ValueError:
            pass
    return {"opened": n, "skipped": skipped}


@router.get("/projects/{pid}/download.zip")
async def projects_download(pid: str):
    d = _project_dir(pid)

    def build() -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(d.rglob("*")):
                if f.is_file():
                    z.write(f, str(f.relative_to(d)))
        return buf.getvalue()
    data = await asyncio.to_thread(build)
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="picarx_project_{pid}.zip"'})


@router.delete("/projects/{pid}")
async def projects_delete(pid: str, confirm: str = ""):
    d = _project_dir(pid)
    if confirm.strip() != "삭제":
        raise HTTPException(422, "확인 문구 '삭제'를 입력하세요.")
    shutil.rmtree(d, ignore_errors=True)
    return {"deleted": pid}


@router.get("/backup.zip")
async def backup_zip():
    """실험 기록 전체(원자료·설정 포함) 백업."""
    def build() -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            if store.settings_path.exists():
                z.write(store.settings_path, "experiment_settings.json")
            if store.runs_dir.is_dir():
                for path in sorted(store.runs_dir.iterdir()):
                    if path.suffix in (".json", ".csv"):
                        z.write(path, f"experiments/{path.name}")
        return buf.getvalue()
    data = await asyncio.to_thread(build)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="picarx_experiment_backup_{stamp}.zip"'})


@router.get("/applied")
async def applied():
    """지금 적용되는 주행·조향·학습 설정."""
    return applied_snapshot()


@router.get("/data-check")
async def data_check():
    """실제 측정 데이터가 제대로 들어오는지 점검합니다."""
    ctl = control()
    all_runs = store.list_runs()
    real = [r for r in all_runs if not r.get("simulated")]
    sim = [r for r in all_runs if r.get("simulated")]
    last = real[0] if real else None
    rates = [r.get("analysis", {}).get("sample_rate_hz") for r in real if r.get("analysis", {}).get("sample_rate_hz")]
    sensor = await asyncio.to_thread(mpu6050_service.status) if ctl.adapter.mode == "hardware" else None
    checks = []
    if ctl.adapter.mode != "hardware":
        checks.append({"ok": False, "text": "서버가 모의 모드입니다. 지금 저장되는 기록은 합성 데이터이며 실제 측정 목록에 섞이지 않습니다."})
    else:
        checks.append({"ok": bool(ctl.adapter.connected), "text": "PiCar-X 하드웨어 연결" + ("됨" if ctl.adapter.connected else " 안 됨")})
        checks.append({"ok": bool(sensor and sensor.get("available")), "text": "MPU-6050 응답" + (" 있음" if sensor and sensor.get("available") else " 없음")})
        checks.append({"ok": bool(sensor and sensor.get("calibrated")), "text": "정지 상태 보정" + (" 완료" if sensor and sensor.get("calibrated") else " 필요")})
        rate = sensor.get("actual_sample_rate_hz") if sensor else None
        checks.append({"ok": bool(rate and rate >= 500), "text": f"실시간 표본 속도 {rate or '—'} Hz (500 Hz 이상 권장)"})
        if sensor:
            tgt = sensor.get("sample_rate_target_hz")
            mode = "센서 버퍼(FIFO)" if sensor.get("read_mode") == "fifo" else "한 표본씩 직접 읽기"
            checks.append({"ok": bool(tgt and tgt >= 1000), "text": f"읽기 방식 {mode} · 목표 {tgt or '—'} Hz"
                           + (f" · {sensor.get('fifo_note')}" if sensor.get("fifo_note") else "")})
            ov, ra = sensor.get("fifo_overflows") or 0, sensor.get("fifo_realigns") or 0
            checks.append({"ok": ov == 0 and ra <= 2, "text": f"센서 버퍼 넘침 {ov}번 · 칸 다시 맞춤 {ra}번 · 깨진 표본 {mpu6050_service.bad_reads}개 · 통신 오류 {mpu6050_service.io_errors}번 (서버 켠 뒤 누적). "
                           "넘침·칸 맞춤이 늘면 측정 중 카메라 화면·초음파를 끄고, I²C를 400 kHz로 올리세요."})
    recent = [r for r in real[:10] if (r.get("analysis") or {}).get("max_gap_ms") is not None]
    if recent:
        gaps = [r["analysis"]["max_gap_ms"] for r in recent]
        bad = sum(1 for r in recent if (r.get("analysis") or {}).get("corrupt_swings", 0) >= 5)
        checks.append({"ok": max(gaps) <= 50 and bad == 0, "text": f"최근 실제 시행 {len(recent)}회: 가장 긴 기록 공백 {max(gaps):.0f} ms"
                       + (f", 값이 번갈아 튀어 빠진 회차 {bad}회" if bad else "") + " (공백 50 ms 이하 권장)"})
    if last:
        rec = last.get("recording") or {}
        checks.append({"ok": (rec.get("count") or 0) > 100, "text": f"마지막 실제 시행 표본 {rec.get('count') or 0}개, {rec.get('sample_rate_hz') or '—'} Hz, 수직축 {rec.get('vertical_axis') or '—'}"})
        span = None
        try:
            cols = await asyncio.to_thread(store.read_columns, last["id"])
            vg = cols["vertical_g"]
            span = (min(vg), max(vg)) if vg else None
        except HTTPException:
            pass
        if span:
            checks.append({"ok": span[1] - span[0] > 0.05, "text": f"마지막 실제 시행 수직 가속도 범위 {span[0]:.2f} ~ {span[1]:.2f} g (변화가 없으면 센서·배선 확인)"})
    return {"mode": ctl.adapter.mode, "real_runs": len(real), "simulated_runs": len(sim),
            "last_real_run": run_summary(last) if last else None,
            "mean_sample_rate_hz": sum(rates) / len(rates) if rates else None, "checks": checks}


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
    from .line_follow import line_runner
    if auto_runner.busy or line_runner.busy or symmetry_runner.busy or any(f() for f in extra_busy):
        raise HTTPException(409, "이미 자동 측정 주행(또는 라인 추종·점검·코스 반복)이 진행 중입니다.")
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
    if request.return_output and request.return_output > EXPERIMENT_MAX_OUTPUT:
        raise HTTPException(422, f"복귀 출력 상한은 {EXPERIMENT_MAX_OUTPUT}입니다.")
    auto_runner.last_result = None
    auto_runner.completed = []
    auto_runner.cycle = 1
    auto_runner.cycles = request.repeats or settings.repeats
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
            "video_speed_mps", "min_g", "jump", "airborne_s", "landing_g", "peak_force_n", "dynamic_force_n", "pi", "critical_speed_mps",
            "purpose", "pair_source", "battery_v", "steering_source", "heading_max_deg", "data_source", "flags", "note"]
    rows: list[list[Any]] = [head]
    for r in sorted(runs, key=lambda r: r.get("created_at", "")):
        c, a, b = r["condition"], r.get("analysis") or {}, r.get("bump") or {}
        rows.append([r["id"], r["created_at"], r.get("simulated"), r.get("source"), r.get("excluded"), r.get("aborted"),
                     c.get("trial"), c.get("bump_id"), b.get("height_mm"), b.get("width_mm"), b.get("radius_mm"),
                     c.get("base_mass_g"), c.get("added_mass_g"), c.get("total_mass_g"), c.get("output_mode"), c.get("output_value"),
                     a.get("sample_rate_hz"), a.get("peak_g"), a.get("peak_delta_g"), a.get("front_t_s"), a.get("rear_t_s"),
                     a.get("axle_dt_s"), a.get("speed_mps"), c.get("video_speed_mps"), a.get("min_g"), a.get("jump"),
                     a.get("airborne_s"), a.get("landing_g"), a.get("peak_force_n"), a.get("dynamic_force_n"), a.get("pi"),
                     a.get("critical_speed_mps"), c.get("purpose") or "main", a.get("pair_source"), r.get("battery_v"),
                     r.get("steering_source"), r.get("heading_max_deg"), r.get("data_source") or ("synthetic" if r.get("simulated") else "mpu6050"),
                     " / ".join(a.get("flags") or []), c.get("note")])
    return _csv_response(rows, "picarx_runs.csv")


PAIR_LABEL = {"pitch": "기울기로 찾음", "pattern": "충격 4개 규칙", "consistency": "같은 조건에 맞춰 다시 고름",
              "manual": "직접 지정", "auto": "가장 큰 두 충격"}


@router.get("/export/summary.csv")
async def export_summary():
    """보고서용 핵심 요약: 조건(PWM)별로 정렬한 회차 표 + 조건 평균. 열 이름은 쉬운 말."""
    ctl = control()
    settings = store.load_settings()
    runs = ia.harmonize_pairs(runs_for_mode(ctl.adapter.mode != "hardware"))
    bundle = analysis_bundle(runs, settings)
    crit = bundle.get("criteria") or {}
    head = ["방지턱", "총질량(g)", "출력", "방향", "회차", "계산에 포함", "통과 속도(m/s)", "앞·뒤 바퀴 간격(ms)", "가장 큰 충격(g)",
            "평소보다 더(Δa, g)", "허용 기준 초과", "바퀴 뜸", "바퀴 찾은 방법", "표본(Hz)",
            "왼쪽 PWM(듀티%)", "오른쪽 PWM(듀티%)", "도는 속도(°/s, +왼쪽)", "방지턱 도착 때 방향(°)", "조향", "배터리(V)", "확인할 점"]
    rows: list[list[Any]] = [head]
    key = lambda r: (r["condition"].get("bump_id") or "", r["condition"].get("total_mass_g") or 0,
                     r["condition"].get("output_value") or 0, r["condition"].get("trial") or 0)
    for r in sorted(runs, key=key):
        c, a = r["condition"], r.get("analysis") or {}
        lim = (crit.get(c.get("bump_id")) or {}).get("a_limit_g")
        used = not r.get("excluded") and not r.get("aborted") and a.get("bump_detected") is not False and (c.get("purpose") or "main") == "main"
        rows.append([c.get("bump_id"), c.get("total_mass_g"), f"{(c.get('output_mode') or 'pwm').upper()} {c.get('output_value')}",
                     "후진" if c.get("direction") == "backward" else "전진", c.get("trial"),
                     "예" if used else "아니오", a.get("speed_mps"), None if a.get("axle_dt_s") is None else round(a["axle_dt_s"] * 1000),
                     a.get("peak_g"), a.get("peak_delta_g"),
                     "" if lim is None or a.get("peak_g") is None else ("초과" if a["peak_g"] > lim else "이내"),
                     "" if a.get("jump") is None else ("예" if a["jump"] else "아니오"),
                     "방지턱 못 찾음" if a.get("bump_detected") is False else PAIR_LABEL.get(a.get("pair_source") or "", ""),
                     a.get("sample_rate_hz"),
                     *(lambda m: [f"{m['left_pwm']} ({m['left_duty']})" if m.get("left_pwm") is not None else "",
                                  f"{m['right_pwm']} ({m['right_duty']})" if m.get("right_pwm") is not None else "",
                                  m.get("yaw_rate_dps"), m.get("heading_at_bump_deg"),
                                  {"none": "없음", "gyro": "자이로", "line": "유도선"}.get(m.get("steering") or "", m.get("steering") or "")])(r.get("motor") or {}),
                     r.get("battery_v"), " / ".join(a.get("flags") or [])])
    rows.append([])
    method_label = {"trim": "가장 큰 값·작은 값 1개씩 뺀 평균", "sigma2": "평균 ± 2×표준편차 밖의 값을 뺀 평균",
                    "iqr": "사분위 범위(IQR) 밖의 값을 뺀 평균", "none": "모든 회차 평균"}.get(settings.outlier_method, settings.outlier_method)
    rows.append([f"# 조건별 평균 ({method_label})"])
    rows.append(["방지턱", "총질량(g)", "출력", "방향", "회차 수(계산에 쓴 수)", "평균 속도(m/s)", "속도 범위(m/s)", "평균 최대 충격(g)",
                 "평균 Δa(g)", "Δa 범위(g)", "바퀴 뜬 회차"])
    for g in bundle["groups"]:
        rows.append([g["bump_id"], g["total_mass_g"], f"{g['output_mode'].upper()} {g['output_value']}",
                     "후진" if g.get("direction") == "backward" else "전진", f"{g['n_runs']} ({g['n_used']})",
                     g.get("mean_speed_mps"), f"{g.get('speed_min_mps')}–{g.get('speed_max_mps')}", g.get("mean_peak_g"),
                     g.get("mean_peak_delta_g"), f"{g.get('kept_min_delta_g')}–{g.get('kept_max_delta_g')}", f"{g['jump_count']}/{g['n_runs']}"])
    for bump_id, lines in (bundle.get("interpretation") or {}).items():
        rows.append([])
        rows.append([f"# 자동 해석 (방지턱 {bump_id})"])
        rows.extend([[line] for line in lines])
    rows = [[int(x) if isinstance(x, float) and x.is_integer() else x for x in row] for row in rows]
    return _csv_response(rows, "picarx_summary.csv")


@router.get("/export/raw.zip")
async def export_raw_zip(ids: str = ""):
    """여러 회차의 원자료 CSV를 한 ZIP으로(ids 없으면 현재 모드의 전체). 파일 이름에 방지턱·질량·출력·회차가 들어갑니다."""
    ctl = control()
    runs = runs_for_mode(ctl.adapter.mode != "hardware")
    want = {x for x in ids.split(",") if x}
    if want:
        runs = [r for r in runs if r["id"] in want]
    if not runs:
        raise HTTPException(404, "내보낼 회차가 없습니다.")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for r in runs:
            path = store.csv_path(r["id"])
            if not path.is_file():
                continue
            c = r["condition"]
            name = (f"{c.get('bump_id')}_m{int(c.get('total_mass_g') or 0)}_{c.get('output_mode')}{int(c.get('output_value') or 0)}"
                    f"_t{c.get('trial')}_{r['id']}.csv")
            z.writestr(name, "\ufeff" + path.read_text(encoding="utf-8"))
    return Response(content=buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="picarx_raw_runs.zip"'})


@router.get("/export/conditions.csv")
async def export_conditions():
    ctl = control()
    settings = store.load_settings()
    bundle = analysis_bundle(runs_for_mode(ctl.adapter.mode != "hardware"), settings)
    head = ["bump_id", "total_mass_g", "output_mode", "output_value", "n_runs", "n_used", "mean_speed_mps",
            "speed_min_mps", "speed_max_mps", "mean_peak_g", "mean_peak_delta_g", "kept_min_delta_g", "kept_max_delta_g",
            "dropped_delta_g", "sd_peak_delta_g", "mean_force_n", "mean_impulse_ns", "mean_pulse_s", "mean_dvz_mps",
            "mean_crest_min_g", "mean_avg_force_n", "mean_pi", "jump_count"]
    rows: list[list[Any]] = [[f"# 이상치 제거 방식: {settings.outlier_method}"], head]
    for g in bundle["groups"]:
        rows.append([g.get(k) if k != "dropped_delta_g" else " / ".join(str(x) for x in g[k]) for k in head])
    rows.append([])
    rows.append(["# 관계식 후보 비교 (조건 평균 사용)"])
    for bump_id, cmp in bundle["models"].items():
        chosen = bundle["chosen_models"].get(bump_id)
        rows.append([f"방지턱 {bump_id}", f"선택: {chosen}", f"순위 기준: {cmp.get('rank_by') or '-'}", cmp.get("note") or ""])
        rows.append(["model_id", "name", "formula", "R2", "RMSE_g", "LOOCV_RMSE_g", "AIC", "parameters (value ± 95% CI)"])
        for m in cmp.get("models", []):
            params = "; ".join(f"{p['name']}={p['value']:.4g}" + (f" [{p['ci95'][0]:.4g}, {p['ci95'][1]:.4g}]" if p.get("ci95") else "") for p in m["params"])
            rows.append([m["id"], m["name"], m["formula"], round(m["r2"], 4), round(m["rmse"], 4),
                         None if m["loocv_rmse"] is None else round(m["loocv_rmse"], 4), None if m["aic"] is None else round(m["aic"], 3), params])
        crit = bundle["criteria"].get(bump_id) or {}
        if crit.get("a_limit_g"):
            rows.append(["허용 기준 a_lim(g)", crit["a_limit_g"], crit.get("source")])
            for v in crit.get("vmax_by_mass", []):
                rows.append(["v_max", f"{v['mass_g']} g", v["vmax_mps"]])
        rows.append(["# 자동 해석"])
        for line in bundle["interpretation"].get(bump_id, []):
            rows.append([line])
        rows.append([])
    return _csv_response(rows, "picarx_conditions.csv")


@router.get("/export/validation.csv")
async def export_validation():
    ctl = control()
    bundle = analysis_bundle(runs_for_mode(ctl.adapter.mode != "hardware"), store.load_settings())
    head = ["run_id", "created_at", "bump_id", "total_mass_g", "output_mode", "output_value", "trial", "speed_mps",
            "measured_delta_g", "predicted_delta_g", "abs_error_g", "error_pct", "model_id"]
    rows: list[list[Any]] = [head] + [[r.get(k) for k in head] for r in bundle["validation"]["rows"]]
    sm = bundle["validation"]["summary"]
    rows += [[], ["평가 수", sm["evaluated"], "평균 절대 오차율(%)", sm["mape"], "평균 오차(%)", sm["bias_pct"], "±10% 이내", sm["within_10pct"]]]
    return _csv_response(rows, "picarx_validation.csv")


# --------------------------------------------------------------------------- 라인 추종 (테이프)
@router.get("/line/status")
async def line_status():
    from .line_follow import line_runner, position, weights
    ctl = control()
    settings = store.load_settings().line
    raw = None
    error = None
    try:
        raw = await asyncio.to_thread(ctl.adapter.read_grayscale) if ctl.adapter.connected else None
    except Exception as exc:
        error = str(exc)
    w = weights(raw or [], settings)
    from .course import is_cross
    return {"raw": raw, "weights": w, "position": position(w, settings), "settings": settings.model_dump(),
            "calibrated": bool(settings.floor and settings.line), "error": error, "runner": line_runner.snapshot(),
            "cross": is_cross(raw, settings, store.load_settings().cross_threshold) if w else None,
            "check": calibration_check(settings) if settings.floor and settings.line else [],
            "auto_cal": dict(auto_cal_state)}


class LineCalibrate(BaseModel):
    target: Literal["floor", "line"]


@router.post("/line/calibrate")
async def line_calibrate(body: LineCalibrate):
    ctl = control()
    samples = []
    for _ in range(15):
        raw = clean_raw(await asyncio.to_thread(ctl.adapter.read_grayscale))
        if raw:   # 범위 밖으로 깨진 읽기는 버림
            samples.append(raw)
        await asyncio.sleep(0.02)
    if len(samples) < 3:
        raise HTTPException(503, "그레이스케일 센서값을 읽지 못했습니다(값이 계속 깨짐).")
    mean = [round(sorted(s[i] for s in samples)[len(samples) // 2], 1) for i in range(3)]   # 중앙값
    settings = store.load_settings()
    setattr(settings.line, body.target, mean)
    store.save_settings(settings)
    return {"target": body.target, "values": mean, "settings": settings.line.model_dump(),
            "check": calibration_check(settings.line) if settings.line.floor and settings.line.line else []}


auto_cal_state: dict[str, Any] = {"state": "idle", "message": ""}


@router.post("/line/auto_calibrate")
async def line_auto_calibrate():
    """자동 보정: 바닥 센서를 유도선 위에 두면, 차가 제자리에서 왼쪽 25° → 오른쪽 25° → 가운데로 천천히 돌며
    센서 3개가 각각 테이프와 바닥을 지나가게 하고, 센서마다 가장 어두운 값(테이프)과 가장 밝은 값(바닥)을 저장합니다."""
    from .course import Stopped, runner as crun, _face_target, CourseRequest
    ctl = control()
    if ctl.adapter.mode != "hardware":
        raise HTTPException(409, "자동 보정은 실제 차량에서만 됩니다.")
    if auto_runner.busy or any(f() for f in extra_busy):
        raise HTTPException(409, "다른 주행이 진행 중입니다.")
    mpu6050_service.heading_stop()
    await asyncio.sleep(0.5)
    if not mpu6050_service.heading_reset():
        raise HTTPException(409, "자동 보정은 자이로가 필요합니다. 센서 화면에서 MPU-6050 정지 보정을 먼저 하세요.")
    auto_cal_state.update(state="running", message="제자리에서 좌우로 돌며 센서값을 모읍니다")
    vals: list[list[float]] = [[], [], []]
    crun.gen = ctl.stop_generation
    crun.target_deg = 0.0
    try:
        for direction, goal in (("left", 25.0), ("right", -25.0)):
            t0 = time.monotonic()
            while time.monotonic() - t0 < 3.0:
                h = mpu6050_service.heading_deg() or 0.0
                if (direction == "left" and h >= goal) or (direction == "right" and h <= goal):
                    break
                if not await ctl.experiment_spin(direction, 60, crun.gen):
                    raise Stopped()
                for _ in range(4):
                    raw = clean_raw(await asyncio.to_thread(ctl.adapter.read_grayscale))
                    if raw:   # 범위(0~4095) 밖으로 깨진 읽기는 버림
                        for i in range(3):
                            vals[i].append(raw[i])
                    await asyncio.sleep(0.002)
            await ctl.stop(reason="line_auto_cal")
            crun.gen = ctl.stop_generation
            await asyncio.sleep(0.2)
        await _face_target(ctl, CourseRequest(condition=ExperimentCondition(bump_id="A", base_mass_g=1, output_value=1)))
    except Stopped:
        auto_cal_state.update(state="error", message="정지 명령으로 멈췄습니다.")
        raise HTTPException(409, "정지 명령으로 멈췄습니다.")
    finally:
        await ctl.stop(reason="line_auto_cal_end")
        mpu6050_service.heading_stop()
    if min(len(v) for v in vals) < 20:
        auto_cal_state.update(state="error", message="센서값을 읽지 못했습니다.")
        raise HTTPException(503, "그레이스케일 센서값을 읽지 못했습니다.")
    # 최댓값·최솟값 대신 상위·하위 3% 값(남은 튀는 값 하나에 휘둘리지 않게)
    def pct(xs: list[float], q: float) -> float:
        xs = sorted(xs)
        return xs[min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))]
    hi = [pct(v, 0.97) for v in vals]
    lo = [pct(v, 0.03) for v in vals]
    settings = store.load_settings()
    # 밝은 바닥에 검은 테이프: 바닥 = 가장 밝은 값, 테이프 = 가장 어두운 값(조금 안쪽으로: 잡음 여유 5%)
    settings.line.floor = [round(h - 0.05 * (h - l), 1) for h, l in zip(hi, lo)]
    settings.line.line = [round(l + 0.05 * (h - l), 1) for h, l in zip(hi, lo)]
    check = calibration_check(settings.line)
    store.save_settings(settings)
    auto_cal_state.update(state="done" if not check else "warn",
                          message="자동 보정 완료" if not check else "자동 보정했지만 확인이 필요합니다: " + " ".join(check))
    return {"floor": settings.line.floor, "line": settings.line.line, "check": check, "settings": settings.line.model_dump()}


@router.put("/line/settings")
async def line_settings(body: LineSettings):
    settings = store.load_settings()
    settings.line = body
    store.save_settings(settings)
    return settings.line.model_dump()


class LineRunRequest(BaseModel):
    output_mode: Literal["pwm", "duty"] = "pwm"
    output_value: float = Field(default=40, gt=0, le=100)
    seconds: float = Field(default=5, ge=0.5, le=30)


@router.post("/line/start")
async def line_start(body: LineRunRequest):
    from .line_follow import line_runner
    ctl = control()
    if auto_runner.busy or line_runner.busy:
        raise HTTPException(409, "다른 주행이 진행 중입니다.")
    settings = store.load_settings().line
    if not (settings.floor and settings.line):
        raise HTTPException(409, "바닥과 테이프 위에서 센서 보정을 먼저 하세요.")
    bad = [m for m in calibration_check(settings) if "범위" in m]
    if bad:
        raise HTTPException(409, bad[0])
    def save_sign(sign: int) -> None:
        st = store.load_settings()
        st.line.steer_sign = sign
        store.save_settings(st)
    def learn_ff(mean: float) -> None:
        st = store.load_settings()
        st.line.steer_ff_deg = round(max(-10.0, min(10.0, 0.5 * st.line.steer_ff_deg + 0.5 * mean)), 2)
        store.save_settings(st)
    guided = None
    if ctl.adapter.mode == "hardware" and mpu6050_service.heading_reset():
        # 자이로가 있으면 측정 주행과 같은 '유도선 + 자이로' 조향(방지턱 위 직진 고정, 학습된 직진 조향)으로 시험
        guided = GuidedSteer(settings, polarity=store.load_settings().heading_sign)
    line_runner.task = asyncio.create_task(line_runner.run(ctl, ctl.adapter.read_grayscale, settings, body.output_mode, body.output_value,
                                                           body.seconds, on_flip=save_sign, guided=guided, on_learn=learn_ff))
    await asyncio.sleep(0)
    return line_runner.snapshot()


@router.post("/line/stop")
async def line_stop():
    await control().stop(reason="line_follow_stop")
    return {"stopped": True}


# --------------------------------------------------------------------------- 전·후진 대칭 점검
class SymmetryRunner:
    def __init__(self) -> None:
        self.state = "idle"
        self.message = ""
        self.result: dict[str, Any] | None = None

    @property
    def busy(self) -> bool:
        return self.state == "running"


symmetry_runner = SymmetryRunner()


def _integrate_speed(t: list[float], a: list[float], t0: float, t1: float) -> tuple[float | None, list[tuple[float, float]]]:
    """t0~t1 구간 가속도(m/s²)를 적분한 속도. 끝에서 정지(0)라고 보고 선형 드리프트를 뺍니다."""
    idx = [i for i in range(len(t)) if t0 <= t[i] <= t1]
    if len(idx) < 10:
        return None, []
    vel = [0.0]
    for j in range(1, len(idx)):
        i0, i1 = idx[j - 1], idx[j]
        vel.append(vel[-1] + 0.5 * (a[i0] + a[i1]) * (t[i1] - t[i0]))
    drift = vel[-1]
    span = t[idx[-1]] - t[idx[0]] or 1.0
    vel = [v - drift * (t[i] - t[idx[0]]) / span for v, i in zip(vel, idx)]
    peak = max(vel, key=abs)
    step = max(1, len(idx) // 150)
    return abs(peak), [(round(t[i], 3), round(v, 4)) for v, i in list(zip(vel, idx))[::step]]


def symmetry_analysis(cols: dict[str, list[float]], bias: list[float] | None, vertical_axis: str, phases: dict[str, tuple[float, float]]) -> dict[str, Any]:
    vi = "xyz".index(vertical_axis or "z")
    axes = [i for i in range(3) if i != vi]
    keys = ["ax_g", "ay_g", "az_g"]
    t = cols["t_s"]
    b = bias or [0.0, 0.0, 0.0]
    f0, f1 = phases["forward"]
    best = None
    for i in axes:
        seg = [cols[keys[i]][k] - b[i] for k in range(len(t)) if f0 <= t[k] <= f1]
        var = sum(x * x for x in seg) / len(seg) if seg else 0
        if best is None or var > best[1]:
            best = (i, var)
    axis = best[0] if best else axes[0]
    acc = [(cols[keys[axis]][k] - b[axis]) * 9.80665 for k in range(len(t))]
    out: dict[str, Any] = {"axis": "xyz"[axis]}
    for name, (p0, p1) in phases.items():
        peak, series = _integrate_speed(t, acc, p0, p1 + 0.6)
        out[name] = {"peak_speed_mps": None if peak is None else round(peak, 4), "series": series}
    f, r = out["forward"]["peak_speed_mps"], out["backward"]["peak_speed_mps"]
    out["ratio_backward_to_forward"] = round(r / f, 3) if f and r else None
    return out


async def _symmetry(body: SymmetryRequest) -> None:
    ctl = control()
    simulated = ctl.adapter.mode != "hardware"
    gen = ctl.stop_generation
    commands: dict[str, Any] = {}
    try:
        if not simulated:
            st = await asyncio.to_thread(mpu6050_service.start_recording)
            if st.get("error") or not st.get("recording"):
                raise RuntimeError(st.get("error") or "MPU-6050 기록을 시작하지 못했습니다.")
        symmetry_runner.message = "정지 기록"
        await _wait(ctl, 0.6, gen)
        t_start = time.monotonic()
        phases: dict[str, tuple[float, float]] = {}
        for direction in ("forward", "backward"):
            symmetry_runner.message = "전진" if direction == "forward" else "후진"
            p0 = time.monotonic() - t_start + 0.6
            try:
                await _drive_for(ctl, body.output_mode, body.output_value, body.seconds, direction, gen, False, 0.0)
                commands[direction] = getattr(ctl.adapter, "last_command", None)
            finally:
                await ctl.stop(reason="symmetry_test")
            gen = ctl.stop_generation
            phases[direction] = (p0, time.monotonic() - t_start + 0.6)
            symmetry_runner.message = "정지 대기"
            await _wait(ctl, 1.0, gen)
        if simulated:
            result = {"axis": "x", "forward": {"peak_speed_mps": None, "series": []}, "backward": {"peak_speed_mps": None, "series": []},
                      "ratio_backward_to_forward": None, "note": "모의 모드에서는 가속도 적분 결과가 없습니다. 실제 차량에서 실행하세요."}
        else:
            await asyncio.to_thread(mpu6050_service.stop_recording)
            meta = await asyncio.to_thread(mpu6050_service.recording_columns)
            result = symmetry_analysis(meta["columns"], meta.get("bias_g"), meta.get("vertical_axis") or "z", phases)
        result["commands"] = commands
        result["request"] = body.model_dump()
        result["drive_settings"] = getattr(ctl, "drive_settings", None)
        ratio = result.get("ratio_backward_to_forward")
        if ratio:
            trims = (ctl.drive_settings or {}) if hasattr(ctl, "drive_settings") else {}
            out = body.output_value
            if ratio > 1.05:
                # 후진이 빠름 → 전진(느린 쪽)을 먼저 올림. 출력 상한(100)에 걸리면 후진을 낮춤.
                tf = trims.get("trim_forward", 1.0) * ratio
                if tf <= 1.5 and out * tf <= 100:
                    result["suggestion"] = {"trim_forward": round(tf, 3),
                                            "text": f"후진이 전진보다 약 {ratio:.2f}배 빠릅니다. 전진 출력 배율을 {tf:.2f}로 올리면 맞춰집니다(오르막을 넘는 힘도 커짐)."}
                else:
                    result["suggestion"] = {"trim_backward": round(max(0.5, trims.get("trim_backward", 1.0) / ratio), 3),
                                            "text": f"후진이 전진보다 약 {ratio:.2f}배 빠릅니다. 전진은 이미 최대라 후진 출력 배율을 낮춥니다."}
            elif ratio < 0.95:
                tb = trims.get("trim_backward", 1.0) / ratio
                if tb <= 1.5 and out * tb <= 100:
                    result["suggestion"] = {"trim_backward": round(tb, 3),
                                            "text": f"전진이 후진보다 약 {1 / ratio:.2f}배 빠릅니다. 후진 출력 배율을 {tb:.2f}로 올리면 맞춰집니다."}
                else:
                    result["suggestion"] = {"trim_forward": round(max(0.5, trims.get("trim_forward", 1.0) * ratio), 3),
                                            "text": f"전진이 후진보다 약 {1 / ratio:.2f}배 빠릅니다. 후진은 이미 최대라 전진 출력 배율을 낮춥니다."}
            else:
                result["suggestion"] = {"text": "전진과 후진 속도 차이가 5% 이내입니다."}
        symmetry_runner.result = result
        symmetry_runner.state, symmetry_runner.message = "done", "점검 완료"
    except Aborted:
        symmetry_runner.state, symmetry_runner.message = "aborted", "정지 명령으로 중단했습니다."
    except Exception as exc:
        symmetry_runner.state, symmetry_runner.message = "error", f"점검 실패: {exc}"
    finally:
        if not simulated:
            await asyncio.to_thread(mpu6050_service.stop_recording)


class BalanceRequest(BaseModel):
    output_mode: Literal["pwm", "duty"] = "pwm"
    output_value: float = Field(default=70, gt=0, le=100)
    seconds: float = Field(default=1.5, ge=0.6, le=4)
    direction: Literal["forward", "backward"] = "forward"
    auto: bool = False
    max_tries: int = Field(default=5, ge=1, le=8)


class BalanceRunner:
    def __init__(self) -> None:
        self.state, self.message = "idle", ""
        self.result: dict[str, Any] | None = None
        self.trials: list[dict[str, Any]] = []

    @property
    def busy(self) -> bool:
        return self.state == "running"


balance_runner = BalanceRunner()


def _yaw_rate(log: list[list[float]], seconds: float) -> float | None:
    """주행 기록 [t, 출력, 조향, 방향°]에서 뒤쪽 60% 구간의 방향 변화율(°/s, 반시계 +)을 최소제곱으로."""
    pts = [(r[0], r[3]) for r in log if r[3] is not None and r[0] >= 0.4 * seconds]
    if len(pts) < 5:
        return None
    n = len(pts)
    mt = sum(p[0] for p in pts) / n
    mh = sum(p[1] for p in pts) / n
    den = sum((p[0] - mt) ** 2 for p in pts)
    return sum((p[0] - mt) * (p[1] - mh) for p in pts) / den if den > 0 else None


def _balance_to_trims(b: float) -> tuple[float, float]:
    """균형값 b(+면 왼쪽을 줄임, −면 오른쪽을 줄임) → (왼쪽, 오른쪽) 배율. 빠른 쪽만 낮춰 최대 출력은 그대로 둡니다."""
    return round(1.0 - max(0.0, b), 3), round(1.0 - max(0.0, -b), 3)


async def _balance(body: BalanceRequest) -> None:
    """직진 보정 없이 조향 0으로 달려 차가 도는 속도(자이로)를 재고, 0이 되도록 좌우 바퀴 배율을 맞춥니다.

    좌우 바퀴 속도가 다르면 차가 돌므로 회전 속도 ω ∝ (오른쪽 − 왼쪽). 할선법으로 ω(b) = 0인 b를 찾습니다(부호 가정 없음).
    한 번 달릴 때마다 반대 방향으로 같은 시간 돌아와 출발 위치 근처로 복귀합니다.
    """
    ctl = control()
    simulated = ctl.adapter.mode != "hardware"
    ds = ctl.drive_settings
    b0 = (1.0 - ds.get("trim_left", 1.0)) - (1.0 - ds.get("trim_right", 1.0))
    tries = body.max_tries if body.auto else 1
    hist: list[tuple[float, float]] = []
    balance_runner.trials = []
    back = "backward" if body.direction == "forward" else "forward"
    try:
        b = b0
        for i in range(tries):
            tl, tr = _balance_to_trims(b)
            ctl.save_drive_settings({"trim_left": tl, "trim_right": tr})
            balance_runner.message = f"{i + 1}번째 점검: 정지 기록(움직이지 마세요)"
            gen = ctl.stop_generation
            await _wait(ctl, 0.8, gen)
            track = not simulated and mpu6050_service.heading_reset()
            balance_runner.message = f"{i + 1}번째 점검: 조향 0으로 곧게 주행 (왼쪽 ×{tl}, 오른쪽 ×{tr})"
            try:
                info = await _drive_for(ctl, body.output_mode, body.output_value, body.seconds, body.direction, gen, False, 0.0, track=track)
            finally:
                await ctl.stop(reason="balance_test")
            w = _yaw_rate(info.get("drive_log") or [], body.seconds) if track else None
            trial = {"try": i + 1, "trim_left": tl, "trim_right": tr, "yaw_dps": None if w is None else round(w, 2),
                     "heading_end_deg": info.get("heading_end_deg")}
            balance_runner.trials.append(trial)
            gen = ctl.stop_generation
            await _wait(ctl, 0.4, gen)
            balance_runner.message = f"{i + 1}번째 점검: 출발 위치로 돌아가는 중"
            try:
                await _drive_for(ctl, body.output_mode, body.output_value, body.seconds, back, gen, False, 0.0, track=False)
            finally:
                await ctl.stop(reason="balance_return")
            if w is None:
                break
            hist.append((b, w))
            if abs(w) <= 1.5:
                break
            # 기울기(배율을 바꿨을 때 회전이 얼마나 바뀌는지)는 회전 차이가 뚜렷한 두 점으로만 구합니다(출력 반올림에 따른 흔들림 방지).
            slope = None
            for (ba, wa), (bb, wb) in ((hist[j], hist[k]) for j in range(len(hist)) for k in range(j + 1, len(hist))):
                if abs(wb - wa) >= 2.0 and abs(bb - ba) > 1e-6:
                    cand = (wb - wa) / (bb - ba)
                    slope = cand if slope is None or abs(bb - ba) > 0 else slope
            if slope is None:
                b_next = b + 0.04  # 첫 걸음: 왼쪽을 4% 줄여 보고 회전이 어떻게 바뀌는지 봄
            else:
                step = -w / slope
                b_next = b + max(-0.08, min(0.08, step))
            b = max(-0.35, min(0.35, b_next))
        best = min(hist, key=lambda x: abs(x[1])) if hist else None
        if best is not None:
            tl, tr = _balance_to_trims(best[0])
            ctl.save_drive_settings({"trim_left": tl, "trim_right": tr})
        last = balance_runner.trials[-1] if balance_runner.trials else {}
        w = best[1] if best else None
        side = None if w is None else ("오른쪽 바퀴가 빠름(왼쪽으로 돎)" if w > 0 else "왼쪽 바퀴가 빠름(오른쪽으로 돎)")
        balance_runner.result = {
            "trials": balance_runner.trials, "best_yaw_dps": None if w is None else round(w, 2),
            "trim_left": ctl.drive_settings.get("trim_left"), "trim_right": ctl.drive_settings.get("trim_right"),
            "text": ("모의 모드라 회전을 잴 수 없습니다." if simulated and not hist else
                     "자이로 방향을 읽지 못했습니다(센서 보정 확인)." if w is None else
                     f"회전 {w:+.1f}°/s로 거의 곧게 갑니다." if abs(w) <= 1.5 else
                     f"회전 {w:+.1f}°/s · {side}. {'자동으로 더 맞추기를 눌러 보세요.' if not body.auto else '최대 횟수까지 맞췄습니다. 조향 중앙(설정 → 조향 보정)도 확인하세요.'}"),
            "direction": body.direction, "last": last,
        }
        balance_runner.state, balance_runner.message = "done", "점검 완료"
    except Aborted:
        balance_runner.state, balance_runner.message = "aborted", "정지 명령으로 중단했습니다."
    except Exception as exc:
        balance_runner.state, balance_runner.message = "error", f"점검 실패: {exc}"
    finally:
        mpu6050_service.heading_stop()
        try:
            await ctl.stop(reason="balance_end")
        except Exception:
            pass


@router.post("/tools/balance")
async def balance_start(body: BalanceRequest):
    ctl = control()
    if auto_runner.busy or symmetry_runner.busy or balance_runner.busy:
        raise HTTPException(409, "다른 주행이 진행 중입니다.")
    if not ctl.adapter.connected:
        raise HTTPException(409, ctl.adapter.connection_error or "PiCar-X가 연결되어 있지 않습니다.")
    if ctl.adapter.mode == "hardware":
        st = await asyncio.to_thread(mpu6050_service.status)
        if not st.get("calibrated"):
            raise HTTPException(409, "센서 화면에서 MPU-6050 정지 상태 보정을 먼저 하세요.")
    balance_runner.state, balance_runner.message, balance_runner.result = "running", "시작", None
    asyncio.create_task(_balance(body))
    return {"state": balance_runner.state, "message": balance_runner.message}


@router.get("/tools/balance")
async def balance_status():
    return {"state": balance_runner.state, "message": balance_runner.message, "result": balance_runner.result,
            "trials": balance_runner.trials}


@router.post("/tools/symmetry")
async def symmetry_start(body: SymmetryRequest):
    ctl = control()
    if auto_runner.busy or symmetry_runner.busy or balance_runner.busy:
        raise HTTPException(409, "다른 주행이 진행 중입니다.")
    if not ctl.adapter.connected:
        raise HTTPException(409, ctl.adapter.connection_error or "PiCar-X가 연결되어 있지 않습니다.")
    if ctl.adapter.mode == "hardware":
        st = await asyncio.to_thread(mpu6050_service.status)
        if not st.get("calibrated"):
            raise HTTPException(409, "센서 화면에서 MPU-6050 정지 상태 보정을 먼저 하세요.")
    symmetry_runner.state, symmetry_runner.message, symmetry_runner.result = "running", "시작", None
    asyncio.create_task(_symmetry(body))
    return {"state": symmetry_runner.state, "message": symmetry_runner.message}


@router.get("/tools/symmetry")
async def symmetry_status():
    return {"state": symmetry_runner.state, "message": symmetry_runner.message, "result": symmetry_runner.result}
