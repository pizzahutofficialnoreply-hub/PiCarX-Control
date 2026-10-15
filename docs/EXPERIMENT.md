# 방지턱 충격 실험 기능

창의력산출물 탐구 "질량과 속도에 따른 관성의 영향 분석 – 자동차가 방지턱 통과 시 충격을 최소화하는 속도"를 위한 기능입니다. 앱의 **실험 관리** 화면에서 조건 입력 → 자동 측정 주행 → 시행 그래프 확인 → 조건별 집계 → 관계식 도출까지 진행합니다.

## 측정 원리

| 항목 | 방법 |
|---|---|
| 수직 가속도 | MPU-6050, 약 1 kHz 목표, DLPF 184 Hz, ±8 g. 정지 보정 자세 기준으로 정지 = 1 g, 공중 = 약 0 g |
| 통과 속도 | 앞바퀴·뒷바퀴가 방지턱에 닿을 때 생기는 두 피크의 시간 간격 Δt와 축간거리 L로 `v = L / Δt` |
| 지면 이탈(점프) | 수직 가속도가 지면 이탈 기준(기본 0.2 g) 미만으로 5 ms 이상 유지된 구간 |
| 착지 피크 | 공중 구간 직후 30 ms 안의 피크. 바퀴 피크 짝짓기에서 제외해 속도 오계산을 막고, 착지 충격은 따로 기록 |
| 충격력 | `F = m · g · a_peak` (N). 증가분은 `m · g · Δa` |
| 무차원수 | `Π = v² / (g · R_eff)`, `R_eff = R + r`, `R = (w²/4 + h²) / (2h)`. Π = 1이 이론상 점프 경계 |
| 조건별 평균 | 같은 방지턱·질량·출력에서 3회 이상이면 최댓값·최솟값을 하나씩 빼고 평균 |
| 관계식 | 조건 평균에 대해 `log Δa = log k₀ + n log v + p log m` 최소제곱. `v_max = ((a_lim − 1) / (k₀ m^p))^(1/n)` |

## 사용 순서

1. 백엔드를 실제 하드웨어 모드로 실행하고(`PICARX_MODE=hardware`), 앱 설정에서 Raspberry Pi 주소로 연결합니다.
2. **센서** 화면에서 차량을 평평한 곳에 두고 *정지 상태 보정*을 누릅니다. 실제 표본 속도(Hz)가 500 이상인지 확인합니다.
3. **실험 관리 → 차량·분석 설정**에 축간거리, 바퀴 반지름, 기본 질량, 방지턱 높이·폭, 허용 최대 가속도를 입력하고 저장합니다.
4. **실험 조건**에서 방지턱, 추가 질량(0 / 150 / 300 g), 출력 방식과 출력값, 회차를 정하고 *자동 측정 주행*을 누릅니다.
   차는 정지 기록(기본 1 s) → 직진(기본 2.5 s) → 정지 후 기록(0.6 s) 순으로 움직이고, 끝나면 자동으로 분석·저장됩니다.
5. 시행 그래프에서 앞·뒤 바퀴 피크가 올바르게 잡혔는지 확인합니다. 이상한 시행은 *집계에서 제외*합니다.
6. 조건별 표, 그래프 1(속도–Δa), 그래프 2(Π–a_peak), 회귀식과 v_max 예측을 확인합니다. 그래프는 PNG(1920×1080)·SVG로, 표는 CSV로 저장합니다.

축간거리나 검출 기준을 나중에 바꾸면 *저장 후 전체 재분석*으로 저장된 원자료를 다시 계산할 수 있습니다.

## 출력 방식

- **라이브러리 PWM**: `picarx.forward(speed)`에 0~100을 그대로 전달합니다. 라이브러리가 내부에서 `speed/2 + 50`으로 바꾸므로 실제 듀티비는 약 50~100%이고, 출력 차이에 비해 속도 차이가 작습니다.
- **직접 듀티비**: `forward(1)`로 방향만 설정한 뒤 좌·우 뒷바퀴 PWM 핀(`motor_speed_pins`)에 듀티비를 직접 씁니다. 저속 구간까지 쓸 수 있어 속도 범위가 넓어집니다. 듀티가 너무 낮으면 모터가 출발하지 못하거나 방지턱 앞에서 멈출 수 있으니 10% 간격으로 내려가며 최저값을 찾으세요. 이 모드는 라이브러리의 모터 속도 보정값(`cali_speed_value`)을 적용하지 않습니다.

