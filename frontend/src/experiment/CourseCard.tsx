// 코스 반복 측정: 출발선(가로 테이프) → 유도선 따라 → 방지턱 → 정지선(가로 테이프)에서 순차 감속·정지 →
// 후진 또는 제자리 180° 회전으로 되돌아와 반복합니다. 사람이 차를 다시 놓지 않아도 출발 위치가 매번 같습니다.
import { useEffect, useRef, useState } from 'react';
import { CircleStop, Play, Route, ScanLine } from 'lucide-react';
import NumInput from '../components/NumInput';
import HelpBox from '../components/HelpBox';
import { usePersistentState } from '../services/persist';
import { outputLabel } from '../services/experiment';
import type { ExperimentContext } from './context';

type CourseStatus = { state: string; message: string; leg: number; legs: number; phase: string; completed_run_ids: string[]; log: { t: number; leg: number; msg: string }[]; elapsed_s: number | null };
type Sensors = { raw: number[] | null; weights: number[] | null; cross: boolean | null; guide: boolean; calibrated: boolean };
const PHASE: Record<string, string> = { pre_roll: '정지 기록', driving: '측정 주행', tail: '멈춘 뒤 기록', returning: '후진 복귀', turning: '제자리 회전·위치 맞춤', rest: '대기' };

