"""모터 단독 점검 (앱 없이 실행) – 왼쪽 바퀴 이상이 소프트웨어 문제인지 하드웨어 문제인지 가립니다.

사용법 (라즈베리파이에서):
    1) 앱 서버를 먼저 끕니다:  sudo systemctl stop picarx-control   (또는 uvicorn 창에서 Ctrl+C)
    2) 차를 상자 위에 올려 바퀴가 바닥에 닿지 않게 합니다.
    3) cd ~/PiCarX-Control/backend && python3 devtools/motor_test.py   (sudo로 실행하면 설정 파일 권한이 바뀌니 금지)

단계마다 바퀴를 보고 Enter를 누르세요. 마지막에 결과를 요약합니다.
  A. 공식 picarx 라이브러리로 왼쪽/오른쪽 바퀴를 하나씩 전진·후진 (출력 30, 60, 100)
  B. 라이브러리를 거치지 않고 핀(방향 핀 + PWM 핀)에 직접 써서 왼쪽 바퀴 전진
"""
import sys
import time

try:
    from picarx import Picarx
except ImportError:
    sys.exit("picarx 라이브러리를 찾지 못했습니다. 라즈베리파이에서 실행하세요.")

px = Picarx()
px.set_dir_servo_angle(0)
print("\n보정값  cali_dir_value =", getattr(px, "cali_dir_value", "?"),
      " cali_speed_value =", getattr(px, "cali_speed_value", "?"))
print("왼쪽 = motor 1, 오른쪽 = motor 2. 라이브러리의 '전진'은 motor 1 = +, motor 2 = − 입니다.\n")

notes = []


def ask(label: str) -> None:
    ans = input(f"  → {label}: 바퀴가 어땠나요? [Enter=정상, x=이상(떨림·안 돎·왔다갔다)] ").strip().lower()
    notes.append((label, "이상" if ans.startswith("x") else "정상"))


def run(label: str, fn, seconds: float = 2.0) -> None:
    print(f"\n[{label}] {seconds:g}초 동안 돕니다…")
    fn()
    time.sleep(seconds)
    px.stop()
    time.sleep(0.4)
    ask(label)


try:
    input("바퀴가 떠 있으면 Enter (중단은 Ctrl+C)…")
    print("\n=== A. 라이브러리 set_motor_speed (한 바퀴씩) ===")
    for name, motor, fwd_sign in (("왼쪽", 1, 1), ("오른쪽", 2, -1)):
        for speed in (30, 60, 100):
            run(f"{name} 전진 {speed}", lambda m=motor, s=fwd_sign * speed: px.set_motor_speed(m, s))
        run(f"{name} 후진 60", lambda m=motor, s=-fwd_sign * 60: px.set_motor_speed(m, s))

    print("\n=== B. 핀 직접 제어 (라이브러리 계산 없이) – 왼쪽 바퀴 전진 ===")
    dir_pin, pwm_pin = px.motor_direction_pins[0], px.motor_speed_pins[0]
    cali = px.cali_dir_value[0] if hasattr(px, "cali_dir_value") else 1
    for duty in (60, 100):
        def raw(d=duty):
            (dir_pin.low() if cali > 0 else dir_pin.high())
            pwm_pin.pulse_width_percent(d)
        run(f"왼쪽 핀 직접 듀티 {duty}%", raw)
except KeyboardInterrupt:
    print("\n중단했습니다.")
finally:
    px.stop()

print("\n=== 결과 ===")
for label, r in notes:
    print(f"  {r}  {label}")
bad = [l for l, r in notes if r == "이상"]
left_bad = [l for l in bad if l.startswith("왼쪽")]
right_bad = [l for l in bad if l.startswith("오른쪽")]
print()
if not bad:
    print("모두 정상 → 하드웨어는 괜찮습니다. 앱 쪽 문제일 가능성이 높으니 어떤 화면·조작에서 생기는지 알려 주세요.")
elif left_bad and not right_bad:
    print("왼쪽만 이상 → 앱과 무관한 하드웨어 쪽입니다(모터·기어·전선·보드의 왼쪽 모터 채널).")
    print("다음: 보드에서 좌우 모터 커넥터를 서로 바꿔 꽂고 다시 실행하세요(이때 '왼쪽' 시험은 실제로 오른쪽 바퀴를 돌립니다).")
    print("  · 또 '왼쪽' 시험(1번 채널)에서 이상 → 보드(Robot HAT)의 왼쪽 모터 채널 문제")
    print("  · 이번엔 '오른쪽' 시험(2번 채널)에서 이상 → 그 모터·기어박스·모터 전선 문제(모터를 따라감)")
elif right_bad and not left_bad:
    print("오른쪽(2번 채널)만 이상 → 하드웨어 쪽입니다. 커넥터를 바꿔 꽂은 상태라면 원래 왼쪽에 있던 모터·기어·전선이 문제입니다.")
else:
    print("여러 곳이 이상 → 배터리 전압(7 V 이상인지)과 전원 스위치·배터리 접촉부터 확인하세요.")
