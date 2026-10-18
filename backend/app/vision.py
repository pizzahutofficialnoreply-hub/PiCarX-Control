"""카메라로 방지턱 미리 보기(실험 기능, 학습 없이 '바닥과 다른 색' 찾기).

원리
  1) 보정: 방지턱이 화면에 없을 때(출발선에서) 화면 아래쪽 관심 영역(ROI)의 바닥 색 평균·흩어짐을 저장합니다.
  2) 감지: 주행 중 같은 영역에서 '바닥 색과 크게 다른 픽셀'의 비율(점수)을 구합니다.
     가운데 유도선(검은 테이프)은 빼고, 왼쪽·오른쪽 영역을 따로 봅니다(방지턱은 유도선 양옆에 있음).
  3) 왼쪽·오른쪽 점수가 모두 기준을 넘는 프레임이 2번 연속이면 '방지턱이 앞에 있다'고 판단합니다.
기계 학습(신경망)을 쓰려면 방지턱 사진 수백 장과 학습이 필요하고 라즈베리파이에서 느립니다. 여기서는 바닥과
방지턱의 색이 뚜렷이 다른(나무 바닥 vs 회색·노란 방지턱) 실험 환경을 이용한 간단한 방법을 씁니다.
카메라 각도(틸트)를 바꾸면 ROI 위치와 보정을 다시 해야 합니다.
"""
from __future__ import annotations

import threading
import time
from typing import Any

from pydantic import BaseModel, Field


class VisionSettings(BaseModel):
    floor_mean: list[float] | None = Field(default=None, description="보정한 바닥 색 평균 [R, G, B]")
    floor_std: float | None = Field(default=None, description="보정한 바닥 색 흩어짐")
    roi_top: float = Field(default=0.55, ge=0.0, le=0.95, description="관심 영역 위쪽(화면 높이 비율)")
    roi_bottom: float = Field(default=0.9, ge=0.05, le=1.0, description="관심 영역 아래쪽")
    center_gap: float = Field(default=0.2, ge=0.0, le=0.8, description="가운데(유도선) 빼는 폭(화면 너비 비율)")
    k_sigma: float = Field(default=4.0, ge=1.5, le=10, description="바닥 색에서 이 배수 넘게 다르면 '바닥 아님'")
    threshold: float = Field(default=0.12, ge=0.01, le=0.9, description="왼쪽·오른쪽 영역에서 '바닥 아님' 비율이 이 이상이면 방지턱")


def _regions(arr: Any, s: VisionSettings):
    h, w = arr.shape[0], arr.shape[1]
    y0, y1 = int(h * s.roi_top), max(int(h * s.roi_top) + 1, int(h * s.roi_bottom))
    gap = int(w * s.center_gap / 2)
    mid = w // 2
    return arr[y0:y1, : max(1, mid - gap), :3], arr[y0:y1, min(w - 1, mid + gap):, :3]


def calibrate(arr: Any, s: VisionSettings) -> VisionSettings:
    import numpy as np
    left, right = _regions(arr, s)
    px = np.concatenate([left.reshape(-1, 3), right.reshape(-1, 3)]).astype(float)
    mean = px.mean(axis=0)
    dist = np.sqrt(((px - mean) ** 2).sum(axis=1))
    return s.model_copy(update={"floor_mean": [round(float(x), 2) for x in mean], "floor_std": round(float(max(4.0, dist.std() + dist.mean())), 2)})


def score(arr: Any, s: VisionSettings) -> tuple[float, float] | None:
    """(왼쪽, 오른쪽) 영역에서 바닥과 다른 픽셀 비율."""
    if s.floor_mean is None or s.floor_std is None:
        return None
    import numpy as np
    mean = np.array(s.floor_mean, dtype=float)
    out = []
    for region in _regions(arr, s):
        px = region.reshape(-1, 3).astype(float)
        if not len(px):
            return None
        d = np.sqrt(((px - mean) ** 2).sum(axis=1))
        out.append(float((d > s.k_sigma * s.floor_std / 2).mean()))
    return out[0], out[1]


class BumpCamera:
    """주행 중 별도 스레드에서 약 10번/초 프레임을 보고 방지턱이 보이면 ``seen_at``(단조 시각)을 기록합니다."""

    def __init__(self, capture, s: VisionSettings, hz: float = 10.0) -> None:
        self.capture, self.s, self.period = capture, s, 1.0 / hz
        self.seen_at: float | None = None
        self.scores: list[tuple[float, float, float]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True, name="bump-camera")
        self._thread.start()

    def _loop(self) -> None:
        hits = 0
        while not self._stop.is_set():
            t = time.monotonic()
            arr = self.capture()
            sc = score(arr, self.s) if arr is not None else None
            if sc is not None:
                self.scores.append((round(t, 3), round(sc[0], 3), round(sc[1], 3)))
                hits = hits + 1 if min(sc) >= self.s.threshold else 0
                if hits >= 2 and self.seen_at is None:
                    self.seen_at = t
            self._stop.wait(max(0.0, self.period - (time.monotonic() - t)))

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)


