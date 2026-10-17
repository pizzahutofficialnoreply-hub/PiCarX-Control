// 라인 추종: 바닥에 붙인 테이프를 그레이스케일 센서 3개로 따라갑니다(측정과 별개로 시험 가능).
import { useEffect, useState } from 'react';
import { CircleStop, Play, Route } from 'lucide-react';
import { fmt, type LineSettings, type OutputMode } from '../services/experiment';
import { XYChart } from '../components/ExperimentCharts';
import type { ExperimentContext } from './context';
import NumInput from '../components/NumInput';
import { usePersistentState } from '../services/persist';

type LineStatus = {
  raw: number[] | null; weights: number[] | null; position: number | null; settings: LineSettings; calibrated: boolean; error: string | null;
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
        <div className="button-row"><button className="secondary" disabled={!ctx.serverAvailable} onClick={() => void calibrate('floor')}>① 바닥 위에 두고 보정</button><button className="secondary" disabled={!ctx.serverAvailable} onClick={() => void calibrate('line')}>② 가운데 센서를 테이프 위에 두고 보정</button></div>
        {draft && <p className="exp-hint">저장된 값 · 바닥 {draft.floor?.join(' / ') ?? '없음'} · 테이프 {draft.line?.join(' / ') ?? '없음'}</p>}
      </article>

      <article className="card experiment-card">
        <div className="card-head"><div><h2>추종 설정과 시험 주행</h2><p>먼저 이 탭에서 테이프만 따라가 보며 배율을 맞춘 뒤, 측정·분석 탭의 '측정 중 조향'을 <b>바닥 테이프 따라가기</b>로 바꾸면 측정 직진에도 쓰입니다(후진 복귀는 자이로 사용).</p></div></div>
        {draft && <div className="experiment-fields">
          <label>조향 배율<NumInput min={0} max={3} step={0.1} value={draft.gain} onChange={e => setDraft({ ...draft, gain: Number(e.target.value) })} /></label>
          <label>감쇠 (s, 흔들림 줄이기)<NumInput min={0} max={1} step={0.05} value={draft.damping ?? 0.25} onChange={e => setDraft({ ...draft, damping: Number(e.target.value) })} /></label>
          <label className="exp-check"><input type="checkbox" checked={draft.auto_sign ?? true} onChange={e => setDraft({ ...draft, auto_sign: e.target.checked })} /> 조향 방향 자동 판별 (현재 {(draft.steer_sign ?? 1) > 0 ? '정방향' : '반대 방향'})</label>
          <label>조향 방향<select value={draft.steer_sign ?? 1} onChange={e => setDraft({ ...draft, steer_sign: Number(e.target.value) })}><option value={1}>정방향</option><option value={-1}>반대 방향</option></select></label>
          <label>최대 조향각 (°)<NumInput min={5} max={30} value={draft.max_steer_deg} onChange={e => setDraft({ ...draft, max_steer_deg: Number(e.target.value) })} /></label>
          <label>테이프 인식 최소값 (0~1)<NumInput min={0.05} max={0.9} step={0.05} value={draft.min_weight} onChange={e => setDraft({ ...draft, min_weight: Number(e.target.value) })} /></label>
          <label>놓친 뒤 정지까지 (s)<NumInput min={0.1} max={3} step={0.1} value={draft.lost_timeout_s} onChange={e => setDraft({ ...draft, lost_timeout_s: Number(e.target.value) })} /></label>
        </div>}
        <p className="exp-hint">초당 50번 센서를 읽어 조향합니다. 테이프 쪽으로 세게 꺾는데도 테이프가 가운데로 오지 않으면 조향 방향이 반대라고 보고 스스로 뒤집습니다(저장됨). 좌우로 흔들리면 감쇠를 0.3~0.4로 올리거나 배율을 낮추고, 커브에서 놓치면 배율을 올리세요. 출발선·정지선(가로 테이프)은 주행 중 약 2 ms마다 따로 읽어 2번 연속 보이면 바로 멈춥니다. 예전에 저장한 감쇠가 0.15라면 0.25로 바꿔 저장하세요.</p>
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
