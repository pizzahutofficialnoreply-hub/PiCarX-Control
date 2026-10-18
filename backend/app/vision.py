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
