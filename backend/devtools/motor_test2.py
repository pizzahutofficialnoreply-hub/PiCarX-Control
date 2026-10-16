"""모터 점검 2단계 – 한 바퀴씩은 정상인데 실제 주행에서 왼쪽 바퀴가 떨릴 때.

차이를 하나씩 더해 가며 어디서 문제가 시작되는지 찾습니다.
  C1. 바퀴를 띄우고 두 바퀴 동시에 전진 (전류 2배)
  C2. 띄운 채 두 바퀴 + 조향 서보까지 움직이기 (전류 더 큼)
  D.  바닥에 내려놓고 전진 (무게·마찰 부하)
매 단계 배터리 전압의 최저값을 함께 보여 줍니다(전압이 뚝 떨어지면 전원 문제).

실행: 앱 서버를 끄고  →  cd ~/PiCarX-Control/backend && sudo python3 devtools/motor_test2.py
"""
import sys
import threading
import time

try:
    from picarx import Picarx
except ImportError:
    sys.exit("picarx 라이브러리를 찾지 못했습니다. 라즈베리파이에서 실행하세요.")
try:
    from robot_hat.utils import get_battery_voltage
except Exception:
    get_battery_voltage = None

px = Picarx()
px.set_dir_servo_angle(0)
notes = []


def volt():
    try:
        return float(get_battery_voltage()) if get_battery_voltage else None
    except Exception:
        return None


def run(label, start, seconds, during=None):
    """start()로 모터를 켜고, seconds 동안 전압 최저값을 기록합니다."""
    lows, stop = [], threading.Event()

    def watch():
        while not stop.is_set():
            v = volt()
            if v:
                lows.append(v)
            time.sleep(0.05)

    t = threading.Thread(target=watch, daemon=True)
    print(f"\n[{label}] {seconds:g}초…")
    t.start()
    start()
    end = time.time() + seconds
    while time.time() < end:
        if during:
            during(time.time())
        time.sleep(0.05)
    px.stop()
    px.set_dir_servo_angle(0)
    stop.set()
    t.join()
    vmin = min(lows) if lows else None
    ans = input(f"  → 왼쪽 바퀴 어땠나요? [Enter=정상, x=떨림·왔다갔다] (최저 전압 {vmin:.2f} V) " if vmin
                else "  → 왼쪽 바퀴 어땠나요? [Enter=정상, x=떨림·왔다갔다] ").strip().lower()
    notes.append((label, "이상" if ans.startswith("x") else "정상", vmin))
    time.sleep(0.5)


def sweep(now):
    px.set_dir_servo_angle(25 if int(now * 3) % 2 else -25)


try:
    v0 = volt()
    print(f"\n정지 상태 배터리 전압: {v0:.2f} V" if v0 else "\n배터리 전압을 읽지 못했습니다(계속 진행).")
    input("\n차를 상자 위에 올려 바퀴를 띄우고 Enter…")
    print("\n=== C1. 띄운 채 두 바퀴 동시 ===")
    for s in (40, 70, 100):
        run(f"띄움·두 바퀴 {s}", lambda s=s: px.forward(s), 2.0)
    print("\n=== C2. 띄운 채 두 바퀴 + 조향 서보 좌우로 ===")
    run("띄움·두 바퀴 100 + 조향", lambda: px.forward(100), 2.5, during=sweep)

    input("\n이제 바닥에 내려놓으세요. 앞으로 1.5 m 이상 비우고 Enter (차가 실제로 달립니다)…")
    print("\n=== D. 바닥 주행 ===")
    for s, sec in ((50, 1.2), (80, 1.0), (100, 0.8)):
        run(f"바닥·전진 {s}", lambda s=s: px.forward(s), sec)
        input("  차를 출발 위치로 되돌리고 Enter…")
except KeyboardInterrupt:
    print("\n중단했습니다.")
finally:
    px.stop()
    px.set_dir_servo_angle(0)

print("\n=== 결과 ===")
for label, r, v in notes:
    print(f"  {r}  {label}" + (f"   (최저 {v:.2f} V)" if v else ""))
bad = {l for l, r, _ in notes if r == "이상"}
lows = [v for l, r, v in notes if v]
low_v = min(lows) if lows else None
print()
if low_v is not None and low_v < 6.6:
    print(f"주행 중 전압이 {low_v:.2f} V까지 떨어졌습니다 → 배터리 부족·노후 또는 배터리 접촉 문제일 가능성이 큽니다. 완충 후 다시 해 보세요.")
if not bad:
    print("모두 정상 → 하드웨어·전원은 괜찮습니다. 앱에서 어떤 조작(WASD, 자동 측정, 출력값)일 때 생기는지 알려 주세요.")
elif any(l.startswith("띄움") for l in bad):
    print("바퀴를 띄워도 두 바퀴를 같이 돌리면 이상 → 전원(배터리·전원 스위치·배터리 단자) 또는 보드 문제입니다.")
    print("한 바퀴씩은 정상이었으니 모터 자체는 괜찮을 가능성이 큽니다. 완충한 배터리로 다시 해 보세요.")
else:
    print("띄우면 정상, 바닥에서만 이상 → 무게가 실렸을 때의 문제입니다.")
    print("  · 왼쪽 바퀴가 차체·나사·전선에 쓸리는지, 바퀴가 축에 헐겁거나 너무 깊이 꽂혔는지 확인")
    print("  · 모터 고정 나사가 풀려 모터가 기울어지는지 확인")
    print("  · 그래도 그러면 무게를 받으면 힘이 모자라는 모터(기어 마모) → 모터 교체")