# --------------------------------------------------------------------------- 카메라로 바닥 테이프 보기
class LineCamSettings(BaseModel):
    tilt_deg: float = Field(default=-30, ge=-45, le=20, description="코스 주행 중 카메라 틸트(아래로 −). 바닥 테이프가 화면 아래쪽에 보이게")
    roi_top: float = Field(default=0.45, ge=0.0, le=0.9, description="화면에서 바닥으로 볼 영역 위쪽(높이 비율)")
    flip: bool = Field(default=False, description="화면 좌우가 반대로 찍히면 켬")
    min_contrast: float = Field(default=40, ge=10, le=200, description="밝은 바닥과 검은 테이프 밝기 차이가 이보다 작으면 판단 안 함")
    cross_frac: float = Field(default=0.55, ge=0.3, le=0.95, description="한 줄(띠)에서 검은 부분이 화면 너비의 이 비율 넘으면 가로선")
    cross_wait_s: float = Field(default=0.5, ge=0.1, le=2.0, description="가로선이 화면 아래로 사라진 뒤 바닥 센서가 이 시간 안에 못 보면 카메라 판단으로 정지선 처리")


def line_scan(arr: Any, s: LineCamSettings) -> dict[str, Any] | None:
    """밝은 바닥 + 검은 테이프 화면에서 유도선 위치(−1 왼쪽 ~ +1 오른쪽)와 가로선 위치(0 위 ~ 1 아래)를 찾습니다."""
    import numpy as np
    if arr is None:
        return None
    a = np.asarray(arr, dtype=float)
    gray = a[..., :3].mean(axis=2) if a.ndim == 3 else a
    if s.flip:
        gray = gray[:, ::-1]
    h, w = gray.shape
    roi = gray[int(h * s.roi_top):, :]
    if roi.size == 0:
        return None
    lo, hi = np.percentile(roi, 5), np.percentile(roi, 90)
    if hi - lo < s.min_contrast:
        return {"guide": None, "guide_far": None, "cross_y": None, "contrast": round(float(hi - lo), 1)}
    dark = roi < (lo + hi) / 2
    nb = 8
    bands = np.array_split(np.arange(dark.shape[0]), nb)
    cross_y = None
    guide_rows = []
    for i, rows in enumerate(bands):
        if not len(rows):
            continue
        frac = dark[rows].mean()
        if frac >= s.cross_frac:
            cross_y = (i + 0.5) / nb       # 가장 아래쪽(가까운) 가로선 위치가 남음
        else:
            guide_rows.append(i)

    def centroid(idx: list[int]) -> float | None:
        if not idx:
            return None
        rows = np.concatenate([bands[i] for i in idx])
        col = dark[rows].mean(axis=0)            # 열마다 검은 비율
        cols = np.where(col > 0.5)[0]
        if len(cols) < max(1, w * 0.01) or len(cols) > w * 0.4:
            return None
        return float(cols.mean() / max(1, w - 1) * 2 - 1)

    near = [i for i in guide_rows if i >= nb // 2]
    far = [i for i in guide_rows if i < nb // 2]
    return {"guide": centroid(near), "guide_far": centroid(far), "cross_y": cross_y, "contrast": round(float(hi - lo), 1)}


class LineCam:
    """주행 중 별도 스레드에서 프레임을 보고(약 10번/초) 유도선 위치와 가로선 지나감을 기록합니다."""

    def __init__(self, capture, s: LineCamSettings, hz: float = 12.0) -> None:
        self.capture, self.s, self.period = capture, s, 1.0 / hz
        self.latest: dict[str, Any] | None = None
        self.latest_t = 0.0
        self.cross_near_t: float | None = None     # 가로선이 화면 아래쪽(가까움)에 있었던 마지막 시각
        self.cross_passed_at: float | None = None  # 가로선이 화면 아래로 사라진(차 밑으로 들어간) 시각
        self.frames = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> "LineCam":
        self._thread = threading.Thread(target=self._loop, daemon=True, name="line-camera")
        self._thread.start()
        return self

    def feed(self, res: dict[str, Any] | None, t: float) -> None:
        if res is None:
            return
        self.latest, self.latest_t = res, t
        self.frames += 1
        cy = res.get("cross_y")
        if cy is not None and cy >= 0.7:
            self.cross_near_t = t
        elif self.cross_near_t is not None and cy is None and t - self.cross_near_t < 0.6:
            self.cross_passed_at = t
            self.cross_near_t = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            t = time.monotonic()
            try:
                self.feed(line_scan(self.capture(), self.s), t)
            except Exception:
                pass
            self._stop.wait(max(0.0, self.period - (time.monotonic() - t)))

    def guide(self, now: float | None = None) -> float | None:
        now = time.monotonic() if now is None else now
        if self.latest is None or now - self.latest_t > 0.4:
            return None
        return self.latest.get("guide")

    def reset_cross(self) -> None:
        self.cross_near_t = self.cross_passed_at = None

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
