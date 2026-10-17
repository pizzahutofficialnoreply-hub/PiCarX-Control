// 해석 가이드: 그래프·CSV 읽는 법, 앞·뒤 바퀴 구분, 관계식이 바뀔 수 있는 이유, 사람 기준(선행 연구)과 실제 도로 환산
import { useMemo, useState } from 'react';
import { ExternalLink } from 'lucide-react';
import { G } from '../services/experiment';
import type { ExperimentContext } from './context';
import NumInput from '../components/NumInput';
import { usePersistentState } from '../services/persist';

type Ref = { id: string; who: string; title: string; where: string; url: string; finding: string; use: string };
const REFS: Ref[] = [
  { id: 'watts', who: 'Watts, G. R. (1973)', title: 'Road humps for the control of vehicle speeds', where: 'TRRL Report LR 597, 영국 교통도로연구소', url: 'https://www.trl.co.uk/uploads/trl/documents/LR597.pdf',
    finding: '원호형 나무 혹 15종(길이 0.05~3.66 m, 높이 13~152 mm)을 8~113 km/h로 넘으며 무릎 위 가속도계로 최대 수직 가속도를 재고, 운전자·동승자 6명이 불편 점수(0 편함 ~ 6 매우 불편)를 매겼습니다. 불편 점수는 최대 가속도에 거의 비례했고(상관계수 0.83~0.84), 약 0.9 g를 넘으면 "불편"으로 평가되었습니다. 길이 3.66 m·높이 76 mm 혹은 8~40 km/h에서 0.6 g 아래였습니다.',
    use: '설문(주관 평가)과 실측 가속도를 함께 잰 대표 연구. 허용 기준 후보 ≈ 0.9 g(불편 시작).' },
  { id: 'trl614', who: 'Kennedy, J. 외 (2004)', title: 'Impact of road humps on vehicles and their occupants', where: 'TRL Report 614, 영국 교통부 의뢰', url: 'https://trl.co.uk/uploads/trl/documents/TRL614(1).pdf',
    finding: '승용차·택시·구급차·버스·미니버스 5대가 높이 75 mm 혹(원형·평탄형·사인형)과 쿠션을 16~64 km/h로 넘을 때 무게중심·운전석·뒷좌석 더미 골반의 수직 가속도를 측정. 불편 점수와 최대 수직 가속도의 상관이 높았고(승용차 R² = 0.82), 선행 연구 기준으로 탑승자는 약 0.7 g 이상의 최대 수직 가속도를 받아들이지 않는다고 정리했습니다. 승용차·택시 운전자는 약 32 km/h에서 0.7 g 아래였고, 택시 뒷좌석은 훨씬 컸습니다.',
    use: '허용 기준 후보 ≈ 0.7 g. 뒷좌석(차체 뒤쪽)이 더 크게 흔들린다는 근거 → 센서 위치 변인의 근거.' },
  { id: 'kyrollos', who: 'Kyrollos, D. G. 외 (2020)', title: 'Injury Risk and Comfort Assessment Applied to Ambulance Transportation', where: 'CDSR 2020 학술대회 논문 156', url: 'https://avestia.com/CDSR2020_Proceedings/files/paper/CDSR_156.pdf',
    finding: '구급차가 방지턱을 15~40 km/h로 넘을 때 바닥 수직 가속도를 측정. Kjemtrup의 기준(최대 수직 가속도 0.2 g = 약간 불편, 0.4 g = 상당히 불편)을 적용해 20 km/h 초과에서 불편, 30 km/h 초과에서 상당한 불편으로 판단했습니다. 척추 부상 위험은 DRI 17.7(심각한 척추 부상 10%) 기준을 함께 썼습니다.',
    use: '환자·노약자처럼 민감한 탑승자 기준 후보 ≈ 0.2~0.4 g.' },
  { id: 'iso2631', who: 'ISO 2631-1:1997 (Asua 외 2022 인용)', title: 'Mechanical vibration and shock – Evaluation of human exposure to whole-body vibration', where: 'Asua 외, Analysis of the Motion Sickness and the Lack of Comfort in Car Passengers, Applied Sciences 12, 3717', url: 'https://arxiv.org/pdf/2501.17664',
    finding: '주파수 가중 RMS 가속도로 승차감을 나눕니다: 0.315 m/s² 미만 불편하지 않음, 0.315~0.63 약간 불편, 0.5~1 꽤 불편, 0.8~1.6 불편, 1.25~2.5 매우 불편, 2 초과 극히 불편.',
    use: '연속 진동(RMS) 기준이라 한 번의 충격 최대값과 바로 비교할 수 없습니다. 보고서에는 "참고 기준"으로만 쓰세요.' },
  { id: 'aslan', who: 'Aslan, S. 외 (2005)', title: 'Speed bump–induced spinal column injury', where: 'American Journal of Emergency Medicine 23(4), 563–564', url: 'https://ajemjournal.com/article/S0735-6757(05)00119-1/pdf',
    finding: '시내버스가 방지턱을 넘을 때 다친 승객 5명: 4명 흉요추 압박골절, 1명 경추 골절, 3명 수술. 과속 통과의 위험을 경고하고 검증된 규격으로 방지턱을 만들 것을 권고했습니다.',
    use: '충격이 실제 부상으로 이어진 사례(동기·필요성).' },
  { id: 'chile', who: '칠레 Hospital del Trabajador 연구진 (2011)', title: 'Speed hump spine fractures: injury mechanism and case series', where: 'Journal of Spinal Disorders & Techniques', url: 'https://read.qxmd.com/read/21150664/speed-hump-spine-fractures-injury-mechanism-and-case-series',
    finding: '1997~2008년 방지턱 통과 중 척추 골절 46명(골절 52곳). 44명이 버스 승객이었고 그중 42명이 맨 뒷줄 좌석. 흉요추 이행부(L1, T12)에 집중, 21.7%가 수술, 평균 104일 결근.',
    use: '뒷좌석·차체 뒤쪽 위치에서 충격이 크다는 근거 → "위치" 변인 탐구로 확장할 이유.' },
  { id: 'shim', who: '심재수·최현규·김성민 (2011)', title: '과속방지턱 설치규격 검토를 위한 동역학적 분석', where: '한국도로학회논문집 13(3), 15–20', url: 'https://db.koreascholar.com/Article/Detail/249899',
    finding: '차량과 운전자를 1자유도계로 모델링해 방지턱 충격 응답을 계산. 운전자가 느끼는 수직 가속도는 방지턱의 폭과 높이의 비에 크게 좌우되므로 폭과 높이를 함께 설계해야 한다고 결론.',
    use: '이 탐구의 곡률 반경 R(폭·높이로 계산) 변인과 무차원수 Π 접근의 국내 근거.' },
  { id: 'lim', who: '임창식·최양원 (2016)', title: '보행자 통행안전성을 고려한 회전교차로의 고원식횡단보도 설치기준 연구', where: '대한토목학회논문집 36(6), 1075–1082', url: 'https://www.koreascience.or.kr/article/JAKO201608259723580.pdf',
    finding: '국내 회전교차로 20곳의 방지턱·고원식 횡단보도를 조사해 접근 속도별 높이(20 km/h 이하 6~8 cm, 30 km/h 8~10 cm)를 제안. Watts(1973)를 인용해 길이 3.6 m·높이 10 cm 혹이 8 km/h에서는 편하고 약 32 km/h 이상에서 불편하다고 정리.',
    use: '국내 설계 맥락과 실제 크기 방지턱의 체감 속도 근거.' },
];