export default function CourseCard({ ctx }: { ctx: ExperimentContext }) {
  const { condition, settings } = ctx;
  const [legs, setLegs] = usePersistentState('course-legs', 6);
  const [accel, setAccel] = usePersistentState<'ramp' | 'instant'>('course-accel', 'ramp');
  const [accelS, setAccelS] = usePersistentState('course-accel-s', 0.6);
  const [decelS, setDecelS] = usePersistentState('course-decel-s', 0.3);
  const [ret, setRet] = usePersistentState<'uturn' | 'reverse' | 'none'>('course-return', 'uturn');
  const [revOut, setRevOut] = usePersistentState<number | ''>('course-rev-out', '');
  const [spinOut, setSpinOut] = usePersistentState('course-spin-out', 70);
  const [spinDir, setSpinDir] = usePersistentState<'left' | 'right'>('course-spin-dir', 'left');
  const [creep, setCreep] = usePersistentState('course-creep', 40);
  const [steering, setSteering] = usePersistentState<'line' | 'gyro' | 'none'>('course-steering', 'line');
  const [record, setRecord] = usePersistentState('course-record', true);
  const [timeout, setTimeoutS] = usePersistentState('course-timeout', 8);
  const [status, setStatus] = useState<CourseStatus | null>(null);
  const [sensors, setSensors] = useState<Sensors | null>(null);
  const poll = useRef<number>();
  const seen = useRef(0);
  const running = status?.state === 'running';

  useEffect(() => () => { if (poll.current) window.clearInterval(poll.current); }, []);
  function startPoll() {
    if (poll.current) window.clearInterval(poll.current);
    seen.current = 0;
    poll.current = window.setInterval(async () => {
      try {
        const st = await ctx.request('/api/experiment/course/status') as CourseStatus;
        setStatus(st);
        if (st.completed_run_ids.length > seen.current) { seen.current = st.completed_run_ids.length; await ctx.loadRuns(st.completed_run_ids[st.completed_run_ids.length - 1]); }
        if (st.state !== 'running') { window.clearInterval(poll.current); poll.current = undefined; }
      } catch (e) { ctx.fail(e); }
    }, 400);
  }
  async function start() {
    try {
      const body = {
        condition: { ...condition, total_mass_g: undefined }, legs, accel, accel_s: accelS, decel_s: decelS, return_mode: ret,
        reverse_output: revOut === '' ? null : revOut, spin_output: spinOut, spin_direction: spinDir, creep_duty: creep, steering, record,
        leg_timeout_s: timeout, spin_lead_deg: settings.course_spin_lead_deg ?? 12,
      };
      setStatus(await ctx.request('/api/experiment/course/start', { method: 'POST', body: JSON.stringify(body) }) as CourseStatus);
      startPoll();
    } catch (e) { ctx.fail(e); }
  }
  async function stop() { try { setStatus(await ctx.request('/api/experiment/course/stop', { method: 'POST' }) as CourseStatus); } catch (e) { ctx.fail(e); } }
  async function check() { try { setSensors(await ctx.request('/api/experiment/course/sensors') as Sensors); } catch (e) { ctx.fail(e); } }

  const calibrated = !!(settings.line?.floor && settings.line?.line);
  const revDefault = Math.min(100, Math.round(condition.output_value * 1.3));
  return <article className="card experiment-card exp-course">
    <div className="card-head"><div><h2><Route size={17} /> 코스 반복 측정 (출발선 → 방지턱 → 정지선)</h2>
      <p>바닥의 가로 테이프 두 줄(출발선·정지선)을 바닥 센서로 알아보고, 가운데 유도선을 따라 달려 매번 같은 자리에서 출발·정지합니다. 위 '실험 조건'(방지턱·질량·출력)을 그대로 씁니다.</p></div></div>
    <HelpBox id="course" title="코스 반복 측정은 이렇게 움직여요">
      <ol>
        <li><b>준비</b>: 유도선(진행 방향 검은 테이프)과 직각으로 출발선·정지선(가로 테이프, 길이 10 cm 이상)을 붙이고, 라인 추종 탭에서 바닥·테이프 보정을 합니다. 차의 바닥 센서를 출발선 위에 놓습니다(조금 어긋나면 천천히 움직여 맞춥니다).</li>
        <li><b>출발</b>: 정지 기록 후 '순차 가속'이면 정한 시간 동안 출력을 0에서 서서히 올리고, '즉시'면 바로 그 출력으로 출발합니다.</li>
        <li><b>주행</b>: 유도선을 '조향 최소' 방식으로 따라갑니다(테이프가 가운데 근처면 바퀴를 똑바로, 벗어날 때만 최대 8°까지 천천히). 방지턱 위에서 차가 기울 때는 가로 테이프 판정을 잠시 멈춥니다(들린 센서가 어둡게 읽혀 착각하지 않게).</li>
        <li><b>정지</b>: 센서가 정지선을 보는 순간부터 정한 시간 동안 출력을 0까지 서서히 줄여 멈춥니다(급정지 진동 방지). 그 회차 기록을 저장합니다.</li>
        <li><b>복귀</b>: <b>제자리 180° 회전</b>이면 뒤 두 바퀴를 반대로 돌려 자이로로 180°를 재며 돌고, 정지선이 센서 바로 밑에 오도록 천천히 맞춘 뒤 <b>반대 방향으로 다음 회차를 측정</b>합니다(왕복 모두 측정). <b>후진</b>이면 더 센 출력으로 방지턱을 후진으로 넘어 출발선을 볼 때까지 돌아온 뒤 위치를 맞춥니다(돌아오는 길은 기록 안 함).</li>
      </ol>
      <p>제자리 회전은 PiCar-X가 앞바퀴 조향 + 뒷바퀴 구동이라 완전한 제자리는 아니고 뒷바퀴 축 근처를 중심으로 돕니다. 멈춘 뒤 더 돌아가는 각을 배워 다음부터 그만큼 일찍 끕니다(지금 {(settings.course_spin_lead_deg ?? 12).toFixed(0)}°).</p>
    </HelpBox>
    {!calibrated && <p className="diagnostic-error">라인 추종 탭에서 바닥·테이프 센서 보정을 먼저 하세요.</p>}
    <div className="experiment-fields">
      <label>측정 회차 수 (구간)<NumInput min={1} max={40} value={legs} onChange={e => setLegs(Math.max(1, Math.min(40, Math.round(Number(e.target.value) || 1))))} /></label>
      <label>출발<div className="exp-inline"><div className="segmented"><button type="button" className={accel === 'ramp' ? 'selected' : ''} onClick={() => setAccel('ramp')}>순차 가속</button><button type="button" className={accel === 'instant' ? 'selected' : ''} onClick={() => setAccel('instant')}>즉시</button></div>
        {accel === 'ramp' && <><NumInput min={0.05} max={3} step={0.1} value={accelS} onChange={e => setAccelS(Math.max(0.05, Math.min(3, Number(e.target.value) || 0.6)))} /><small>초</small></>}</div></label>
      <label>정지선에서 감속 시간 (s)<NumInput min={0.05} max={3} step={0.05} value={decelS} onChange={e => setDecelS(Math.max(0.05, Math.min(3, Number(e.target.value) || 0.3)))} /></label>
      <label>복귀 방식<select value={ret} onChange={e => setRet(e.target.value as 'uturn' | 'reverse' | 'none')}><option value="uturn">제자리 180° 회전 (왕복 모두 측정)</option><option value="reverse">출발선까지 후진</option><option value="none">한 번만 (복귀 안 함)</option></select></label>
      {ret === 'reverse' && <label>후진 출력 (빈칸 = 측정 출력 × 1.3 = {revDefault})<NumInput min={1} max={100} value={revOut} placeholder={String(revDefault)} onChange={e => setRevOut(e.target.value === '' ? '' : Math.max(1, Math.min(100, Number(e.target.value))))} /></label>}
      {ret === 'uturn' && <><label>회전 출력<NumInput min={10} max={100} value={spinOut} onChange={e => setSpinOut(Math.max(10, Math.min(100, Number(e.target.value) || 70)))} /></label>
        <label>회전 방향<select value={spinDir} onChange={e => setSpinDir(e.target.value as 'left' | 'right')}><option value="left">왼쪽(반시계)</option><option value="right">오른쪽(시계)</option></select></label></>}
      <label>위치 맞춤 듀티 (%, 차가 겨우 움직일 정도)<NumInput min={15} max={100} value={creep} onChange={e => setCreep(Math.max(15, Math.min(100, Number(e.target.value) || 40)))} /></label>
      <label>주행 중 조향<select value={steering} onChange={e => setSteering(e.target.value as 'line' | 'gyro' | 'none')}><option value="line">유도선 따라 (조향 최소)</option><option value="gyro">자이로 직진</option><option value="none">조향 없음</option></select></label>
      <label>한 구간 최대 시간 (s)<NumInput min={1} max={30} value={timeout} onChange={e => setTimeoutS(Math.max(1, Math.min(30, Number(e.target.value) || 8)))} /></label>
      <label className="exp-check"><input type="checkbox" checked={record} onChange={e => setRecord(e.target.checked)} /> 각 구간을 측정 기록으로 저장 (끄면 코스 동작만 시험)</label>
    </div>
    <p className="exp-hint">조건: 방지턱 {condition.bump_id} · {(condition.base_mass_g + condition.added_mass_g).toLocaleString()} g · {outputLabel(condition.output_mode, condition.output_value)} · 회차 번호 {condition.trial}부터. 출력이 낮으면 방지턱을 못 넘을 수 있으니 처음엔 1~2구간으로 시험하세요.</p>
    <div className="button-row">
      <button className="primary" disabled={!ctx.serverAvailable || running || ctx.busy || !calibrated || !ctx.vehicleConnected} onClick={() => void start()}><Play size={15} /> 코스 시작 ({legs}구간)</button>
      <button className="emergency exp-stop" disabled={!ctx.serverAvailable} onClick={() => void stop()}><CircleStop size={15} /> 정지</button>
      <button className="secondary" disabled={!ctx.serverAvailable} onClick={() => void check()}><ScanLine size={14} /> 바닥 센서 확인</button>
      {sensors && <span className="exp-hint">{!sensors.calibrated ? '보정 필요' : sensors.cross ? '가로 테이프 위 ✔ (출발 가능)' : sensors.guide ? '유도선 위 (가로 테이프 아님)' : '바닥 위'} · {sensors.weights?.map(w => Math.round(w * 100) + '%').join(' / ')}</span>}
    </div>
    {status && status.state !== 'idle' && <div className={'exp-auto-state ' + (running ? 'driving' : status.state)}>
      <span className="exp-phase">{running ? (PHASE[status.phase] ?? '진행') : status.state === 'done' ? '완료' : status.state === 'stopped' ? '정지' : '오류'}</span>
      {status.legs > 0 && <span className="exp-cycle">{Math.min(status.leg, status.legs)}/{status.legs}</span>}<span>{status.message}</span></div>}
    {status?.log?.length ? <details className="exp-tips"><summary>진행 기록 ({status.log.length})</summary><ul>{status.log.slice(-25).map((l, i) => <li key={i}>{l.t.toFixed(1)}s · {l.leg ? `${l.leg}구간 · ` : ''}{l.msg}</li>)}</ul></details> : null}
  </article>;
}
