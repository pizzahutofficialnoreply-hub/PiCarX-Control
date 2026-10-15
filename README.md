# PiCarX-Control

Raspberry Pi에서 실행하는 로컬 PiCar-X 제어 플랫폼의 2차 개발 단계입니다. React + TypeScript 모바일 우선 웹앱과 FastAPI 백엔드, 모의 하드웨어 어댑터 및 안전 watchdog을 포함합니다.

> 기본 실행 모드는 **모의 모드**이며, 하드웨어 모드는 `PICARX_MODE=hardware`로 선택합니다. 어댑터는 설치된 라이브러리의 버전을 가정하지 않고 확인된 API 메서드만 호출합니다. 초기화 실패 시 주행은 거부되며 진단 화면에 원인을 표시합니다.

## 구성

- `frontend/`: React, TypeScript, Vite 제어 UI
- `backend/app/`: FastAPI, 입력 모델, 안전 제어 서비스, 하드웨어 어댑터
- `backend/picarx-control.service`: Raspberry Pi systemd 예시
- `docs/`: 설치, 구조, API, 안전 문서

## 빠른 실행

### PC에서 UI 미리보기 (기본 시뮬레이션)

실제 차량이나 백엔드 없이 프론트엔드만 실행할 수 있습니다. 최초 실행은 브라우저 내부 시뮬레이션 모드이며, **설정 → 제어 모드**에서 실제 차량 모드로 바꾸기 전에는 백엔드 주소로 요청하지 않습니다. 화면에는 시뮬레이션 배지가 계속 표시됩니다.

Windows PowerShell 또는 macOS/Linux 터미널에서 실행합니다.

1. frontend 폴더로 이동합니다: cd frontend
2. 패키지를 설치합니다: npm install
3. 개발 서버를 시작합니다: npm run dev

브라우저에서 http://localhost:5173을 엽니다. 가상 전후진, 조향, PWM, 센서 화면과 페이지 이동을 확인할 수 있습니다. 시뮬레이션 센서 숫자는 실제 측정값이 아닙니다. 카메라는 영상 대신 연결 대기 화면을 표시합니다.

### 실제 Raspberry Pi 연결

기본 설정은 모의 모드입니다. FastAPI를 실행한 뒤 앱의 **설정 → 제어 모드**에서 실제 차량을 선택하고, 서버 주소(예: http://192.168.0.120:8000)를 입력해 연결 테스트를 누릅니다. 실제 차량 모드 전환 시 확인 대화상자가 표시됩니다.

### 빌드 및 FastAPI 정적 제공

frontend 폴더에서 npm run build를 실행합니다. 프로젝트 루트의 frontend/dist가 존재하면 FastAPI가 해당 파일을 /에서 제공합니다. 그 다음 백엔드를 실행하고 http://<서버-IP>:8000으로 접속합니다. 개발 중에는 Vite와 FastAPI를 각각 실행해도 됩니다.

Python 3.10 이상과 Node.js 20 이상이 필요합니다.

### 백엔드

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# Raspberry Pi/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 프론트엔드 (별도 터미널)

```bash
cd frontend
npm install
npm run dev -- --host 0.0.0.0
```

PC에서는 `http://localhost:5173`, 스마트폰에서는 같은 Wi-Fi의 Raspberry Pi 주소 `http://<라즈베리파이-IP>:5173`에 접속합니다. 설정의 서버 주소는 `http://<라즈베리파이-IP>:8000`으로 지정합니다.

운영에서는 프론트엔드를 빌드해 정적 호스팅하는 배포 구성을 추가하는 것이 좋습니다. 개발 서버는 초기 사용용입니다.

## 조작 및 안전

- 주행 버튼은 누르는 동안만 명령을 반복 전송하도록 설계되어야 하며, 서버 watchdog이 0.5초간 명령 갱신이 없으면 정지합니다.
- 정지 API는 즉시 속도/조향 상태를 0으로 만들고 하드웨어 stop을 호출합니다.
- 속도 0–60%, 조향 입력 0–35°로 백엔드에서 검증합니다.
- `PICARX_MODE=hardware` selects the verified adapter; driver initialization failure keeps status endpoints online and rejects movement. The API reports initialization state, not proof that every motor and wire works.
- API는 현재 인증이 없습니다. 신뢰하는 로컬 Wi-Fi에서만 사용하세요.

## 문서

- [설치 안내](docs/INSTALL.md)
- [아키텍처](docs/ARCHITECTURE.md)
- [API 명세](docs/API.md)
- [안전 안내](docs/SAFETY.md)
- [방지턱 충격 실험 기능](docs/EXPERIMENT.md)

## 방지턱 충격 실험 (MPU-6050)

**실험 관리** 화면에서 조건을 입력하고 *자동 측정 주행*을 누르면, 차량이 정지 기록 → 직진 → 정지 순으로 움직이며 MPU-6050 수직 가속도를 약 1 kHz로 기록합니다. 시행마다 앞·뒤 바퀴 피크 간격으로 통과 속도를 계산하고, 지면 이탈·착지 충격·충격력·Π를 함께 구합니다. 조건별 절사평균과 회귀식 Δa = k₀·vⁿ·mᵖ, 허용 가속도에 대한 최대 통과 속도 예측을 보여 주며, 그래프는 PNG/SVG, 표는 CSV로 저장합니다. 기록은 Raspberry Pi의 `backend/data/experiments/`에 저장됩니다. 자세한 내용은 [docs/EXPERIMENT.md](docs/EXPERIMENT.md)를 보세요.

## Phase 5 기능 상태

- 조이스틱은 연속 조향각과 방향별 PWM 목표로 변환합니다. 가속은 100ms 간격으로 1 PWM씩 증가하며, 손을 놓거나 취소·긴급 정지하면 즉시 `/api/stop`을 호출합니다.
- `GET /api/sensors`는 확인된 PiCar-X 거리, 그레이스케일, 절벽 API를 읽습니다. 모의 응답은 `mode: mock`으로 구분되고 화면에도 시뮬레이션으로 표시됩니다.
- 카메라 Pan/Tilt 서보는 확인된 API를 통해 보정할 수 있습니다. 확인된 영상 스트리밍 API가 없어 카메라 영상은 아직 미지원입니다.
- 배터리와 실측 속도는 API/엔코더가 없어 확인 불가입니다. 목표 속도 입력은 보정된 PWM 환산값이 없으므로 모터 출력에 연결되지 않습니다.
- 하드웨어 테스트 전 바퀴를 들어 올리고, 설치 문서의 저출력 테스트 순서를 따르세요. API 응답은 물리 장치 정상 동작을 보장하지 않습니다.


##
