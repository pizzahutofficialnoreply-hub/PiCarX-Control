# Raspberry Pi 설치 안내

## Windows PC에서 시뮬레이션 미리보기

실제 Raspberry Pi 없이 UI를 확인하려면 프로젝트의 frontend 폴더에서 실행합니다. 백엔드는 실행하지 않아도 됩니다.

1. cd frontend
2. npm install
3. npm run dev

브라우저에서 http://localhost:5173을 엽니다. 화면 상단의 시뮬레이션 모드 표시를 확인하세요. 전후진과 조향은 브라우저 안의 가상 상태만 바꾸며 네트워크로 주행 명령을 보내지 않습니다. 시뮬레이션 센서 수치는 실제 센서 측정값이 아닙니다.

실제 차량을 연결하려면 설정에서 제어 모드를 실제 차량으로 전환한 다음, 아래에서 실행할 Raspberry Pi의 주소를 입력합니다.

초보자를 위한 개발 서버 기준 안내입니다. 명령은 Raspberry Pi OS 터미널에서 실행합니다. PiCar-X 실제 제어는 드라이버 어댑터 검증 전까지 비활성 상태이며 기본 모의 모드만 실행합니다.

## 1. 운영체제와 네트워크

1. Raspberry Pi와 스마트폰/PC를 같은 Wi-Fi에 연결합니다.
2. Pi에서 터미널을 열고 `hostname -I`를 실행합니다. 표시되는 로컬 IP를 메모합니다.
3. 시스템 패키지 목록을 갱신합니다.

```bash
sudo apt update
```

4. Python과 빌드 도구를 설치합니다.

```bash
sudo apt install -y python3 python3-venv python3-pip nodejs npm
```

Node.js 20 이상인지 `node --version`으로 확인합니다. OS 저장소 버전이 낮으면 Raspberry Pi OS의 권장 방식으로 Node.js를 업데이트하세요.

## 2. 프로젝트 복사 및 백엔드

프로젝트를 `/home/pi/PiCarX-Control`에 복사한 뒤 다음을 실행합니다.

```bash
cd /home/pi/PiCarX-Control/backend

source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
PICARX_MODE=mock uvicorn app.main:app --host 0.0.0.0 --port 8000
```

브라우저에서 `http://<메모한-IP>:8000/docs`에 접속하면 API 문서를 확인할 수 있습니다. 터미널에서 `Ctrl+C`로 서버를 종료합니다.

## 3. 프론트엔드

다른 터미널에서 실행합니다.

```bash
cd /home/pi/PiCarX-Control/frontend
npm install
npm run dev -- --host 0.0.0.0
```

PC 또는 스마트폰 브라우저에서 `http://<메모한-IP>:5173`으로 접속합니다. 설정 화면에서 백엔드 주소 `http://<메모한-IP>:8000`을 입력하고 연결 테스트를 누릅니다.

## 프로덕션 정적 UI 제공

프로젝트의 frontend 폴더에서 npm run build를 실행하면 빌드 파일이 frontend/dist에 생성됩니다. 빌드 산출물이 있으면 FastAPI가 같은 서버의 /에서 UI를 제공합니다. Raspberry Pi에서 백엔드 실행 후 http://<메모한-IP>:8000으로 접속합니다. 개발 중에는 Vite와 FastAPI를 별도 프로세스로 실행할 수 있습니다.

## 4. systemd 자동 시작 예시

자동 시작은 시스템 설정을 변경합니다. 아래는 예시 파일이며, 프로젝트 경로와 사용자 계정을 확인한 뒤 직접 설치해야 합니다.

```bash
sudo cp /home/pi/PiCarX-Control/backend/picarx-control.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now picarx-control
sudo systemctl status picarx-control
```

로그 확인: `sudo journalctl -u picarx-control -f`. 현재 서비스 파일은 백엔드만 시작합니다. 프론트엔드 정적 배포/서비스는 별도 구성해야 합니다.

## Phase 5 하드웨어 점검 순서

실제 차량 모드로 바꾸기 전에 PiCar-X를 고정하고 네 바퀴를 바닥에서 띄우세요. 서버 진단에서 라이브러리 import, 제어 API, 드라이버 초기화를 확인한 뒤 다음 순서로 시험합니다.

1. 서버 연결 및 하드웨어 모드를 확인합니다.
2. 조향 중앙값을 저장하고 정지 상태에서 좌우 조향을 짧게 확인합니다.
3. 전진·후진 PWM 제한을 낮게 설정하고 짧게 눌러 모터 방향을 확인합니다.
4. 손을 놓아 즉시 정지하는지 확인하고, 긴급 정지도 별도로 시험합니다.
5. 센서 페이지를 새로고침해 거리·그레이스케일·절벽 API 응답을 확인합니다.
6. 카메라 영상 스트리밍은 현재 미지원이며 장치 연결 여부도 자동 확인하지 않습니다.

표시된 API 존재 및 값 응답은 센서 정확도나 모터·서보의 물리 작동을 검증한 것이 아닙니다. 첫 주행 전 배선, 바퀴 회전 방향, 조향 방향을 직접 확인하세요.

## 5. SunFounder hardware driver and real mode

The adapter uses only the method signatures reported for the library installed on your Raspberry Pi. Its version is intentionally not assumed. If the package is missing, install it by following the [current official SunFounder installation guide](https://docs.sunfounder.com/projects/picar-x-v20/en/latest/python/install_all_modules.html) for your PiCar-X model.

For Raspberry Pi OS Lite, install its stated prerequisites first:

```bash
sudo apt install git python3-pip python3-setuptools python3-smbus
# 방지턱 실험(MPU-6050 1 kHz)용: /boot/firmware/config.txt 에 dtparam=i2c_arm_baudrate=400000 추가 후 재부팅
```

If the library is missing, follow the linked current official installation guide for the PiCar-X model and OS. Upstream maintains its dependency versions and commands; this project does not pin or assume the installed PiCar-X release.

Create a separate backend venv with `--system-site-packages` so it can import the official system-installed `picarx` module. This preserves the existing mock-mode `.venv`. Then start the API in hardware mode:
```bash
cd /home/pi/PiCarX-Control/backend
python3 -m venv --system-site-packages .venv-hardware
source .venv-hardware/bin/activate
python -m pip install -r requirements.txt
python -c "import sys, picarx, picarx.picarx; print(sys.version); print(picarx.__file__); print(picarx.picarx.__file__)"
# This checks import paths only; it does not initialize motors or servos.
PICARX_MODE=hardware .venv-hardware/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```
The API checks required method names before initializing the board. If import, GPIO, I2C, or initialization fails, the server stays available and reports `mode: hardware`, `connected: false`, and `connection_error`; movement requests are refused. A successful Picarx initialization confirms driver initialization only. It cannot verify that the physical chassis, motor wiring, or wheels behave correctly.
For systemd, set `Environment=PICARX_MODE=hardware` and change `ExecStart` to `/home/pi/PiCarX-Control/backend/.venv-hardware/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000`. Keep the default service in mock mode until the raised-wheel checks pass. Then reload and restart the service.
Before driving, follow SunFounder's [servo adjustment instructions](https://docs.sunfounder.com/projects/picar-x-v20/en/latest/python/py_servo_adjust.html), raise the wheels, and keep a physical power cutoff within reach. Start at low PWM and verify forward, reverse, steering direction, stop, and watchdog behavior. The web UI cannot replace this physical check.


##
