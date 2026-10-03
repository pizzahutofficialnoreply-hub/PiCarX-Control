# 시스템 구조

```text
모바일/PC 브라우저 (React + TypeScript)
  ├─ REST: /api/health, /api/status, /api/drive, /api/stop
  └─ WebSocket: /api/ws (상태 전달)
                         │ 로컬 Wi-Fi
                         ▼
FastAPI API → Pydantic 입력 검증 → ControlService/watchdog → HardwareAdapter
                                                               ├─ MockAdapter (기본)
                                                               └─ PiCarXAdapter (confirmed installed SunFounder API)
```

## 책임 분리

- `frontend/src/App.tsx`: 화면, 연결 설정, 명령 요청 및 상태 표시
- `backend/app/main.py`: HTTP/WebSocket 경계, 서비스 수명 주기 및 안전 감시
- `backend/app/models.py`: 명령 입력 범위 검사
- `backend/app/hardware.py`: 하드웨어 추상화와 모의 구현

정지 상태를 서버 초기화 때 설정하며 종료 때도 정지 호출합니다. 백엔드 watchdog은 마지막 구동 명령 뒤 0.5초 동안 새 명령이 없으면 정지합니다. API 라우터는 어댑터에 직접 접근하지 않고 ControlService를 거칩니다.

## 확장 지점

새 카메라, 초음파, 라인 센서, 주행 기록 기능은 별도 서비스와 API 모듈로 추가합니다. 프론트엔드의 카메라/센서/기록 메뉴는 현재 데이터가 없는 안내 화면입니다. 측정값이나 영상은 표시하지 않습니다.

실제 어댑터는 제공된 설치 API 메서드 목록만 호출하며, 라이브러리 버전을 가정하지 않습니다. 조향 및 Pan/Tilt 보정값은 `backend/data/settings.json`에 저장한 뒤 확인된 서보 위치 API로 적용합니다. 카메라 장치 자체는 확인하지 않으므로 진단 결과를 unknown으로 표시합니다. 드라이버 초기화 성공은 모터 배선이나 차체 동작을 검증했다는 뜻은 아닙니다.