const CSV_DOCS: { file: string; how: string; cols: [string, string][] }[] = [
  { file: 'picarx_runs.csv (시행 CSV)', how: '측정·분석 탭 → 시행 목록 → 시행 CSV. 한 줄 = 한 번 넘은 시행. 엑셀 피벗이나 그래프의 원재료입니다.', cols: [
    ['trial, bump_id, total_mass_g, output_mode, output_value', '조작 변인(회차, 방지턱, 총질량, 출력 방식·값)'],
    ['purpose', 'main = 관계식 도출용, validation = 식 검증용, pilot = 예비'],
    ['peak_g / peak_delta_g', '최대 수직 가속도(정지 = 1 g) / 그 증가분 Δa = peak_g − 1. 관계식은 Δa로 맞춥니다'],
    ['front_t_s, rear_t_s, axle_dt_s', '앞·뒤 바퀴 피크 시각과 간격 Δt(s)'],
    ['speed_mps', '통과 속도 v = 축간거리 / Δt'],
    ['video_speed_mps', '슬로모션 영상으로 잰 속도(입력했을 때) – speed_mps와 비교해 측정 방법 검증'],
    ['min_g, jump, airborne_s, landing_g', '최소 가속도, 지면 이탈 여부, 공중에 뜬 시간, 착지 충격'],
    ['peak_force_n, dynamic_force_n', '충격력 F = m·g·a_peak (N)과 정지 하중을 뺀 증가분'],
    ['pi, critical_speed_mps', 'Π = v²/(g·R_eff), 이론 점프 속도 v_c = √(g·R_eff)'],
    ['pair_source', 'auto = 자동으로 앞·뒤 피크 선택, manual = 사람이 지정'],
    ['battery_v, steering_source, heading_max_deg', '시작 배터리 전압, 조향 방식, 직진 중 최대 방향 오차(°)'],
    ['data_source, simulated', 'mpu6050 = 실제 측정, synthetic = 합성(모의) 데이터'],
    ['excluded, aborted, flags', '집계 제외 여부, 정지로 중단, 자동 점검 메시지'],
  ] },
  { file: 'picarx_conditions.csv (조건별 CSV)', how: '측정·분석 탭 → 분석 → 조건별 CSV. 보고서 표 IV-3과 그래프 1에 바로 쓰는 표입니다.', cols: [
    ['n_runs (n_used)', '그 조건의 시행 수와 평균에 쓴 수(3회 이상이면 최댓값·최솟값 하나씩 제외)'],
    ['mean_speed_mps, speed_min/max', '평균 속도와 사용한 시행의 속도 범위'],
    ['mean_peak_g, mean_peak_delta_g', '평균 최대 가속도와 평균 Δa'],
    ['kept_min/max_delta_g, dropped_delta_g', '평균에 쓴 값의 범위(그래프 오차막대)와 뺀 값'],
    ['mean_force_n, mean_pi, jump_count', '평균 충격력, 평균 Π, 점프한 시행 수'],
    ['# 관계식 후보 비교', '모델별 R², RMSE, 교차검증 RMSE, AIC, 매개변수와 95% 신뢰구간'],
    ['# 자동 해석', '숫자를 넣어 만든 결론 문장 초안'],
  ] },
  { file: 'picarx_validation.csv (검증 CSV)', how: '식 검증 탭. 실험하지 않은 조건에서 예측과 실측을 비교한 표.', cols: [
    ['predicted_delta_g, measured_delta_g', '관계식 예측 Δa와 실측 Δa'],
    ['abs_error_g, error_pct', '오차(g)와 오차율(%) = (실측 − 예측)/예측'],
    ['평균 절대 오차율', '10% 이내면 식이 새 조건을 잘 예측한다고 볼 수 있습니다'],
  ] },
  { file: 'run_….csv (시행 원자료)', how: '시행 결과 → 원자료 CSV. 약 1 kHz 센서 값 전체.', cols: [
    ['t_s', '기록 시작부터의 시간(s)'],
    ['ax_g, ay_g, az_g', '센서 3축 가속도(g, 보정 전 원값)'],
    ['gx_dps, gy_dps, gz_dps', '3축 각속도(°/s)'],
    ['vertical_g', '정지 자세 기준 수직 가속도(정지 1 g, 공중 0 g) – 그래프와 분석에 쓰는 값'],
    ['dynamic_g', '정지 상태 대비 가속도 변화의 크기(방향 무관)'],
  ] },
  { file: 'impact_….csv (센서 화면 수동 기록)', how: '센서 탭 → 측정 데이터 CSV. 위 원자료와 같은 열(축소 없음).', cols: [['열 구성', '시행 원자료와 같음']] },
];