실험 출력 상한은 기본 100이며 `PICARX_EXPERIMENT_MAX_OUTPUT` 환경 변수로 낮출 수 있습니다. 수동 주행 화면의 PWM 상한(60)은 그대로입니다.

## 안전

- 자동 측정 주행은 0.1초마다 명령을 갱신하며, 주행 시간(최대 6 s)이 지나면 정지합니다. 서버 watchdog도 그대로 동작합니다.
- 주행 중 `/api/stop`(앱의 긴급 정지)을 누르면 즉시 멈추고, 그 시행은 *중단·제외* 상태로 저장됩니다.
- 주행로 끝에 벽이나 완충재를 두고, 주행 시간을 주행로 길이에 맞게 줄이세요.

## 데이터 파일

`backend/data/experiments/`(git 제외)에 시행마다 두 파일이 생깁니다.

- `<id>.json`: 조건, 방지턱 형상, 기록 정보, 분석 결과
- `<id>.csv`: 원자료 `t_s, ax_g, ay_g, az_g, gx_dps, gy_dps, gz_dps, vertical_g, dynamic_g`

설정은 `backend/data/experiment_settings.json`에 저장됩니다. 데이터 위치는 `PICARX_DATA_DIR`로 바꿀 수 있습니다.

## 차량 없이 시험하기

백엔드를 모의 모드(`PICARX_MODE=mock`)로 실행하고, 앱 설정에서 *Raspberry Pi 연결*을 선택해 `http://localhost:8000`에 연결하면 합성 데이터로 전체 흐름을 확인할 수 있습니다. 합성 데이터는 `simulated: true`로 저장되며, 실제 하드웨어 모드의 목록·집계·회귀에는 나타나지 않습니다.

## API

| Method | Path | 설명 |
|---|---|---|
| GET/PUT | `/api/experiment/settings` | 축간거리, 바퀴 반지름, 검출 기준, 허용 가속도, 방지턱 형상 등 |
| POST | `/api/experiment/auto-run` | `{condition, run_seconds?, pre_roll_s?, tail_s?}` 자동 측정 주행 시작 |
| GET | `/api/experiment/auto-run` | 진행 상태(`pre_roll`, `driving`, `tail`, `analyzing`, `done`, `aborted`, `error`)와 결과 |
| POST | `/api/experiment/save-recording` | 센서 화면에서 수동으로 기록한 데이터를 조건과 함께 저장 |
| GET | `/api/experiment/runs` | 시행 목록, 조건별 집계, 방지턱별 회귀식 |
| GET | `/api/experiment/runs/{id}` | 시행 상세 + 그래프용 시계열(피크 보존 다운샘플) |
| GET | `/api/experiment/runs/{id}/raw.csv` | 원자료 CSV |
| PATCH/DELETE | `/api/experiment/runs/{id}` | 제외 표시·메모 수정 / 삭제 |
| POST | `/api/experiment/reanalyze` | 현재 설정으로 전체 재분석 |
| GET | `/api/experiment/export/runs.csv` | 시행별 표 |
| GET | `/api/experiment/export/conditions.csv` | 조건별 절사평균 표 + 회귀식 |

`condition` 예시:

```json
{"trial":1,"bump_id":"A","base_mass_g":1020,"added_mass_g":150,"output_mode":"duty","output_value":45,"video_speed_mps":null,"note":""}
```

## 라즈베리파이 준비

- I²C 속도를 400 kHz로 올리면 표본 속도가 좋아집니다. `/boot/firmware/config.txt`(구버전 `/boot/config.txt`)에 `dtparam=i2c_arm_baudrate=400000`을 추가하고 재부팅하세요.
- `smbus2`가 있으면 우선 사용하고, 없으면 `python3-smbus`를 사용합니다.
- 센서는 차체 중앙(앞·뒤 축 사이)에 나사나 핫글루로 단단히 고정합니다. 폼 양면테이프는 충격을 걸러냅니다. 센서를 뒤집어 붙여도 보정 시 중력 방향을 판별하므로 수직 가속도의 의미는 같습니다.
