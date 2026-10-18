// 라인 추종: 바닥에 붙인 테이프를 그레이스케일 센서 3개로 따라갑니다(측정과 별개로 시험 가능).
import { useEffect, useState } from 'react';
import { CircleStop, Play, Route } from 'lucide-react';
import { fmt, type LineSettings, type OutputMode } from '../services/experiment';
import { XYChart } from '../components/ExperimentCharts';
import type { ExperimentContext } from './context';
import NumInput from '../components/NumInput';
import { usePersistentState } from '../services/persist';
import HelpBox from '../components/HelpBox';

type LineStatus = {
  raw: number[] | null; weights: number[] | null; position: number | null; settings: LineSettings; calibrated: boolean; error: string | null;
  cross?: boolean | null; check?: string[]; auto_cal?: { state: string; message: string };
  runner: { state: string; message: string; elapsed_s: number | null; last: { steer_deg?: number; lost?: boolean } | null; log: { t: number; position: number | null; steer_deg: number; lost: boolean }[] };
};

export default function LineTab({ ctx }: { ctx: ExperimentContext }) {
  const [status, setStatus] = useState<LineStatus | null>(null);
  const [draft, setDraft] = useState<LineSettings | null>(null);
  const [mode, setMode] = usePersistentState<OutputMode>('line-mode', 'pwm');
  const [output, setOutput] = usePersistentState('line-output', 40);
  const [seconds, setSeconds] = usePersistentState('line-seconds', 5);

  useEffect(() => {
    if (!ctx.serverAvailable) return;
    let stop = false;
    const tick = async () => {
      try {
        const s = await ctx.request('/api/experiment/line/status') as LineStatus;
        if (!stop) { setStatus(s); setDraft(d => d ?? s.settings); }
      } catch (e) { if (!stop) ctx.fail(e); }
    };
    void tick();
    const id = window.setInterval(() => void tick(), 300);
    return () => { stop = true; window.clearInterval(id); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ctx.serverAvailable]);

  async function calibrate(target: 'floor' | 'line') {
    try { const r = await ctx.request('/api/experiment/line/calibrate', { method: 'POST', body: JSON.stringify({ target }) }); setDraft(r.settings); ctx.setMessage(`${target === 'floor' ? '바닥' : '테이프'} 값을 저장했습니다: ${r.values.join(', ')}`); } catch (e) { ctx.fail(e); }
  }
  const [autoCal, setAutoCal] = useState(false);
  async function autoCalibrate() {
    setAutoCal(true);
    try {
      const r = await ctx.request('/api/experiment/line/auto_calibrate', { method: 'POST', timeoutMs: 20000 });
      setDraft(r.settings);
      ctx.setMessage(r.check?.length ? `자동 보정했지만 확인이 필요합니다: ${r.check.join(' ')}` : `자동 보정 완료 · 바닥 ${r.floor.join(' / ')} · 테이프 ${r.line.join(' / ')}`);
    } catch (e) { ctx.fail(e); } finally { setAutoCal(false); }
  }
  async function save() {
    if (!draft) return;
    try { setDraft(await ctx.request('/api/experiment/line/settings', { method: 'PUT', body: JSON.stringify(draft) })); ctx.setMessage('라인 추종 설정을 저장했습니다.'); } catch (e) { ctx.fail(e); }
  }
  async function start() {
    try { await ctx.request('/api/experiment/line/start', { method: 'POST', body: JSON.stringify({ output_mode: mode, output_value: output, seconds }) }); } catch (e) { ctx.fail(e); }
  }

  const pos = status?.position;
  const running = status?.runner.state === 'running';
  const log = status?.runner.log ?? [];
  const usingLine = ctx.settings.steering_source === 'line';

  return <>
    <section className="experiment-layout">
      <article className="card experiment-card">
        <div className="card-head"><div><h2><Route size={16} /> 테이프 인식</h2><p>차 아래 그레이스케일 센서 3개(왼쪽·가운데·오른쪽)로 바닥에 붙인 테이프의 위치를 찾습니다. 밝은 바닥에 검은 절연 테이프(폭 15~20 mm)가 가장 잘 됩니다. 반대(어두운 바닥·흰 테이프)도 보정만 하면 됩니다.</p></div></div>
        {status?.error && <p className="diagnostic-error">{status.error}</p>}
        <div className="exp-line-sensors">{['왼쪽', '가운데', '오른쪽'].map((name, i) => <div key={name}><small>{name}</small><b>{status?.raw ? Math.round(status.raw[i]) : '—'}</b>
          <div className="exp-bar"><i style={{ width: `${Math.round((status?.weights?.[i] ?? 0) * 100)}%` }} /></div><span>테이프 {status?.weights ? Math.round(status.weights[i] * 100) + '%' : '보정 필요'}</span></div>)}</div>
        <div className="exp-line-pos"><span>왼쪽</span><div className="exp-line-track"><i className="center" />{pos != null && <b style={{ left: `${(pos + 1) * 50}%` }} />}</div><span>오른쪽</span></div>
        <p className="exp-hint">테이프 위치 {pos == null ? (status?.calibrated ? '못 찾음' : '보정 필요') : `${pos > 0 ? '+' : ''}${pos.toFixed(2)} (−1 왼쪽 ~ +1 오른쪽)`}{running && status?.runner.last?.steer_deg != null ? ` · 조향 ${status.runner.last.steer_deg.toFixed(1)}°` : ''}</p>
        <p className="exp-hint">가로선 판정: {status?.cross == null ? '—' : status.cross ? <b>가로선 위 ✔ (세 센서 모두 테이프)</b> : '가로선 아님'}</p>
        {(status?.check?.length ?? 0) > 0 && <ul className="exp-flags">{status!.check!.map(m => <li key={m}>{m}</li>)}</ul>}
        <div className="button-row"><button className="primary" disabled={!ctx.serverAvailable || autoCal || ctx.busy || running} onClick={() => void autoCalibrate()}>{autoCal ? '자동 보정 중…' : '자동 보정 (추천)'}</button></div>
        <p className="exp-hint"><b>자동 보정</b>: 바닥 센서를 유도선(세로 테이프) 위에 두고 누르면, 차가 제자리에서 왼쪽·오른쪽으로 25°씩 천천히 돌며 세 센서가 각각 테이프와 바닥을 지나가게 해서 센서마다 값을 따로 잡습니다(자이로 보정 필요).</p>
        <div className="button-row"><button className="secondary" disabled={!ctx.serverAvailable} onClick={() => void calibrate('floor')}>직접: ① 바닥 위에서</button><button className="secondary" disabled={!ctx.serverAvailable} onClick={() => void calibrate('line')}>직접: ② 세 센서 모두 가로선(출발선) 위에서</button></div>
        <p className="exp-hint">직접 보정할 때 ②는 <b>좁은 유도선 위가 아니라 넓은 가로선 위</b>에서 하세요. 유도선 위에서 하면 가운데 센서만 테이프를 보고 양옆 센서는 바닥값을 저장해서, 정지선을 못 알아보고 왼쪽에 있는 테이프도 모릅니다.</p>
        {draft && <p className="exp-hint">저장된 값 · 바닥 {draft.floor?.join(' / ') ?? '없음'} · 테이프 {draft.line?.join(' / ') ?? '없음'}</p>}
      </article>

      <article className="card experiment-card">
        <HelpBox id="line-gentle" title="왜 '조향 최소' 방식인가요?" defaultOpen={false}>
          <p>바닥 센서 3개는 테이프가 가운데 센서 밑에 있을 때(약 ±1 cm) 위치를 구분하지 못해, 예전 방식은 작은 흔들림에도 좌우로 크게 꺾었습니다. 그러면 차가 방지턱에 비스듬히 들어가 왼쪽·오른쪽 바퀴가 다른 시각에 부딪혀 측정이 흐트러집니다.</p>
          <ul><li><b>조향 최소</b>: 테이프 위치가 '안 꺾는 범위' 안이면 바퀴를 똑바로 두고, 그 밖으로 벗어난 만큼만 최대 8°까지 꺾습니다. 꺾는 각도도 1초에 60° 이상 빨리 바뀌지 않게 합니다.</li>
            <li>그래도 흔들리면 '안 꺾는 범위'를 0.45로 올리고, 테이프에서 자꾸 벗어나면 0.25로 낮추거나 최대 조향각을 10~12°로 올리세요.</li>
            <li>출발 전에 차를 유도선과 나란하게 놓는 것이 가장 중요합니다(코스 반복 측정은 출발선 가로 테이프로 위치를 맞춥니다).</li></ul>
        </HelpBox>
        <div className="card-head"><div><h2>추종 설정과 시험 주행</h2><p>먼저 이 탭에서 테이프만 따라가 보며 배율을 맞춘 뒤, 측정·분석 탭의 '측정 중 조향'을 <b>바닥 테이프 따라가기</b>로 바꾸면 측정 직진에도 쓰입니다(후진 복귀는 자이로 사용).</p></div></div>
        {draft && <div className="experiment-fields">
          <label>추종 방식<select value={draft.mode ?? 'gentle'} onChange={e => setDraft({ ...draft, mode: e.target.value as 'gentle' | 'normal' })}><option value="gentle">조향 최소 (가운데 근처면 똑바로, 추천)</option><option value="normal">적극 추종 (커브 코스용)</option></select></label>
          {(draft.mode ?? 'gentle') === 'gentle' && <>
            <label>안 꺾는 범위 (0~0.9, 클수록 덜 꺾음)<NumInput min={0} max={0.9} step={0.05} value={draft.deadband ?? 0.35} onChange={e => setDraft({ ...draft, deadband: Math.max(0, Math.min(0.9, Number(e.target.value))) })} /></label>
            <label>최대 조향각 (조향 최소 모드, °)<NumInput min={2} max={30} value={draft.gentle_max_deg ?? 8} onChange={e => setDraft({ ...draft, gentle_max_deg: Math.max(2, Math.min(30, Number(e.target.value) || 8)) })} /></label>
            <label>조향 바뀌는 속도 한계 (°/s)<NumInput min={10} max={600} step={10} value={draft.rate_limit_dps ?? 60} onChange={e => setDraft({ ...draft, rate_limit_dps: Math.max(10, Math.min(600, Number(e.target.value) || 60)) })} /></label></>}
          <label>직진 조향 (°, 0.1 단위 · 곧게 가려면 필요한 조향)<NumInput step={0.1} min={-10} max={10} value={draft.steer_ff_deg ?? 0} onChange={e => setDraft({ ...draft, steer_ff_deg: Math.max(-10, Math.min(10, Math.round((Number(e.target.value) || 0) * 10) / 10)) })} /></label>
          <label className="exp-check"><input type="checkbox" checked={draft.learn_steer ?? true} onChange={e => setDraft({ ...draft, learn_steer: e.target.checked })} /> 직진 조향 자동 학습 (유도선 가운데로 곧게 가는 동안의 평균 조향을 매 회 반영)</label>
          <label>방지턱 위 조향<select value={draft.bump_steer ?? 'straight'} onChange={e => setDraft({ ...draft, bump_steer: e.target.value as 'straight' | 'hold' })}><option value="straight">꺾지 않음 – 직진 고정 (충격 측정용, 추천)</option><option value="hold">자이로로 방향 유지 (턱 위에서도 꺾음)</option></select></label>
          <label className="exp-check"><input type="checkbox" checked={!!draft.camera_assist} onChange={e => setDraft({ ...draft, camera_assist: e.target.checked })} /> 카메라 보조 (앞쪽 바닥의 검은 테이프로 유도선 위치·정지선을 함께 봄)</label>
          {draft.camera_assist && <><label>카메라 아래로 숙이기 (°)<NumInput min={-45} max={20} value={ctx.settings.linecam?.tilt_deg ?? -30} onChange={e => void ctx.patchSettings({ linecam: { ...(ctx.settings.linecam ?? { tilt_deg: -30, roi_top: 0.45, flip: false, min_contrast: 40, cross_frac: 0.55, cross_wait_s: 0.5 }), tilt_deg: Math.max(-45, Math.min(20, Number(e.target.value) || -30)) } })} /></label>
            <label className="exp-check"><input type="checkbox" checked={!!ctx.settings.linecam?.flip} onChange={e => void ctx.patchSettings({ linecam: { ...(ctx.settings.linecam ?? { tilt_deg: -30, roi_top: 0.45, flip: false, min_contrast: 40, cross_frac: 0.55, cross_wait_s: 0.5 }), flip: e.target.checked } })} /> 카메라 화면 좌우가 반대</label></>}
          <label>조향 배율<NumInput min={0} max={3} step={0.1} value={draft.gain} onChange={e => setDraft({ ...draft, gain: Number(e.target.value) })} /></label>
          <label>감쇠 (s, 흔들림 줄이기)<NumInput min={0} max={1} step={0.05} value={draft.damping ?? 0.25} onChange={e => setDraft({ ...draft, damping: Number(e.target.value) })} /></label>
          <label className="exp-check"><input type="checkbox" checked={draft.auto_sign ?? true} onChange={e => setDraft({ ...draft, auto_sign: e.target.checked })} /> 조향 방향 자동 판별 (현재 {(draft.steer_sign ?? 1) > 0 ? '정방향' : '반대 방향'})</label>
          <label>조향 방향<select value={draft.steer_sign ?? 1} onChange={e => setDraft({ ...draft, steer_sign: Number(e.target.value) })}><option value={1}>정방향</option><option value={-1}>반대 방향</option></select></label>
          <label>최대 조향각 (°)<NumInput min={5} max={30} value={draft.max_steer_deg} onChange={e => setDraft({ ...draft, max_steer_deg: Number(e.target.value) })} /></label>
          <label>테이프 인식 최소값 (0~1)<NumInput min={0.05} max={0.9} step={0.05} value={draft.min_weight} onChange={e => setDraft({ ...draft, min_weight: Number(e.target.value) })} /></label>
          <label>놓친 뒤 정지까지 (s)<NumInput min={0.1} max={3} step={0.1} value={draft.lost_timeout_s} onChange={e => setDraft({ ...draft, lost_timeout_s: Number(e.target.value) })} /></label>
        </div>}
        <p className="exp-hint">자이로가 보정되어 있으면 '유도선 + 자이로' 방식으로 달립니다: 테이프 위치로 가야 할 방향을 정하고 자이로로 그 방향을 맞춥니다. 시험 주행이 끝나면 '곧게 갈 때 평균 조향'을 직진 조향으로 학습합니다. 차가 한쪽으로만 잘 안 꺾이면 설정 → 조향 중심을 0.1° 단위로 맞추세요.</p>
        <div className="button-row"><button className="secondary" onClick={() => void save()}>설정 저장</button>
          <button className={usingLine ? 'secondary selected' : 'secondary'} onClick={() => void ctx.patchSettings({ steering_source: usingLine ? 'gyro' : 'line', heading_hold: usingLine })}>{usingLine ? '측정에 테이프 사용 중 (자이로로 되돌리기)' : '측정 직진에 테이프 사용'}</button></div>
        <div className="experiment-fields">
          <label>출력 방식<select value={mode} onChange={e => setMode(e.target.value as OutputMode)}><option value="pwm">라이브러리 PWM</option><option value="duty">직접 듀티비</option></select></label>
          <label>출력값<NumInput min={1} max={100} value={output} onChange={e => setOutput(Math.max(1, Math.min(100, Number(e.target.value) || 40)))} /></label>
          <label>주행 시간 (s)<NumInput min={0.5} max={30} step={0.5} value={seconds} onChange={e => setSeconds(Math.max(0.5, Math.min(30, Number(e.target.value) || 5)))} /></label>
        </div>
        <div className="button-row"><button className="primary" disabled={!ctx.serverAvailable || !status?.calibrated || running || ctx.busy} onClick={() => void start()}><Play size={15} /> 테이프 따라 주행</button><button className="emergency exp-stop" disabled={!ctx.serverAvailable} onClick={() => void ctx.request('/api/experiment/line/stop', { method: 'POST' }).catch(ctx.fail)}><CircleStop size={15} /> 정지</button></div>
        {status && status.runner.state !== 'idle' && <div className={'exp-auto-state ' + (running ? 'driving' : status.runner.state === 'done' ? 'done' : 'error')}><span className="exp-phase">{running ? '주행' : status.runner.state === 'done' ? '완료' : '정지'}</span><span>{status.runner.message}</span>{running && <b>{fmt(status.runner.elapsed_s, 1, 's')}</b>}</div>}
      </article>
    </section>
    {log.length > 1 && <section className="card experiment-card">
      <XYChart name="line_follow_log" title="라인 추종 기록 – 테이프 위치와 조향" xLabel="시간 (s)" yLabel="위치 (−1~+1) / 조향 (÷10, °)" zeroLine
        series={[{ name: '테이프 위치', color: '#2f6fb0', line: true, dots: false, points: log.filter(p => p.position != null).map(p => ({ x: p.t, y: p.position as number })) },
          { name: '조향 ÷ 10', color: '#d0782a', line: true, dots: false, points: log.map(p => ({ x: p.t, y: p.steer_deg / 10 })) }]}
        caption="위치가 0 근처에서 작게 흔들리면 잘 따라가는 것입니다. 좌우 흔들림이 점점 커지면 감쇠를 올리거나 조향 배율을 낮추고, 커브에서 벗어나면 배율을 높이세요." />
    </section>}
  </>;
}