function fitPi(points: { pi: number; d: number }[]) {
  const pts = points.filter(p => p.pi > 0 && p.d > 0);
  if (pts.length < 3) return null;
  const xs = pts.map(p => Math.log(p.pi)), ys = pts.map(p => Math.log(p.d));
  const mx = xs.reduce((a, b) => a + b, 0) / xs.length, my = ys.reduce((a, b) => a + b, 0) / ys.length;
  const sxx = xs.reduce((a, x) => a + (x - mx) ** 2, 0);
  if (sxx <= 0) return null;
  const n = xs.reduce((a, x, i) => a + (x - mx) * (ys[i] - my), 0) / sxx;
  return { k: Math.exp(my - n * mx), n, count: pts.length };
}

export default function GuideTab({ ctx }: { ctx: ExperimentContext }) {
  const runs = (ctx.data?.runs ?? []).filter(r => !r.excluded && (r.condition.purpose ?? 'main') === 'main');
  const piFit = useMemo(() => fitPi(runs.map(r => ({ pi: r.analysis.pi ?? 0, d: r.analysis.peak_delta_g ?? 0 }))), [runs]);
  const [humanLimit, setHumanLimit] = usePersistentState('guide-human', 0.7);
  const [realH, setRealH] = usePersistentState('guide-realH', 100);
  const [realW, setRealW] = usePersistentState('guide-realW', 3600);
  const [realWheel, setRealWheel] = usePersistentState('guide-realWheel', 300);
  const rReal = (realW ** 2 / 4 + realH ** 2) / (2 * realH) / 1000 + realWheel / 1000;
  const piLimit = piFit && piFit.n > 0 ? (humanLimit / piFit.k) ** (1 / piFit.n) : null;
  const vReal = piLimit ? Math.sqrt(piLimit * G * rReal) : null;
  const vJump = Math.sqrt(G * rReal);

  return <div className="exp-guide">
    <details className="card experiment-card" open><summary><h2>측정 가이드 – 처음부터 끝까지 순서대로</h2></summary>
      <ol className="exp-steps">
        <li><b>주행로 만들기</b> – 평평한 바닥에 1.5 m 이상 직선. <b>출발선 → 방지턱 앞까지 40~60 cm</b>로 정하고 바닥에 표시하세요(차가 일정한 속도에 도달하는 거리). 방지턱은 양면테이프로 고정하고, 방지턱 뒤로 50 cm 이상 비우고 끝에 완충재를 둡니다.</li>
        <li><b>출발선 테이프(권장)</b> – 출발 위치에서 차 앞쪽 센서 바로 아래에 검은 테이프를 가로로 붙이고, <i>테이프 인식</i> 탭에서 바닥·테이프 보정을 한 뒤 측정·분석 탭의 <i>출발선 테이프에서 멈추기</i>를 켜면 후진 복귀가 매번 같은 자리에서 멈춥니다.</li>
        <li><b>차량 준비</b> – 배터리 완충(7.6 V 이상), 센서는 나사나 케이블타이로 단단히 고정(폼테이프는 진동을 흡수해 피크가 작아짐). 축간거리 L과 바퀴 반지름을 자로 재서 <i>차량·분석 설정</i>에 입력하세요. <b>축간거리가 없으면 속도를 못 구해 n도 안 나옵니다.</b> 무게추를 단 상태로 총질량을 저울로 잽니다.</li>
        <li><b>센서 보정</b> – 평평한 곳에 차를 가만히 두고 센서 화면에서 정지 상태 보정.</li>
        <li><b>평지 시험 2~3회</b> – 방지턱 없이 자동 측정을 돌려 보세요. '방지턱 못 찾음'이 나와야 정상이고, <i>주행 진동 크기</i>가 0.1 g 안팎이면 좋습니다. 0.2 g 이상이면 센서 고정이나 바퀴·기어 상태를 먼저 확인하세요(진동이 크면 작은 방지턱 충격이 묻힙니다).</li>
        <li><b>본 측정</b> – 질량 한 가지를 정해 놓고 출력을 4~5단계(예: 40·55·70·85·100)로 바꾸며 단계마다 3~5회 반복합니다. 그다음 무게추를 바꿔(2~3단계) 같은 출력들로 반복합니다. <i>측정 계획</i> 표가 남은 칸을 알려 줍니다. 방지턱을 못 넘는 낮은 출력은 빼세요.</li>
        <li><b>조건마다 바로 확인</b> – <i>겹쳐 보기 → 같은 조건만</i>에서 앞·뒤 바퀴 봉우리가 모든 회차에서 같은 자리에 솟는지, 통과 속도가 서로 10% 안쪽인지 봅니다. 혼자 튀는 회차는 한 회씩 보기에서 확인하고 '집계에서 빼기'.</li>
        <li><b>관계식(n) 구하기</b> – 통과 속도가 서로 다른 조건이 <b>2개 이상</b>이면 식이 계산되고, 믿을 만하려면 <b>4개 이상</b>이 좋습니다. 질량 지수 p는 질량이 2단계 이상이어야 나옵니다.</li>
        <li><b>식 검증</b> – 관계식이 정해진 뒤, 측정하지 않은 질량이나 속도로 <i>식 검증</i> 탭에서 몇 회 측정해 예측과 비교합니다. 오차 10% 안쪽이면 식이 쓸 만합니다.</li>
        <li><b>정리</b> – 그래프 PNG와 조건별 CSV를 저장하고, 백업 ZIP을 받아 두세요.</li>
      </ol>
      <p className="exp-hint"><b>주행 거리·배터리 차이는 괜찮나요?</b> 그래프의 가로축은 PWM이 아니라 <b>방지턱에서 직접 잰 통과 속도</b>라서, 같은 PWM에서 회차마다 속도가 조금 달라도 그 속도 그대로 분석에 들어갑니다. 다만 출발선 위치를 매번 같게 해야 방지턱에 닿기 전에 속도가 안정됩니다. 후진 복귀 구간은 기록하지 않습니다(측정은 출발 전 정지 → 직진 → 멈춘 뒤 잠깐까지만).</p>
    </details>

    <section className="card experiment-card">
      <h2>한눈에 보는 측정 원리</h2>
      <svg viewBox="0 0 640 200" className="exp-guide-svg" role="img" aria-label="앞바퀴와 뒷바퀴가 차례로 방지턱을 넘으며 생기는 두 피크">
        <rect x="0" y="0" width="640" height="200" fill="#fff" />
        <line x1="20" y1="80" x2="300" y2="80" stroke="#555" strokeWidth="2" />
        <path d="M150,80 Q175,55 200,80" fill="#c9a36b" stroke="#8a6d3b" />
        <text x="175" y="98" fontSize="11" textAnchor="middle" fill="#555">방지턱</text>
        <rect x="60" y="38" width="90" height="22" rx="5" fill="#2f6fb0" opacity="0.85" />
        <circle cx="72" cy="68" r="11" fill="#333" /><circle cx="140" cy="68" r="11" fill="#333" />
        <text x="140" y="32" fontSize="11" textAnchor="middle" fill="#d0782a">앞바퀴</text><text x="72" y="32" fontSize="11" textAnchor="middle" fill="#3c9a5f">뒷바퀴</text>
        <path d="M160,45 l25,0" stroke="#333" markerEnd="url(#ga)" /><text x="190" y="49" fontSize="11" fill="#333">진행 방향</text>
        <line x1="72" y1="118" x2="140" y2="118" stroke="#333" markerStart="url(#ga)" markerEnd="url(#ga)" /><text x="106" y="134" fontSize="11" textAnchor="middle" fill="#333">축간거리 L</text>
        <line x1="330" y1="160" x2="620" y2="160" stroke="#999" /><line x1="330" y1="20" x2="330" y2="160" stroke="#999" />
        <text x="335" y="16" fontSize="11" fill="#555">수직 가속도 (g)</text><text x="618" y="176" fontSize="11" textAnchor="end" fill="#555">시간</text>
        <line x1="330" y1="120" x2="620" y2="120" stroke="#bbb" strokeDasharray="4 3" /><text x="616" y="115" fontSize="10" textAnchor="end" fill="#777">정지 1 g</text>
        <path d="M330,120 L400,120 L410,40 L420,135 L430,120 L520,120 L530,55 L540,133 L550,120 L620,120" fill="none" stroke="#2f6fb0" strokeWidth="2" />
        <text x="410" y="34" fontSize="11" textAnchor="middle" fill="#d0782a">① 앞바퀴</text><text x="530" y="48" fontSize="11" textAnchor="middle" fill="#3c9a5f">② 뒷바퀴</text>
        <line x1="410" y1="150" x2="530" y2="150" stroke="#333" markerStart="url(#ga)" markerEnd="url(#ga)" /><text x="470" y="146" fontSize="11" textAnchor="middle" fill="#333">Δt → v = L / Δt</text>
        <defs><marker id="ga" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#333" /></marker></defs>
      </svg>
      <p>센서는 차체에 붙어 있어 <b>앞바퀴가 방지턱에 닿을 때 한 번, 뒷바퀴가 닿을 때 한 번</b> 수직 가속도가 튀어 오릅니다. 차는 앞으로 가므로 <b>먼저 나온 피크가 앞바퀴</b>입니다. 두 피크 사이 시간 Δt 동안 차는 축간거리 L만큼 갔으므로 통과 속도 v = L / Δt 입니다. 정지 상태는 1 g(중력), 공중에 뜨면 0 g 근처, 충돌·착지 순간에는 1 g보다 커집니다.</p>
    </section>

    <details className="card experiment-card" open><summary><h2>앞바퀴·뒷바퀴는 어떻게 구분하나요?</h2></summary>
      <ol>
        <li><b>시간 순서</b>: 앞바퀴가 먼저 닿으므로 두 피크 중 이른 것이 앞바퀴입니다(앱의 모든 계산이 이 규칙).</li>
        <li><b>어떤 두 피크를 고르나</b>: 피크가 3개 이상일 때가 문제입니다. 앱은 ① 공중에 떴다가 내려온 직후(30 ms 이내)의 <b>착지 피크</b>를 빼고, ② 두 피크 간격으로 계산한 속도가 0.03~4 m/s 범위인 쌍만 남긴 뒤, ③ 두 피크 높이의 합이 가장 큰 쌍을 고릅니다.</li>
        <li><b>스스로 확인하는 방법</b>: (a) 같은 조건의 다른 시행과 속도가 20% 넘게 다르면 경고가 뜹니다. (b) 영상 실측 속도를 입력해 비교합니다. (c) 앞·뒤 피크 높이는 보통 비슷합니다(뒷바퀴가 조금 작거나 큼). (d) 공중에 뜬 시행은 '착지' 피크가 따로 표시됩니다.</li>
        <li><b>틀렸을 때</b>: 시행 결과의 '앞·뒤 바퀴 피크 확인·직접 지정'에서 후보 #번호를 골라 '지정 적용'을 누르면 그 시행만 다시 계산되고 <i>수동 지정</i>으로 표시됩니다. 보고서에 몇 개를 수동 지정했는지 적어 두세요.</li>
      </ol>
    </details>

    <details className="card experiment-card"><summary><h2>그래프 읽는 법</h2></summary>
      <dl className="exp-guide-dl">
        <dt>시행 그래프 (수직 가속도 – 시간)</dt><dd>한 번 넘은 기록. 주황 ● 앞바퀴, 초록 ● 뒷바퀴, 보라 ● 착지, 회색 ▼ 다른 후보. 노란 점선 위로 올라온 봉우리만 피크 후보가 됩니다. 빨간 점선(0.2 g) 아래로 내려간 구간은 공중에 뜬 시간입니다. '방지턱 구간'으로 확대해 보고서 그림 3에 쓰세요.</dd>
        <dt>그래프 1 (속도 – Δa)</dt><dd>작은 점 = 시행 하나, 큰 원 = 조건 평균, 세로 막대 = 평균에 쓴 값의 범위(재현성), 가로 막대 = 속도 범위. 선은 선택한 관계식. 색은 총질량. 같은 속도에서 색끼리 위아래로 갈라지면 질량 효과가 있는 것이고, 겹치면 질량이 가속도에 큰 영향이 없는 것입니다. 빨간 점선과 선이 만나는 ◆가 허용 기준의 최대 통과 속도 v_max입니다.</dd>
        <dt>그래프 2 (Π – 최대 가속도)</dt><dd>모양이 다른 방지턱(A, B)의 점이 <b>하나의 곡선 위에 모이면</b> 충격이 "속도와 곡률의 조합 Π"로 정해진다는 뜻입니다. Π = 1(빨간 선) 근처부터 속이 빈 점(점프)이 나타나는지 보세요.</dd>
        <dt>그래프 3 (예측 – 실측)</dt><dd>관계식이 조건 평균을 얼마나 잘 맞히는지. 대각선 위에 있으면 정확, 초록 띠는 ±10%. 빠른 조건만 위로 벗어나면 식의 모양(지수)이 맞지 않는 것입니다.</dd>
        <dt>그래프 4 (식 검증)</dt><dd>실험에 쓰지 않은 조건을 예측과 비교. 이것이 "관계식이 맞는가"에 대한 가장 강한 증거입니다.</dd>
        <dt>대칭 점검·라인 추종 그래프</dt><dd>장치 점검용. 보고서에는 '실험 방법의 타당성' 근거로 짧게 쓸 수 있습니다.</dd>
      </dl>
      <p className="exp-hint">모든 그래프는 PNG(1920×1080, 흰 바탕)·SVG로 저장됩니다. 한글 보고서에는 PNG, 확대·편집이 필요하면 SVG를 쓰세요.</p>
    </details>

    <details className="card experiment-card"><summary><h2>관계식은 왜 바뀔 수 있고, 어떻게 다루나요?</h2></summary>
      <p>맞다 — 관계식은 데이터와 고른 모양에 따라 얼마든지 달라집니다. 그래서 "식 하나를 구했다"가 아니라 <b>"여러 후보 중 이 식이 가장 잘 예측했고, 새 조건에서도 맞았다"</b>로 보여 줘야 설득력이 있습니다.</p>
      <ol>
        <li><b>후보식 비교</b>: 거듭제곱(n, p 자유), n=1(충돌 모델), n=2(원운동 모델), 일차식, 이차식을 모두 맞춥니다. 이론에서 나온 n=1, n=2 식이 후보에 있다는 점이 중요합니다.</li>
        <li><b>교차검증 RMSE로 고르기</b>: 한 조건씩 빼고 나머지로 식을 만든 뒤 뺀 조건을 예측한 오차입니다. 매개변수를 늘리면 R²는 항상 오르지만 교차검증 오차는 오히려 커질 수 있습니다(과적합).</li>
        <li><b>신뢰구간으로 말하기</b>: n = 1.62 (95% CI 1.41~1.83)처럼 범위로 쓰세요. 범위가 2를 포함하면 "원운동 모델과 구분되지 않는다", 1과 2를 모두 포함하면 "데이터가 부족해 구분할 수 없다"가 정직한 결론입니다.</li>
        <li><b>검증 실험</b>: 식을 만든 뒤 실험하지 않은 질량(+75 g, +225 g 등)으로 식 검증 탭에서 측정해 오차율을 보고합니다.</li>
        <li><b>식이 바뀌는 원인 줄이기</b>: 속도 조건을 4~5단계 이상, 조건마다 5회, 배터리 전압 일정, 센서 고정, 같은 바닥. 식이 크게 흔들리면 데이터가 부족하다는 신호입니다.</li>
      </ol>
    </details>

    <details className="card experiment-card" open><summary><h2>사람이 견딜 수 있는 수직 가속도 – 선행 연구</h2></summary>
      <p>아래는 실제로 열어 확인한 자료입니다. 측정 방식(최대값/RMS, 측정 위치)이 서로 달라 숫자를 그대로 섞으면 안 됩니다.</p>
      <div className="experiment-table-wrap"><table className="experiment-table exp-refs"><thead><tr><th>연구</th><th>무엇을 어떻게 했나</th><th>이 탐구에 쓰는 법</th></tr></thead>
        <tbody>{REFS.map(r => <tr key={r.id}><td><a href={r.url} target="_blank" rel="noreferrer"><b>{r.who}</b> <ExternalLink size={11} /></a><br /><span>{r.title}</span><br /><small>{r.where}</small></td><td>{r.finding}</td><td>{r.use}</td></tr>)}</tbody></table></div>
      <div className="exp-fit">
        <div><small>정리: 탑승자 기준 (최대 수직 가속도 증가분)</small><b>0.2 g 약간 불편 · 0.4 g 상당히 불편 · 0.7 g 받아들이지 않음 · 0.9 g 불편</b><span>Kjemtrup(Kyrollos 2020 인용), TRL 614(2004), Watts(1973). 여기서 g는 중력을 뺀 증가분이므로 앱의 Δa와 같은 뜻입니다.</span></div>
        <div><small>주의</small><b>모형차의 Δa ≠ 사람이 느끼는 가속도</b><span>모형차는 서스펜션·좌석이 없고 센서가 차체에 붙어 있습니다. 위 기준을 모형차 값에 그대로 대지 말고, 아래처럼 Π로 실제 크기로 옮겨 비교하거나 "모형차 기준"을 따로 정했다고 밝히세요.</span></div>
      </div>
    </details>

    <details className="card experiment-card" open><summary><h2>실제 도로로 옮겨 보기 (Π 환산)</h2></summary>
      <p>원운동에서 생기는 가속도는 v²/R이므로 <b>Π = v²/(g·R)가 같으면 크기가 달라도 가속도(g 단위)가 같습니다</b>. 그래서 모형 실험에서 "Δa가 사람 기준에 닿는 Π"를 찾으면, 실제 방지턱 반경을 넣어 실제 속도로 바꿀 수 있습니다. 서스펜션이 없는 모형이라 실제보다 엄격한(낮은) 속도가 나오는 상한 추정입니다.</p>
      <div className="experiment-fields">
        <label>사람 기준 Δa (g)<select value={humanLimit} onChange={e => setHumanLimit(Number(e.target.value))}><option value={0.2}>0.2 g – 약간 불편 (Kjemtrup)</option><option value={0.4}>0.4 g – 상당히 불편 (Kjemtrup)</option><option value={0.7}>0.7 g – 받아들이지 않음 (TRL 614)</option><option value={0.9}>0.9 g – 불편 (Watts 1973)</option></select></label>
        <label>실제 방지턱 높이 (mm)<NumInput min={10} value={realH} onChange={e => setRealH(Math.max(1, Number(e.target.value) || 100))} /></label>
        <label>실제 방지턱 폭 (mm)<NumInput min={100} value={realW} onChange={e => setRealW(Math.max(10, Number(e.target.value) || 3600))} /></label>
        <label>실제 바퀴 반지름 (mm)<NumInput min={100} value={realWheel} onChange={e => setRealWheel(Math.max(10, Number(e.target.value) || 300))} /></label>
      </div>
      <div className="exp-fit">
        <div><small>모형 데이터의 Δa – Π 관계 (본 실험 {piFit?.count ?? 0}회)</small><b>{piFit ? `Δa = ${piFit.k.toFixed(3)} · Π^${piFit.n.toFixed(3)}` : '속도·Π가 계산된 시행이 3개 이상 필요'}</b><span>모든 방지턱을 함께 맞춘 식입니다(그래프 2의 곡선).</span></div>
        <div><small>사람 기준에 닿는 Π</small><b>{piLimit ? piLimit.toFixed(3) : '—'}</b><span>실제 방지턱 R_eff = {rReal.toFixed(2)} m (R = (w²/4 + h²)/(2h) + 바퀴 반지름)</span></div>
        <div className="exp-vmax"><small>실제 도로 환산 권장 속도</small><b>{vReal ? `${vReal.toFixed(2)} m/s ≈ ${(vReal * 3.6).toFixed(1)} km/h` : '—'}</b><span>참고: 이 방지턱의 이론 점프 속도 √(g·R_eff) = {(vJump * 3.6).toFixed(0)} km/h. 폭 3.6 m·높이 10 cm는 Watts(1973)의 3.66 m × 102 mm 혹과 같은 크기입니다(그 연구에서는 약 8 km/h에서 편함, 32 km/h 이상에서 불편).</span></div>
      </div>
    </details>

    <details className="card experiment-card"><summary><h2>CSV 파일 사전</h2></summary>
      {CSV_DOCS.map(d => <div key={d.file} className="exp-csv-doc"><h3>{d.file}</h3><p className="exp-hint">{d.how}</p>
        <table className="exp-mini"><tbody>{d.cols.map(([k, v]) => <tr key={k}><td><code>{k}</code></td><td>{v}</td></tr>)}</tbody></table></div>)}
      <p className="exp-hint">모든 CSV는 UTF-8(BOM 포함)이라 엑셀에서 바로 열면 한글이 깨지지 않습니다. 엑셀 그래프: 조건별 CSV에서 mean_speed_mps를 X, mean_peak_delta_g를 Y로 분산형 차트 → 추세선 '거듭제곱' → '수식 표시'를 켜면 앱의 거듭제곱(속도만) 식과 같은 값이 나옵니다.</p>
    </details>

    <details className="card experiment-card"><summary><h2>보고서 작성 순서 추천</h2></summary>
      <ol>
        <li>그림: 실험 장치 사진, 시행 그래프 1장(앞·뒤 피크 표시), 그래프 1, 그래프 2, 그래프 3 또는 4.</li>
        <li>표: 조건별 CSV의 평균 표(IV-3), 관계식 후보 비교표(V), 검증 표(V-5).</li>
        <li>결론 문장: 측정·분석 탭의 '자동 해석'을 복사해 다듬기. 숫자는 그대로, 해석은 내 말로.</li>
        <li>한계: 서스펜션 없음, 센서 위치 1곳, 수동 지정한 시행 수, 배터리 전압 변화, 사람 기준과 모형 값의 차이.</li>
        <li>선행 연구: 위 표의 연구를 서론(동기: 척추 골절 사례), 이론(Π, 폭·높이 비), 결론(사람 기준과 환산 속도)에 나눠 인용.</li>
      </ol>
    </details>
  </div>;
}
