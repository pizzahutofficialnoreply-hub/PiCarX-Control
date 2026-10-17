// 초음파 거리 → 속력 그래프(한 회씩 보기). 앞바퀴 충격 시각(MPU)과 초음파 속력 골을 함께 표시합니다.
import { fmt, type RunDetail, type RunSummary } from '../services/experiment';
import { XYChart } from '../components/ExperimentCharts';

export default function DistanceChart({ run, detail, timeBase }: { run: RunSummary; detail: RunDetail; timeBase: 'front' | 'record' }) {
  const ds = detail.distance_series;
  const sum = run.distance_summary;
  if (!ds || !ds.t_s.length || !sum) return null;
  const a = run.analysis;
  const t0 = timeBase === 'front' && a.front_t_s != null ? a.front_t_s : 0;
  const removed = ds.t_s.map((t, i) => ({ t, d: ds.cm[i] })).filter(p => !ds.t_clean.some(tc => Math.abs(tc - p.t) < 1e-6));
  const vLines = [
    ...(a.front_t_s != null ? [{ x: a.front_t_s - t0, label: '앞바퀴(MPU)', color: '#d0782a' }] : []),
    ...(a.rear_t_s != null ? [{ x: a.rear_t_s - t0, label: '뒷바퀴', color: '#3c9a5f' }] : []),
    ...(sum.us_bump_t_s != null ? [{ x: sum.us_bump_t_s - t0, label: '초음파 속력 골', color: '#1f9e9e' }] : []),
    ...(run.slowdown?.slowed_at_s != null ? [{ x: run.slowdown.slowed_at_s - t0, label: '감속 시작', color: '#a04fa0' }] : []),
  ];
  const xl = t0 ? '앞바퀴 기준 시간 (s)' : '시간 (s, 기록 시작 기준)';
  return <div className="exp-distance">
    <h4>초음파 거리·속력 <small>{sum.rate_hz ? `${sum.rate_hz} Hz` : ''} · 튀는 값 {sum.n_removed}개 제외</small></h4>
    <div className="exp-stats">
      <div><small>방지턱 직전 속력</small><b>{fmt(sum.approach_speed_mps, 3, 'm/s')}</b><span>앞바퀴 0.5~0.1초 전 평균</span></div>
      <div><small>방지턱 통과 중 속력</small><b>{fmt(sum.bump_speed_mps, 3, 'm/s')}</b><span>바퀴 간격 속도 {fmt(a.speed_mps, 3, 'm/s')}{sum.axle_vs_us_pct != null ? ` (차이 ${sum.axle_vs_us_pct > 0 ? '+' : ''}${sum.axle_vs_us_pct}%)` : ''}</span></div>
      <div><small>방지턱에서 느려진 정도</small><b>{sum.drop_pct == null ? '—' : `${sum.drop_pct}%`}</b><span>가장 느릴 때 {fmt(sum.min_speed_mps, 3, 'm/s')}</span></div>
      <div><small>초음파로 찾은 방지턱</small><b>{sum.us_vs_imu_s == null ? '못 찾음' : `${sum.us_vs_imu_s > 0 ? '+' : ''}${(sum.us_vs_imu_s * 1000).toFixed(0)} ms`}</b><span>MPU 앞바퀴 충격 대비 속력 골 시각{sum.us_dip_pct ? ` · 골 깊이 ${sum.us_dip_pct}%` : ''}</span></div>
      <div><small>앞바퀴 충격 때 벽까지</small><b>{fmt(sum.distance_at_front_cm, 1, 'cm')}</b><span>회차마다 같아야 정상(방지턱 위치 확인)</span></div>
    </div>
    <XYChart name={`ultrasonic_speed_${run.id}`} title="초음파 거리 변화로 구한 속력" xLabel={xl} yLabel="속력 (m/s)" yZero
      series={[{ name: '속력(앞뒤 0.15초 직선 맞춤)', color: '#1f9e9e', line: true, dots: false, width: 2, points: ds.t_clean.map((t, i) => ({ x: t - t0, y: ds.speed_mps[i] })).filter((p): p is { x: number; y: number } => p.y != null) },
        ...(a.speed_mps ? [{ name: '바퀴 간격으로 구한 통과 속도', color: '#d0782a', line: true, dots: false, dash: '6 3', points: a.front_t_s != null && a.rear_t_s != null ? [{ x: a.front_t_s - t0, y: a.speed_mps }, { x: a.rear_t_s - t0, y: a.speed_mps }] : [] }] : [])]}
      vLines={vLines}
      caption="거리(cm)를 시간으로 미분해 속력을 구했습니다. 방지턱을 오를 때 속력이 잠깐 움푹 줄어드는 곳(골)이 초음파로 본 방지턱입니다. 감속 제어를 했다면 보라색 선 이후 속력이 줄어드는지 보세요." />
    <XYChart name={`ultrasonic_distance_${run.id}`} title="벽까지 거리" xLabel={xl} yLabel="거리 (cm)"
      series={[{ name: '사용한 값', color: '#2f6fb0', line: true, dots: true, points: ds.t_clean.map((t, i) => ({ x: t - t0, y: ds.cm_clean[i] })) },
        ...(removed.length ? [{ name: '튀어서 뺀 값', color: '#c62828', dots: true, points: removed.map(p => ({ x: p.t - t0, y: p.d })) }] : [])]}
      vLines={vLines} />
  </div>;
}
