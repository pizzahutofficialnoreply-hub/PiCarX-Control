# 가짜 하드웨어 (개발·시험 전용)

PiCar-X와 MPU-6050 없이 **하드웨어 모드 경로**(센서 보정·측정·내보내기, 자동 측정 주행, 직진–후진 반복, 자이로 직진 보정)를 PC에서 시험합니다.

```bash
cd backend
PYTHONPATH=devtools/fake_hardware PICARX_MODE=hardware uvicorn app.main:app --port 8000
```

- 차가 움직이면 위치를 적분하고, 앞·뒤 축이 x = 0.6 m의 방지턱을 지날 때 피크를 만듭니다.
- 직진 중 약 +4 °/s로 왼쪽으로 휘는 성질이 있어 자이로 직진 보정의 효과를 확인할 수 있습니다.
- 실제 측정값이 아니므로 연구 데이터로 쓰지 마세요. 저장 위치를 분리하려면 `PICARX_DATA_DIR`를 지정하세요.
