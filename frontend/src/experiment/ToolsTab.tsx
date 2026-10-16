// 점검·데이터: 실제 데이터 입력 확인, 배터리, 전·후진 대칭 점검과 출력 보정, 백업·초기화·휴지통, 측정 전 점검표
import { useEffect, useRef, useState } from 'react';
import { AlertTriangle, Battery, CheckCircle2, Download, Play, RefreshCw, RotateCcw, Trash2 } from 'lucide-react';
import { fmt, outputLabel, type OutputMode } from '../services/experiment';
import { XYChart } from '../components/ExperimentCharts';
import type { ExperimentContext } from './context';

type Check = { ok: boolean; text: string };
type DataCheck = { mode: string; real_runs: number; simulated_runs: number; mean_sample_rate_hz: number | null; checks: Check[]; last_real_run: { id: string; created_at: string } | null };
type DriveSettings = { mapping: 'library' | 'duty'; trim_forward: number; trim_backward: number; max_pwm: number };
type Cmd = { direction: string; mapping: string; requested: number; applied: number; expected_duty_percent: number; trim: number; pins?: { cali_dir_value?: number[]; cali_speed_value?: number[] } | null } | null;
type SymResult = {
  axis: string; ratio_backward_to_forward: number | null; note?: string;
  forward: { peak_speed_mps: number | null; series: [number, number][] }; backward: { peak_speed_mps: number | null; series: [number, number][] };
  commands: { forward?: Cmd; backward?: Cmd }; suggestion?: { text: string; trim_backward?: number; trim_forward?: number };
};

const CHECKLIST = [
  '배터리 완충 (7.6 V 이상) – 전압이 떨어지면 같은 출력에서도 느려집니다',
  '센서가 차체에 단단히 고정됨 (폼테이프 금지)',
  '평평한 곳에서 센서 정지 상태 보정 완료',
  '축간거리·바퀴 반지름·방지턱 크기 입력',
  '방지턱이 주행로 폭 전체에 고정되어 밀리지 않음',
  '출발선 표시, 주행로 끝에 완충재',
  '무게추가 중앙에 좌우 대칭으로 고정됨',
  '조건을 바꿀 때마다 총질량을 저울로 다시 측정',
];

