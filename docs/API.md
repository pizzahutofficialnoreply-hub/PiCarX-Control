# API 명세

기본 주소 `http://<Pi-IP>:8000`. 제어 API에는 현재 인증이 없으므로 신뢰된 LAN에서만 사용하세요.

| Method | Path | 설명 |
|---|---|---|
| GET | `/api/health` | API 연결 및 모드 확인 |
| GET | `/api/diagnostics` | 서버·라이브러리·API·드라이버 초기화 진단; 실제 장치 동작 미검증 항목은 unknown |
| GET | `/api/status` | 모드, 주행 상태, 명령값, 보정값 확인 |
| POST | `/api/drive` | 유효한 이동 명령 |
| POST | `/api/stop` | 즉시 정지 |
| PUT | `/api/settings/calibration` | 조향 중심 및 카메라 Pan/Tilt 보정값 저장 |
| POST | `/api/settings/steering-test` | 모터 정지 상태에서 조향 서보만 시험 |
| WS | `/api/ws` | 약 200ms 간격 상태 메시지 전달 |

`POST /api/drive` JSON 예시:

```json
{"direction":"forward","speed":35,"steering":25}
```

`direction` accepts `forward`, `backward`, `left`, or `right`. Speed is an integer from 0 to 60; steering is a signed integer from -30 to 30 degrees. Out-of-range and unknown values return 422.

`POST /api/stop`은 `{ "motion":"stopped", "speed":0, "steering":0 }` 상태를 반환합니다. 하드웨어 오류 시 503과 오류 설명을 반환하며 가능한 한 정지 상태로 복귀합니다.

보정 예시 `PUT /api/settings/calibration`:

```json
{"steering_center":-3}
```

Steering center accepts -20..20; pan and tilt centers accept -30..30. Values are applied through the confirmed PiCar-X servo methods and saved in `backend/data/settings.json`.

WebSocket은 접속 후 JSON 상태를 주기적으로 전송합니다. 연결 종료 시 보수적으로 정지 요청합니다. `GET /api/status` 응답의 `mode`는 `mock` 또는 `hardware`입니다. `connected`와 `connection_error`는 실제 모드에서 드라이버 초기화 상태를 나타냅니다. 하드웨어 연결 불가 시 `/api/drive`는 503을 반환합니다.


Steering test JSON: `{"direction":"left","angle":15}`. Direction accepts `left`, `right`, or `center`. Calibration accepts any subset of `steering_center` (-20..20), `pan_center` (-30..30), and `tilt_center` (-30..30).

`GET /api/diagnostics` reports `library_installed`, import/class paths, method presence, driver initialization, motor/steering API availability, camera servo API availability, camera device status, Python version, and `last_error`. A `null` camera device means unknown: this application does not open the camera to probe it. API method availability and driver initialization are not proof that motors, servos, or a camera physically work.

## Phase 5 추가 API

### `GET /api/sensors`

하드웨어 모드에서는 확인된 `get_distance()`, `get_grayscale_data()`, `get_cliff_status()`를 호출합니다. 호출은 별도 스레드에서 실행합니다. 반환 예시:

```json
{"mode":"hardware","available":true,"distance_cm":23.4,"grayscale":[120,140,130],"cliff_status":[false,false,false],"error":null}
```

`mode`는 `mock` 또는 `hardware`입니다. 모의 값은 물리 측정값이 아닙니다. 설치 라이브러리에 센서 메서드가 없으면 해당 값은 `null`입니다. 센서 호출 오류는 HTTP 503으로 반환됩니다.

`POST /api/drive`의 `steering`은 -30..30의 부호 있는 각도입니다. 음수는 왼쪽, 양수는 오른쪽이며 어댑터에서 다시 제한됩니다. 기존 방향값 `forward`, `backward`, `left`, `right`도 허용됩니다.

## 방지턱 충격 실험 API

`/api/impact/*`(MPU-6050 실시간 상태·보정·수동 기록)과 `/api/experiment/*`(설정, 자동 측정 주행, 저장, 분석, CSV 내보내기)는 [EXPERIMENT.md](EXPERIMENT.md)에 정리했습니다. `/api/impact/status`의 표본은 피크를 보존하도록 다운샘플링되며, 각 표본에는 정지 보정 자세 기준 수직 가속도 `vertical_g`(정지 1 g)가 추가되었습니다.

