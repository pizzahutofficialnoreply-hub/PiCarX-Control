// 실험 관리 화면 – 보고서 III~V장의 절차(조건 입력 → 자동 측정 주행 → 시행 확인 → 조건별 집계 → 회귀식)를 따릅니다.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Activity, AlertTriangle, Download, FlaskConical, Play, RefreshCw, Save, Trash2 } from 'lucide-react';
import { apiDownload } from './services/api';
import {
  bumpRadiusMm, effectiveDuty, fmt, G, outputLabel, predictVmax, readCondition, writeCondition,
  type AutoRunState, type BumpProfile, type ExperimentCondition, type ExperimentSettings, type RunDetail, type RunsResponse,
} from './services/experiment';
import { PiChart, RunTraceChart, SpeedImpactChart } from './components/ExperimentCharts';

type ImpactInfo = { available: boolean; calibrated: boolean; recording?: boolean; recorded_sample_count?: number } | null;
type Props = {
  request: (path: string, options?: RequestInit & { timeoutMs?: number }) => Promise<any>;
  controlMode: 'mock' | 'hardware';
  connected: boolean;
  serverMode: string | undefined;
  vehicleConnected: boolean;
  impact: ImpactInfo;
  onOpenSensors: () => void;
};
const BUSY: AutoRunState['state'][] = ['pre_roll', 'driving', 'tail', 'analyzing'];
const PHASE_LABEL: Record<string, string> = { pre_roll: '정지 기록', driving: '주행', tail: '정지 후 기록', analyzing: '분석·저장', done: '완료', error: '오류', aborted: '중단', idle: '대기' };