export default function ToolsTab({ ctx }: { ctx: ExperimentContext }) {
  const [check, setCheck] = useState<DataCheck | null>(null);
  const [battery, setBattery] = useState<{ voltage: number | null; level: string | null } | null>(null);
  const [drive, setDrive] = useState<DriveSettings | null>(null);
  const [sym, setSym] = useState<{ state: string; message: string; result: SymResult | null } | null>(null);
  const [symMode, setSymMode] = useState<OutputMode>('pwm');
  const [symOut, setSymOut] = useState(70);
  const [symSec, setSymSec] = useState(1.0);
  const [distF, setDistF] = useState<number | ''>('');
  const [distB, setDistB] = useState<number | ''>('');
  const [trash, setTrash] = useState<{ batch: string; runs: number }[]>([]);
  const [confirm, setConfirm] = useState('');
  const [scope, setScope] = useState<'current' | 'all'>('current');
  const [ticks, setTicks] = useState<boolean[]>(() => { try { return JSON.parse(localStorage.getItem('picarx-exp-checklist') || '[]'); } catch { return []; } });

  async function refresh() {
    try {
      const [c, b, d, t, s] = await Promise.all([
        ctx.request('/api/experiment/data-check', { timeoutMs: 10000 }), ctx.request('/api/battery'), ctx.request('/api/settings/drive'),
        ctx.request('/api/experiment/trash'), ctx.request('/api/experiment/tools/symmetry'),
      ]);
      setCheck(c); setBattery(b); setDrive(d); setTrash(t.batches); setSym(s);
    } catch (e) { ctx.fail(e); }
  }
  useEffect(() => { if (ctx.serverAvailable) void refresh(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [ctx.serverAvailable]);
  useEffect(() => {
    if (sym?.state !== 'running') return;
    const id = window.setInterval(async () => { try { const s = await ctx.request('/api/experiment/tools/symmetry'); setSym(s); if (s.state !== 'running') window.clearInterval(id); } catch { /* 다음 주기 */ } }, 400);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sym?.state]);

  async function saveDrive(patch: Partial<DriveSettings>) {
    try { setDrive(await ctx.request('/api/settings/drive', { method: 'PUT', body: JSON.stringify(patch) })); ctx.setMessage('주행 출력 설정을 저장했습니다(차량을 한 번 정지시킵니다).'); } catch (e) { ctx.fail(e); }
  }
  async function startSym() {
    try { setSym({ ...(await ctx.request('/api/experiment/tools/symmetry', { method: 'POST', body: JSON.stringify({ output_mode: symMode, output_value: symOut, seconds: symSec }) })), result: null }); } catch (e) { ctx.fail(e); }
  }
  const [tune, setTune] = useState<{ running: boolean; log: string[] }>({ running: false, log: [] });
  const tuneStop = useRef(false);
  /** 점검 → 제안 배율 적용 → 다시 점검을 비율이 0.95~1.05가 될 때까지(최대 4번) 반복합니다. */
  async function autoTune() {
    tuneStop.current = false;
    const log: string[] = [];
    const push = (t: string) => { log.push(t); setTune({ running: true, log: [...log] }); };
    setTune({ running: true, log: [] });
    try {
      for (let i = 1; i <= 4 && !tuneStop.current; i++) {
        push(`${i}번째 점검 중… (차가 앞뒤로 움직입니다)`);
        await ctx.request('/api/experiment/tools/symmetry', { method: 'POST', body: JSON.stringify({ output_mode: symMode, output_value: symOut, seconds: symSec }) });
        let st: { state: string; message: string; result: SymResult | null };
        do { await new Promise(res => window.setTimeout(res, 400)); st = await ctx.request('/api/experiment/tools/symmetry'); } while (st.state === 'running' && !tuneStop.current);
        setSym(st);
        const ratio = st.result?.ratio_backward_to_forward;
        if (st.state !== 'done' || !ratio) { push(`멈춤: ${st.message || '결과 없음'}`); break; }
        if (ratio >= 0.95 && ratio <= 1.05) { push(`${i}번째: 후진/전진 = ${ratio.toFixed(2)} → 5% 이내, 맞춤 완료!`); break; }
        const sug = st.result?.suggestion;
        if (!sug || (!sug.trim_backward && !sug.trim_forward)) { push(`${i}번째: 비율 ${ratio.toFixed(2)}, 제안 배율 없음`); break; }
        await saveDrive({ ...(sug.trim_backward ? { trim_backward: sug.trim_backward } : {}), ...(sug.trim_forward ? { trim_forward: sug.trim_forward } : {}) });
        push(`${i}번째: 후진/전진 = ${ratio.toFixed(2)} → ${sug.trim_backward ? `후진 배율 ${sug.trim_backward}` : `전진 배율 ${sug.trim_forward}`}로 바꾸고 다시 점검`);
        if (i === 4) push('4번 해도 5% 안에 들지 않았습니다. 줄자로 거리를 재서 아래 칸으로 맞춰 보세요.');
        await new Promise(res => window.setTimeout(res, 1500));
      }
    } catch (e) { ctx.fail(e); } finally { setTune(t => ({ ...t, running: false })); await refresh(); }
  }
  async function reset() {
    try { const r = await ctx.request('/api/experiment/reset', { method: 'POST', body: JSON.stringify({ scope, confirm }) }); setConfirm(''); ctx.setMessage(`시행 ${r.moved}개를 휴지통(${r.batch})으로 옮겼습니다. 아래에서 되돌릴 수 있습니다.`); await ctx.loadRuns(null); await refresh(); } catch (e) { ctx.fail(e); }
  }
  async function restore(batch: string) {
    try { const r = await ctx.request(`/api/experiment/trash/${batch}/restore`, { method: 'POST' }); ctx.setMessage(`${r.restored}개 시행을 되돌렸습니다.`); await ctx.loadRuns(null); await refresh(); } catch (e) { ctx.fail(e); }
  }
  async function purge(batch: string) {
    if (!window.confirm('이 휴지통 묶음을 완전히 지울까요? 되돌릴 수 없습니다.')) return;
    try { await ctx.request(`/api/experiment/trash/${batch}`, { method: 'DELETE' }); await refresh(); } catch (e) { ctx.fail(e); }
  }
  const toggleTick = (i: number) => setTicks(prev => { const next = [...prev]; next[i] = !next[i]; try { localStorage.setItem('picarx-exp-checklist', JSON.stringify(next)); } catch { /* 무시 */ } return next; });

  const r = sym?.result;
  const tapeRatio = distF && distB ? Number(distB) / Number(distF) : null;
  const cmdRow = (name: string, c: Cmd | undefined) => <tr><td>{name}</td><td>{c ? `${c.mapping === 'duty' ? '직접 듀티' : '라이브러리'} ${c.requested}` : '—'}</td><td>{c ? `${c.applied} (×${c.trim})` : '—'}</td><td>{c ? `${c.expected_duty_percent}%` : '—'}</td><td>{c?.pins?.cali_dir_value ? c.pins.cali_dir_value.join(' / ') : '—'}</td></tr>;

  return <>
    <section className="experiment-layout">
      <article className="card experiment-card">
        <div className="card-head"><div><h2>실제 데이터 입력 확인</h2><p>지금 저장되는 기록이 실제 MPU-6050 측정인지 점검합니다. 합성(모의) 데이터는 실제 목록·집계와 따로 저장됩니다.</p></div><button className="secondary" onClick={() => void refresh()}><RefreshCw size={14} /> 다시 점검</button></div>
        {check ? <>
          <div className="experiment-summary-grid"><div><small>실제 측정 시행</small><strong>{check.real_runs}</strong></div><div><small>합성 시행</small><strong>{check.simulated_runs}</strong></div><div><small>실제 시행 평균 표본 속도</small><strong>{fmt(check.mean_sample_rate_hz, 0)}</strong></div><div><small>서버 모드</small><strong>{check.mode === 'hardware' ? '실제' : '모의'}</strong></div></div>
          <ul className="exp-checks">{check.checks.map((c, i) => <li key={i} className={c.ok ? 'ok' : 'bad'}>{c.ok ? <CheckCircle2 size={14} /> : <AlertTriangle size={14} />}{c.text}</li>)}</ul>
        </> : <div className="empty-chart">점검 중…</div>}
        <div className="exp-battery"><Battery size={16} /><b>배터리 {battery?.voltage != null ? `${battery.voltage.toFixed(2)} V` : '읽기 불가'}</b><span>{battery?.level === 'low' ? '낮음 – 충전하세요 (속도가 떨어져 결과가 흔들립니다)' : battery?.level === 'medium' ? '보통 – 중요한 측정 전 충전 권장' : battery?.level === 'ok' ? '충분' : 'robot_hat 라이브러리가 있으면 표시됩니다'}</span><small>각 시행 시작 때 전압을 함께 저장합니다(시행 CSV의 battery_v).</small></div>
      </article>

      <article className="card experiment-card">
        <div className="card-head"><div><h2>측정 전 점검표</h2><p>체크 상태는 이 브라우저에만 저장됩니다.</p></div><button className="secondary" onClick={() => { setTicks([]); try { localStorage.removeItem('picarx-exp-checklist'); } catch { /* 무시 */ } }}>모두 해제</button></div>
        <ul className="exp-checklist">{CHECKLIST.map((text, i) => <li key={i}><label><input type="checkbox" checked={!!ticks[i]} onChange={() => toggleTick(i)} />{text}</label></li>)}</ul>
      </article>
    </section>

    <section className="card experiment-card">
      <div className="card-head"><div><h2>전진·후진 출력 대칭 점검</h2><p>같은 출력에서 전진과 후진 속도가 다른지 확인합니다. 차가 전진 {symSec}초 → 정지 → 후진 {symSec}초 움직이며, 앱이 실제로 모터에 보낸 듀티와 핀 값을 기록하고, 가속도를 적분해 방향별 최고 속도를 추정합니다. 앞뒤로 {symSec * 0.8 + 0.3 > 1 ? '1 m' : '50 cm'} 이상 빈 공간을 두세요.</p></div></div>
      <div className="experiment-fields">
        <label>출력 방식<select value={symMode} onChange={e => setSymMode(e.target.value as OutputMode)}><option value="pwm">라이브러리 PWM</option><option value="duty">직접 듀티비</option></select></label>
        <label>출력값<input type="number" min={1} max={100} value={symOut} onChange={e => setSymOut(Math.max(1, Math.min(100, Number(e.target.value) || 60)))} /></label>
        <label>방향별 주행 시간 (s)<input type="number" min={0.3} max={3} step={0.1} value={symSec} onChange={e => setSymSec(Math.max(0.3, Math.min(3, Number(e.target.value) || 1)))} /></label>
      </div>
      <div className="button-row"><button className="primary" disabled={!ctx.serverAvailable || ctx.busy || sym?.state === 'running'} onClick={() => void startSym()}><Play size={15} /> 대칭 점검 한 번</button>
        <button className="secondary" disabled={!ctx.serverAvailable || ctx.busy || sym?.state === 'running' || tune.running} onClick={() => void autoTune()}>자동으로 맞추기 (최대 4번 왕복)</button>
        {tune.running && <button className="secondary" onClick={() => { tuneStop.current = true; void ctx.stopNow(); }}>그만</button>}{sym && sym.state !== 'idle' && <span className="exp-hint">{sym.message}</span>}</div>
      {tune.log.length > 0 && <ol className="exp-tune-log">{tune.log.map((t, i) => <li key={i}>{t}</li>)}</ol>}
      {r && <>
        {r.note && <p className="exp-hint">{r.note}</p>}
        <div className="experiment-table-wrap"><table className="experiment-table"><thead><tr><th>방향</th><th>요청</th><th>적용 (배율)</th><th>모터에 가는 듀티</th><th>방향 보정 L / R</th></tr></thead><tbody>{cmdRow('전진', r.commands.forward)}{cmdRow('후진', r.commands.backward)}</tbody></table></div>
        <p className="exp-hint">'모터에 가는 듀티'가 전진과 후진에서 같으면 앱은 같은 출력을 보낸 것입니다. 그런데도 속도가 다르면 모터·기어·바퀴 쪽 차이이므로 아래 배율로 맞춥니다. (모터 핀을 직접 읽으면 robot_hat이 방향 핀을 입력으로 바꿔 바퀴가 떨리므로 읽지 않습니다.)</p>
        <div className="exp-fit">
          <div><small>가속도 적분 최고 속도 (진행축 {r.axis})</small><b>전진 {fmt(r.forward.peak_speed_mps, 3, 'm/s')} · 후진 {fmt(r.backward.peak_speed_mps, 3, 'm/s')}</b><span>후진/전진 = {fmt(r.ratio_backward_to_forward, 2)} · 적분 추정이라 ±10% 정도 오차가 있습니다. 아래 줄자 측정으로 확인하세요.</span></div>
          <div><small>제안</small><b>{r.suggestion?.text ?? '—'}</b>{r.suggestion && (r.suggestion.trim_backward || r.suggestion.trim_forward) ? <button className="secondary" onClick={() => void saveDrive({ ...(r.suggestion?.trim_backward ? { trim_backward: r.suggestion.trim_backward } : {}), ...(r.suggestion?.trim_forward ? { trim_forward: r.suggestion.trim_forward } : {}) })}>제안 배율 적용</button> : null}</div>
        </div>
        {(r.forward.series.length > 1 || r.backward.series.length > 1) && <XYChart name="symmetry_speed" title="전진·후진 속도 추정 (가속도 적분)" xLabel="시간 (s)" yLabel="속도 (m/s)" zeroLine
          series={[{ name: '전진', color: '#2f6fb0', line: true, dots: false, points: r.forward.series.map(([x, y]) => ({ x, y })) }, { name: '후진', color: '#d0782a', line: true, dots: false, points: r.backward.series.map(([x, y]) => ({ x, y })) }]} />}
      </>}
      <div className="exp-repeat"><div className="exp-repeat-head"><b>줄자로 확인 (가장 정확)</b><small>대칭 점검 중 전진·후진한 거리를 줄자로 재서 넣으세요(같은 시간 동안 움직인 거리 비 = 속도 비).</small></div>
        <div className="experiment-fields"><label>전진 거리 (cm)<input type="number" min={0} value={distF} onChange={e => setDistF(e.target.value === '' ? '' : Number(e.target.value))} /></label><label>후진 거리 (cm)<input type="number" min={0} value={distB} onChange={e => setDistB(e.target.value === '' ? '' : Number(e.target.value))} /></label></div>
        {tapeRatio && drive && <div className="button-row"><span className="exp-hint">후진/전진 = {tapeRatio.toFixed(2)}{Math.abs(tapeRatio - 1) <= 0.05 ? ' · 5% 이내로 대칭입니다.' : tapeRatio > 1 ? ` · 후진이 빠름 → 후진 배율 ${(drive.trim_backward / tapeRatio).toFixed(3)} 추천` : ` · 전진이 빠름 → 전진 배율 ${(drive.trim_forward * tapeRatio).toFixed(3)} 추천`}</span>
          {Math.abs(tapeRatio - 1) > 0.05 && <button className="secondary" onClick={() => void saveDrive(tapeRatio > 1 ? { trim_backward: Math.max(0.5, drive.trim_backward / tapeRatio) } : { trim_forward: Math.max(0.5, drive.trim_forward * tapeRatio) })}>추천 배율 적용</button>}</div>}
      </div>
      {drive && <div className="exp-repeat"><div className="exp-repeat-head"><b>주행 출력 설정 (수동 주행·자동 측정 공통)</b><small>최대 출력 {drive.max_pwm}. 배율은 방향별 출력에 곱해집니다(예: 후진 0.8 → 후진 출력 100을 80으로).</small></div>
        <div className="experiment-fields">
          <label>수동 주행 출력 방식<select value={drive.mapping} onChange={e => void saveDrive({ mapping: e.target.value as DriveSettings['mapping'] })}><option value="library">라이브러리 PWM (값/2+50 %)</option><option value="duty">직접 듀티비 (값 %)</option></select></label>
          <label>전진 배율<input type="number" min={0.5} max={1.5} step={0.01} defaultValue={drive.trim_forward} key={'f' + drive.trim_forward} onBlur={e => void saveDrive({ trim_forward: Math.max(0.5, Math.min(1.5, Number(e.target.value) || 1)) })} /></label>
          <label>후진 배율<input type="number" min={0.5} max={1.5} step={0.01} defaultValue={drive.trim_backward} key={'b' + drive.trim_backward} onBlur={e => void saveDrive({ trim_backward: Math.max(0.5, Math.min(1.5, Number(e.target.value) || 1)) })} /></label>
        </div>
        <div className="button-row"><button className="secondary" onClick={() => void saveDrive({ trim_forward: 1, trim_backward: 1 })}><RotateCcw size={14} /> 배율 1.0으로</button><span className="exp-hint">현재: {drive.mapping === 'duty' ? '직접 듀티' : '라이브러리'} · 전진 ×{drive.trim_forward} · 후진 ×{drive.trim_backward} · 예: {outputLabel('pwm', 100)} → 전진 {Math.round(100 * drive.trim_forward)} / 후진 {Math.round(100 * drive.trim_backward)}</span></div>
      </div>}
    </section>

    <section className="experiment-layout">
      <article className="card experiment-card">
        <div className="card-head"><div><h2>백업과 초기화</h2><p>초기화는 기록을 바로 지우지 않고 휴지통으로 옮깁니다. 중요한 측정 전후로 백업 ZIP을 받아 두세요.</p></div></div>
        <div className="button-row"><button className="secondary" disabled={!ctx.serverAvailable} onClick={() => ctx.download('/api/experiment/backup.zip', 'picarx_experiment_backup.zip')}><Download size={14} /> 전체 백업 ZIP (원자료 포함)</button></div>
        <div className="experiment-fields">
          <label>초기화 범위<select value={scope} onChange={e => setScope(e.target.value as 'current' | 'all')}><option value="current">현재 모드의 기록만 ({ctx.simulated ? '합성' : '실제'})</option><option value="all">실제·합성 전부</option></select></label>
          <label>확인 문구 '초기화' 입력<input value={confirm} onChange={e => setConfirm(e.target.value)} placeholder="초기화" /></label>
        </div>
        <div className="button-row"><button className="emergency exp-stop" disabled={confirm.trim() !== '초기화' || ctx.busy} onClick={() => void reset()}><Trash2 size={14} /> 실험 기록 초기화</button></div>
      </article>
      <article className="card experiment-card">
        <div className="card-head"><div><h2>휴지통</h2><p>초기화한 기록 묶음입니다. 되돌리기를 누르면 그대로 복구됩니다.</p></div></div>
        {trash.length === 0 ? <div className="empty-chart">비어 있음</div> : <ul className="exp-trash">{trash.map(t => <li key={t.batch}><span>{t.batch} · 시행 {t.runs}개</span><button className="secondary" onClick={() => void restore(t.batch)}><RotateCcw size={14} /> 되돌리기</button><button className="secondary" onClick={() => void purge(t.batch)}>완전 삭제</button></li>)}</ul>}
      </article>
    </section>
  </>;
}