export default function ExperimentPage({ request: requestProp, controlMode, connected, serverMode, vehicleConnected, impact, onOpenSensors }: Props) {
  const [settings, setSettings] = useState<ExperimentSettings | null>(null);
  const [draft, setDraft] = useState<ExperimentSettings | null>(null);
  const [condition, setConditionState] = useState<ExperimentCondition>(readCondition);
  const [data, setData] = useState<RunsResponse | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [auto, setAuto] = useState<AutoRunState | null>(null);
  const [analysisBump, setAnalysisBump] = useState<string>('');
  const [calcMass, setCalcMass] = useState<number | ''>('');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [savingSettings, setSavingSettings] = useState(false);
  const pollRef = useRef<number>();
  // App은 센서 폴링으로 자주 다시 그려지므로 request 함수를 ref로 고정해 불필요한 재요청을 막습니다.
  const requestRef = useRef(requestProp);
  requestRef.current = requestProp;
  const request = useCallback((path: string, options?: RequestInit & { timeoutMs?: number }) => requestRef.current(path, options), []);

  const serverAvailable = controlMode === 'hardware' && connected;
  const simulated = serverMode === 'mock';
  const busy = !!auto && BUSY.includes(auto.state);
  const setCondition = (update: Partial<ExperimentCondition>) => setConditionState(prev => { const next = { ...prev, ...update }; writeCondition(next); return next; });
  const fail = (e: unknown) => setError(e instanceof Error ? e.message : String(e));

  const loadRuns = useCallback(async (select?: string | null) => {
    const result = await request('/api/experiment/runs', { timeoutMs: 10000 }) as RunsResponse;
    setData(result);
    const keep = select !== undefined ? select : selectedId;
    const nextId = keep && result.runs.some(r => r.id === keep) ? keep : result.runs[0]?.id ?? null;
    setSelectedId(nextId);
    return result;
  }, [request, selectedId]);

  useEffect(() => {
    if (!serverAvailable) return;
    let cancelled = false;
    (async () => {
      try {
        const s = await request('/api/experiment/settings') as ExperimentSettings;
        if (cancelled) return;
        setSettings(s); setDraft(s);
        setConditionState(prev => {
          const next = { ...prev };
          if (!next.base_mass_g && s.base_mass_g) next.base_mass_g = s.base_mass_g;
          if (!s.bumps.some(b => b.id === next.bump_id) && s.bumps[0]) next.bump_id = s.bumps[0].id;
          writeCondition(next);
          return next;
        });
        await loadRuns();
        const state = await request('/api/experiment/auto-run') as AutoRunState;
        if (!cancelled) { setAuto(state); if (BUSY.includes(state.state)) startPolling(); }
      } catch (e) { if (!cancelled) fail(e); }
    })();
    return () => { cancelled = true; if (pollRef.current) window.clearInterval(pollRef.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serverAvailable, serverMode]);

  useEffect(() => {
    if (!selectedId || !serverAvailable) { setDetail(null); return; }
    let cancelled = false;
    request(`/api/experiment/runs/${selectedId}`, { timeoutMs: 10000 }).then(d => { if (!cancelled) setDetail(d as RunDetail); }).catch(e => { if (!cancelled) fail(e); });
    return () => { cancelled = true; };
  }, [selectedId, serverAvailable, request, data]);

  function startPolling() {
    if (pollRef.current) window.clearInterval(pollRef.current);
    pollRef.current = window.setInterval(async () => {
      try {
        const state = await request('/api/experiment/auto-run') as AutoRunState;
        setAuto(state);
        if (!BUSY.includes(state.state)) {
          window.clearInterval(pollRef.current); pollRef.current = undefined;
          if (state.state === 'done' || state.state === 'aborted') {
            await loadRuns(state.result?.id ?? null);
            if (state.state === 'done') setConditionState(prev => { const next = { ...prev, trial: prev.trial + 1 }; writeCondition(next); return next; });
          }
        }
      } catch (e) { fail(e); }
    }, 300);
  }

  const totalMass = condition.base_mass_g + condition.added_mass_g;
  const maxOutput = settings?.max_output ?? 100;
  const conditionError = !condition.base_mass_g ? '차체 기본 질량(저울 측정값)을 입력하세요.'
    : condition.output_value <= 0 || condition.output_value > maxOutput ? `출력값은 1~${maxOutput} 사이여야 합니다.`
    : !settings?.bumps.some(b => b.id === condition.bump_id) ? '방지턱을 선택하세요.' : '';
  const sensorReady = simulated || (impact?.available && impact.calibrated && !impact.recording);
  const canAutoRun = serverAvailable && vehicleConnected && !busy && !conditionError && sensorReady;

  async function startAutoRun() {
    setError(''); setMessage('');
    try {
      const body = { condition: { ...condition, total_mass_g: undefined } };
      setAuto(await request('/api/experiment/auto-run', { method: 'POST', body: JSON.stringify(body) }) as AutoRunState);
      startPolling();
    } catch (e) { fail(e); }
  }
  async function saveManualRecording() {
    setError('');
    try {
      const run = await request('/api/experiment/save-recording', { method: 'POST', body: JSON.stringify({ condition }), timeoutMs: 15000 });
      setMessage('수동 측정 기록을 저장했습니다.');
      await loadRuns(run.id);
      setCondition({ trial: condition.trial + 1 });
    } catch (e) { fail(e); }
  }
  async function saveSettings(reanalyze: boolean) {
    if (!draft) return;
    setSavingSettings(true); setError('');
    try {
      const body = { ...draft, bumps: draft.bumps.map(({ id, name, height_mm, width_mm }) => ({ id, name, height_mm, width_mm })) };
      delete (body as Partial<ExperimentSettings>).max_output;
      const s = await request('/api/experiment/settings', { method: 'PUT', body: JSON.stringify(body) }) as ExperimentSettings;
      setSettings(s); setDraft(s);
      if (reanalyze) {
        const r = await request('/api/experiment/reanalyze', { method: 'POST', timeoutMs: 60000 });
        setMessage(`설정을 저장하고 ${r.reanalyzed}개 시행을 다시 분석했습니다.`);
        await loadRuns();
      } else setMessage('설정을 저장했습니다. 새 측정부터 적용됩니다.');
    } catch (e) { fail(e); } finally { setSavingSettings(false); }
  }
  async function toggleExcluded(id: string, excluded: boolean) {
    try { await request(`/api/experiment/runs/${id}`, { method: 'PATCH', body: JSON.stringify({ excluded }) }); await loadRuns(id); } catch (e) { fail(e); }
  }
  async function deleteRun(id: string) {
    if (!window.confirm('이 시행 기록과 원자료를 삭제할까요? 되돌릴 수 없습니다.')) return;
    try { await request(`/api/experiment/runs/${id}`, { method: 'DELETE' }); await loadRuns(null); } catch (e) { fail(e); }
  }
  const download = (path: string, name: string) => { apiDownload(path, name).catch(fail); };
  const updateBump = (index: number, patch: Partial<BumpProfile>) => setDraft(d => d ? { ...d, bumps: d.bumps.map((b, i) => i === index ? { ...b, ...patch } : b) } : d);

  // ------------------------------------------------------------ 분석 데이터
  const runs = data?.runs ?? [];
  const bumpIds = useMemo(() => Array.from(new Set(runs.map(r => r.condition.bump_id))).sort(), [runs]);
  const bump = analysisBump && bumpIds.includes(analysisBump) ? analysisBump : bumpIds[0] ?? '';
  const bumpRuns = runs.filter(r => r.condition.bump_id === bump && !r.excluded);
  const bumpGroups = (data?.groups ?? []).filter(g => g.bump_id === bump);
  const fit = data?.fits?.[bump];
  const aLimit = settings?.a_limit_g ?? null;
  const masses = Array.from(new Set(bumpGroups.map(g => g.total_mass_g))).sort((a, b) => a - b);
  const vmaxMass = calcMass === '' ? (masses[0] ?? totalMass) : calcMass;
  const vmax = aLimit ? predictVmax(fit, aLimit, vmaxMass) : null;
  const bumpInfo = settings?.bumps.find(b => b.id === bump);
  const rEff = bumpInfo?.radius_mm && settings?.wheel_radius_mm ? (bumpInfo.radius_mm + settings.wheel_radius_mm) / 1000 : null;
  const selected = runs.find(r => r.id === selectedId) ?? null;

  if (controlMode === 'mock') {
    return <><section className="page-heading"><div><div className="eyebrow">실험 보조 도구</div><h1>실험 관리</h1><p>방지턱 통과 충격 실험의 조건 입력, 자동 측정 주행, 그래프 확인·저장, 관계식 도출을 한 곳에서 합니다.</p></div></section>
      <section className="card placeholder"><div className="placeholder-icon"><FlaskConical /></div><h2>서버 연결이 필요합니다</h2>
        <p>브라우저 시뮬레이션 모드에는 실험 서버가 없습니다. 실험 기록은 Raspberry Pi 디스크에 저장됩니다.</p>
        <p>차량 없이 화면과 그래프를 확인하려면 PC에서 백엔드를 모의 모드(<code>PICARX_MODE=mock</code>)로 실행하고, 설정 → 제어 모드에서 <b>Raspberry Pi 연결</b>을 선택해 <code>http://localhost:8000</code>에 연결하세요. 합성 데이터로 전체 흐름을 시험할 수 있으며, 합성 데이터는 실제 측정 기록과 섞이지 않습니다.</p></section></>;
  }

  return <>
    <section className="page-heading"><div><div className="eyebrow">실험 보조 도구{simulated ? ' · 서버 모의 모드' : ''}</div><h1>실험 관리</h1><p>조건을 입력하고 자동 측정 주행으로 기록합니다. 시행마다 앞·뒤 바퀴 피크로 속도를 계산하고, 조건별 평균과 관계식을 자동으로 구합니다.</p></div>
      <div className="button-row"><button className="secondary" disabled={!serverAvailable} onClick={() => void loadRuns().catch(fail)}><RefreshCw size={14} /> 새로고침</button></div></section>
    {simulated && <div className="simulation-strip"><span className="simulation-indicator" />서버 모의 모드 · 합성 데이터이며 실제 측정값이 아닙니다. 실제 차량 기록과 분리해 저장합니다.</div>}
    {!serverAvailable && <div className="error-banner"><AlertTriangle size={17} /><span>서버에 연결되어 있지 않습니다. 설정에서 연결 테스트를 하세요.</span></div>}
    {error && <div className="error-banner"><AlertTriangle size={17} /><span>{error}</span><button aria-label="닫기" onClick={() => setError('')}>×</button></div>}
    {message && <p className="data-tag exp-message">{message}</p>}

    <section className="experiment-layout">
      <article className="card experiment-card"><div className="card-head"><div><h2>실험 조건</h2><p>조작 변인(방지턱·질량·출력)을 정하고 자동 측정 주행을 누르세요. 차는 정지 기록 → 직진 → 정지 순으로 움직입니다.</p></div></div>
        <div className="experiment-fields">
          <label>반복 회차<input type="number" min={1} value={condition.trial} onChange={e => setCondition({ trial: Math.max(1, Math.round(Number(e.target.value) || 1)) })} /></label>
          <label>방지턱<select value={condition.bump_id} onChange={e => setCondition({ bump_id: e.target.value })}>{(settings?.bumps ?? []).map(b => <option key={b.id} value={b.id}>{b.name || b.id} · h {b.height_mm} / w {b.width_mm} mm · R {fmt(b.radius_mm, 1)} mm</option>)}</select></label>
          <label>차체 기본 질량 (g)<input type="number" min={1} value={condition.base_mass_g || ''} placeholder="저울 측정값 (차체+센서)" onChange={e => setCondition({ base_mass_g: Math.max(0, Number(e.target.value) || 0) })} /></label>
          <label>추가 질량 (g)<div className="exp-inline"><input type="number" min={0} value={condition.added_mass_g} onChange={e => setCondition({ added_mass_g: Math.max(0, Number(e.target.value) || 0) })} />{[0, 150, 300].map(v => <button key={v} type="button" className={condition.added_mass_g === v ? 'secondary selected' : 'secondary'} onClick={() => setCondition({ added_mass_g: v })}>+{v}</button>)}</div></label>
          <label>출력 방식<select value={condition.output_mode} onChange={e => setCondition({ output_mode: e.target.value as ExperimentCondition['output_mode'] })}><option value="pwm">라이브러리 PWM (실제 듀티 50~100%)</option><option value="duty">직접 듀티비 (저속까지 사용)</option></select></label>
          <label>출력값 ({condition.output_mode === 'duty' ? '듀티 %' : 'PWM'})<input type="number" min={1} max={maxOutput} value={condition.output_value} onChange={e => setCondition({ output_value: Math.max(0, Math.min(maxOutput, Math.round(Number(e.target.value) || 0))) })} /></label>
          <label>영상 실측 속도 (m/s, 선택)<input type="number" min={0} step={0.01} value={condition.video_speed_mps ?? ''} placeholder="슬로모션 교차 검증" onChange={e => setCondition({ video_speed_mps: e.target.value === '' ? null : Math.max(0, Number(e.target.value) || 0) })} /></label>
          <label>실험 메모<input value={condition.note} onChange={e => setCondition({ note: e.target.value })} placeholder="예: 배터리 완충 직후" /></label>
        </div>
        <div className="experiment-mass"><span>총 질량</span><strong>{totalMass.toLocaleString()} g</strong><small>실제 모터 듀티 약 {effectiveDuty(condition.output_mode, condition.output_value)}% · 축간거리 {settings?.wheelbase_mm ? `${settings.wheelbase_mm} mm` : '미입력 (속도 계산 불가)'}</small></div>
        {!simulated && !sensorReady && <div className="warning">센서 화면에서 MPU-6050 정지 상태 보정을 먼저 하세요. {impact?.recording ? '수동 측정이 진행 중입니다.' : ''} <button className="text-button" onClick={onOpenSensors}>센서 화면으로</button></div>}
        {conditionError && <p className="diagnostic-error">{conditionError}</p>}
        <div className="button-row"><button className="primary" disabled={!canAutoRun} onClick={() => void startAutoRun()}><Play size={15} /> 자동 측정 주행</button>
          {!simulated && <button className="secondary" disabled={!serverAvailable || busy || !!conditionError || !impact?.recorded_sample_count} onClick={() => void saveManualRecording()}><Save size={15} /> 수동 측정 기록 저장</button>}</div>
        {auto && auto.state !== 'idle' && <div className={'exp-auto-state ' + auto.state}><span className="exp-phase">{PHASE_LABEL[auto.state] ?? auto.state}</span><span>{auto.message}</span>{auto.phase_remaining_s != null && busy && <b>{auto.phase_remaining_s.toFixed(1)} s</b>}</div>}
        <p className="data-tag">긴급 정지가 필요하면 상단 주행 화면의 긴급 정지 또는 정지 API를 쓰세요. 중단된 시행은 '제외' 상태로 저장됩니다.</p>
      </article>

      <article className="card experiment-card"><div className="card-head"><div><h2>차량·분석 설정</h2><p>축간거리와 바퀴 반지름은 자로 실측해 입력하세요. 속도와 Π 계산에 쓰입니다.</p></div></div>
        {draft && <>
          <div className="experiment-fields">
            <label>축간거리 L (mm)<input type="number" min={10} value={draft.wheelbase_mm ?? ''} placeholder="앞·뒤 바퀴 중심 거리" onChange={e => setDraft({ ...draft, wheelbase_mm: e.target.value === '' ? null : Number(e.target.value) })} /></label>
            <label>바퀴 반지름 r (mm)<input type="number" min={5} value={draft.wheel_radius_mm ?? ''} onChange={e => setDraft({ ...draft, wheel_radius_mm: e.target.value === '' ? null : Number(e.target.value) })} /></label>
            <label>기본 질량 기본값 (g)<input type="number" min={1} value={draft.base_mass_g ?? ''} onChange={e => setDraft({ ...draft, base_mass_g: e.target.value === '' ? null : Number(e.target.value) })} /></label>
            <label>허용 최대 가속도 a_lim (g)<input type="number" min={1.01} step={0.05} value={draft.a_limit_g ?? ''} placeholder="예: 2.0" onChange={e => setDraft({ ...draft, a_limit_g: e.target.value === '' ? null : Number(e.target.value) })} /></label>
            <label>피크 검출 기준 Δa (g)<input type="number" min={0.02} step={0.05} value={draft.threshold_delta_g} onChange={e => setDraft({ ...draft, threshold_delta_g: Number(e.target.value) || 0.3 })} /></label>
            <label>피크 최소 간격 (s)<input type="number" min={0.005} step={0.005} value={draft.min_gap_s} onChange={e => setDraft({ ...draft, min_gap_s: Number(e.target.value) || 0.03 })} /></label>
            <label>지면 이탈 기준 (g)<input type="number" min={0} max={0.9} step={0.05} value={draft.jump_g} onChange={e => setDraft({ ...draft, jump_g: Number(e.target.value) })} /></label>
            <label>정지 기록 / 주행 / 정지 후 (s)<div className="exp-inline"><input type="number" min={0.3} step={0.1} value={draft.pre_roll_s} onChange={e => setDraft({ ...draft, pre_roll_s: Number(e.target.value) || 1 })} /><input type="number" min={0.3} max={6} step={0.1} value={draft.run_seconds} onChange={e => setDraft({ ...draft, run_seconds: Number(e.target.value) || 2.5 })} /><input type="number" min={0.1} step={0.1} value={draft.tail_s} onChange={e => setDraft({ ...draft, tail_s: Number(e.target.value) || 0.6 })} /></div></label>
          </div>
          <div className="exp-bumps"><div className="exp-bumps-head"><b>방지턱 형상</b><button type="button" className="text-button" disabled={draft.bumps.length >= 4} onClick={() => setDraft({ ...draft, bumps: [...draft.bumps, { id: String.fromCharCode(65 + draft.bumps.length), name: '', height_mm: 10, width_mm: 40 }] })}>+ 추가</button></div>
            {draft.bumps.map((b, i) => <div className="exp-bump-row" key={i}><input aria-label="ID" value={b.id} maxLength={12} onChange={e => updateBump(i, { id: e.target.value.replace(/[^A-Za-z0-9_-]/g, '') })} /><input aria-label="이름" value={b.name} placeholder="이름" onChange={e => updateBump(i, { name: e.target.value })} /><label>h<input type="number" min={1} value={b.height_mm ?? ''} onChange={e => updateBump(i, { height_mm: Number(e.target.value) || null })} /></label><label>w<input type="number" min={1} value={b.width_mm ?? ''} onChange={e => updateBump(i, { width_mm: Number(e.target.value) || null })} /></label><span>R {fmt(bumpRadiusMm(b.height_mm, b.width_mm), 1)} mm</span><button type="button" className="text-button" disabled={draft.bumps.length <= 1} onClick={() => setDraft({ ...draft, bumps: draft.bumps.filter((_, n) => n !== i) })}>삭제</button></div>)}
            <small>곡률 반경 R = (w²/4 + h²) / (2h)</small></div>
          <div className="button-row"><button className="secondary" disabled={savingSettings} onClick={() => void saveSettings(false)}>설정 저장</button><button className="primary" disabled={savingSettings || busy} onClick={() => void saveSettings(true)}>저장 후 전체 재분석</button></div>
        </>}
      </article>
    </section>

    <section className="experiment-runs"><div className="section-subhead"><h2>시행 결과</h2><p>표에서 시행을 누르면 원자료 그래프와 계산값을 확인할 수 있습니다. 이상한 시행은 '제외'로 표시하면 집계에서 빠집니다.</p></div>
      {selected && detail && detail.id === selected.id ? <article className="card experiment-run">
        <div className="card-head"><div><h3>{selected.condition.bump_id} · {selected.condition.total_mass_g.toLocaleString()} g · {outputLabel(selected.condition.output_mode, selected.condition.output_value)} · {selected.condition.trial}회차{selected.excluded ? ' · 제외됨' : ''}{selected.simulated ? ' · 합성' : ''}</h3><p>{new Date(selected.created_at).toLocaleString()} · 표본 {selected.analysis.sample_count.toLocaleString()}개 · {fmt(selected.analysis.sample_rate_hz, 0, 'Hz')}</p></div>
          <div className="button-row"><button className="secondary" onClick={() => download(`/api/experiment/runs/${selected.id}/raw.csv`, `run-${selected.id}.csv`)}><Download size={14} /> 원자료 CSV</button><button className="secondary" onClick={() => void toggleExcluded(selected.id, !selected.excluded)}>{selected.excluded ? '집계에 포함' : '집계에서 제외'}</button><button className="secondary" onClick={() => void deleteRun(selected.id)}><Trash2 size={14} /> 삭제</button></div></div>
        <RunTraceChart key={selected.id} series={detail.series} analysis={selected.analysis} thresholdDelta={Number(selected.analysis.params?.threshold_delta_g ?? settings?.threshold_delta_g ?? 0.3)} jumpG={Number(selected.analysis.params?.jump_g ?? settings?.jump_g ?? 0.2)} title={`수직 가속도 – ${selected.condition.bump_id}, ${selected.condition.total_mass_g} g, ${outputLabel(selected.condition.output_mode, selected.condition.output_value)}, ${selected.condition.trial}회차`} name={`trace_${selected.condition.bump_id}_m${selected.condition.total_mass_g}_${selected.condition.output_mode}${selected.condition.output_value}_t${selected.condition.trial}`} />
        <div className="exp-stats">
          <div><small>최대 수직 가속도</small><b>{fmt(selected.analysis.peak_g, 3, 'g')}</b><span>Δa {fmt(selected.analysis.peak_delta_g, 3, 'g')}</span></div>
          <div><small>통과 속도 v</small><b>{fmt(selected.analysis.speed_mps, 3, 'm/s')}</b><span>Δt {selected.analysis.axle_dt_s == null ? '—' : (selected.analysis.axle_dt_s * 1000).toFixed(1) + ' ms'}{selected.condition.video_speed_mps ? ` · 영상 ${selected.condition.video_speed_mps} m/s` : ''}</span></div>
          <div><small>최소 가속도 · 점프</small><b>{fmt(selected.analysis.min_g, 3, 'g')}</b><span>{selected.analysis.jump == null ? '판정 불가' : selected.analysis.jump ? `지면 이탈${selected.analysis.airborne_s ? ` · 공중 ${(selected.analysis.airborne_s * 1000).toFixed(0)} ms` : ''}${selected.analysis.landing_g ? ` · 착지 ${selected.analysis.landing_g.toFixed(2)} g` : ''}` : '접지 유지'}</span></div>
          <div><small>충격력 F = m·a</small><b>{fmt(selected.analysis.peak_force_n, 2, 'N')}</b><span>증가분 {fmt(selected.analysis.dynamic_force_n, 2, 'N')}</span></div>
          <div><small>Π = v²/(g·R_eff)</small><b>{fmt(selected.analysis.pi, 3)}</b><span>v_c {fmt(selected.analysis.critical_speed_mps, 3, 'm/s')}</span></div>
          <div><small>앞 / 뒤 바퀴 피크</small><b>{fmt(selected.analysis.front_g, 2)} / {fmt(selected.analysis.rear_g, 2)} g</b><span>검출 피크 {selected.analysis.peak_count}개</span></div>
        </div>
        {selected.analysis.flags.length > 0 && <ul className="exp-flags">{selected.analysis.flags.map(f => <li key={f}><AlertTriangle size={13} />{f}</li>)}</ul>}
      </article> : <div className="card empty-chart">{runs.length ? '시행 데이터를 불러오는 중…' : '아직 저장된 시행이 없습니다. 조건을 입력하고 자동 측정 주행을 하세요.'}</div>}

      <article className="card experiment-card"><div className="card-head"><div><h2>시행 목록 ({runs.length})</h2><p>최신 순. 회색 행은 집계에서 제외된 시행입니다.</p></div><div className="button-row"><button className="secondary" disabled={!runs.length} onClick={() => download('/api/experiment/export/runs.csv', 'picarx_runs.csv')}><Download size={14} /> 시행 CSV</button></div></div>
        {runs.length === 0 ? <div className="empty-chart">기록 없음</div> : <div className="experiment-table-wrap"><table className="experiment-table exp-clickable"><thead><tr><th>회차</th><th>방지턱</th><th>총질량</th><th>출력</th><th>속도 (m/s)</th><th>최대 (g)</th><th>Δa (g)</th><th>Π</th><th>점프</th><th>상태</th></tr></thead>
          <tbody>{runs.map(r => <tr key={r.id} className={(r.id === selectedId ? 'selected ' : '') + (r.excluded ? 'excluded' : '')} onClick={() => setSelectedId(r.id)}><td>{r.condition.trial}</td><td>{r.condition.bump_id}</td><td>{r.condition.total_mass_g} g</td><td>{outputLabel(r.condition.output_mode, r.condition.output_value)}</td><td>{fmt(r.analysis.speed_mps, 3)}</td><td>{fmt(r.analysis.peak_g, 3)}</td><td>{fmt(r.analysis.peak_delta_g, 3)}</td><td>{fmt(r.analysis.pi, 2)}</td><td>{r.analysis.jump == null ? '—' : r.analysis.jump ? '예' : '아니오'}</td><td>{r.aborted ? '중단' : r.excluded ? '제외' : r.analysis.flags.length ? '확인 필요' : '정상'}</td></tr>)}</tbody></table></div>}
      </article>
    </section>

    <section className="experiment-runs"><div className="section-subhead"><h2>분석 및 관계식</h2><p>조건별로 5회 중 최댓값·최솟값을 하나씩 뺀 평균을 쓰고(3회 이상일 때), 로그 선형회귀로 Δa = k₀·vⁿ·mᵖ 를 구합니다.</p></div>
      {bumpIds.length === 0 ? <div className="card empty-chart">집계할 시행이 없습니다.</div> : <>
        <div className="card experiment-card"><div className="card-head"><div><h2>방지턱 {bump} 결과</h2><p>{bumpInfo ? `h ${bumpInfo.height_mm} mm · w ${bumpInfo.width_mm} mm · R ${fmt(bumpInfo.radius_mm, 1)} mm` : ''}{rEff ? ` · R_eff ${(rEff * 1000).toFixed(1)} mm · 이론 점프 속도 v_c = ${Math.sqrt(G * rEff).toFixed(3)} m/s` : ''}</p></div>
          <div className="button-row">{bumpIds.length > 1 && <div className="segmented">{bumpIds.map(id => <button key={id} type="button" className={id === bump ? 'selected' : ''} onClick={() => setAnalysisBump(id)}>방지턱 {id}</button>)}</div>}<button className="secondary" onClick={() => download('/api/experiment/export/conditions.csv', 'picarx_conditions.csv')}><Download size={14} /> 조건별 CSV</button></div></div>
          <SpeedImpactChart runs={bumpRuns} groups={bumpGroups} fit={fit} aLimit={aLimit} title={`그래프 1. 통과 속도와 최대 수직 가속도 (방지턱 ${bump})`} name={`graph1_speed_impact_${bump}`} />
          <div className="exp-fit">
            <div><small>회귀식</small><b>{fit?.formula ?? '—'}</b><span>{fit?.overall ? `R² = ${fit.overall.r2.toFixed(3)} · n = ${fit.overall.n.toFixed(3)} (n≈1 충돌 지배, n≈2 원운동 지배) · p = ${fit.overall.p.toFixed(3)} (p<0: 무거울수록 가속도 감소)` : fit?.note ?? ''}</span></div>
            {fit && fit.per_mass.length > 0 && <div><small>질량별 거듭제곱 맞춤</small>{fit.per_mass.map(p => <span key={p.mass_g}>{p.mass_g.toLocaleString()} g: Δa = {p.k.toPrecision(3)}·v^{p.n.toFixed(3)} (R² {p.r2.toFixed(3)}, {p.points}점)</span>)}</div>}
            <div className="exp-vmax"><small>최대 통과 속도 예측 v_max</small>
              <div className="exp-inline"><label>질량 (g)<input type="number" min={1} value={calcMass === '' ? vmaxMass : calcMass} onChange={e => setCalcMass(e.target.value === '' ? '' : Number(e.target.value))} /></label><span>a_lim {aLimit ? `${aLimit} g` : '미설정 (설정 카드에서 입력)'}</span></div>
              <b>{vmax == null ? '—' : `${vmax.toFixed(3)} m/s`}</b><span>v_max = ((a_lim − 1) / (k₀·m^p))^(1/n). 검증하려면 실험하지 않은 질량을 넣고 그 속도 근처에서 다시 측정해 오차율을 구하세요.</span></div>
          </div>
          <div className="experiment-table-wrap"><table className="experiment-table"><thead><tr><th>총질량</th><th>출력</th><th>시행 (사용)</th><th>평균 속도 (m/s)</th><th>평균 최대 (g)</th><th>평균 Δa (g)</th><th>범위 Δa</th><th>제외값</th><th>충격력 (N)</th><th>Π</th><th>점프</th></tr></thead>
            <tbody>{bumpGroups.map(g => <tr key={`${g.total_mass_g}-${g.output_mode}-${g.output_value}`}><td>{g.total_mass_g} g</td><td>{outputLabel(g.output_mode, g.output_value)}</td><td>{g.n_runs} ({g.n_used})</td><td>{fmt(g.mean_speed_mps, 3)}</td><td>{fmt(g.mean_peak_g, 3)}</td><td>{fmt(g.mean_peak_delta_g, 3)}</td><td>{g.kept_min_delta_g.toFixed(2)}–{g.kept_max_delta_g.toFixed(2)}</td><td>{g.dropped_delta_g.map(v => v.toFixed(2)).join(', ') || '—'}</td><td>{fmt(g.mean_force_n, 2)}</td><td>{fmt(g.mean_pi, 2)}</td><td>{g.jump_count}/{g.n_runs}</td></tr>)}</tbody></table></div>
        </div>
        <div className="card experiment-card"><div className="card-head"><div><h2>무차원 정리</h2><p>모든 방지턱의 포함된 시행을 함께 표시합니다.</p></div></div>
          <PiChart runs={runs.filter(r => !r.excluded)} title="그래프 2. 무차원수 Π와 최대 수직 가속도" name="graph2_pi_peak" />
        </div>
      </>}
    </section>
    {!serverAvailable && <div className="card empty-chart"><Activity size={18} /> 서버 연결 후 사용할 수 있습니다.</div>}
  </>;
}
