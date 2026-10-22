// 실험 관리 화면 – 보고서 III~V장의 절차(조건 입력 → 자동 측정 주행 → 시행 확인 → 조건별 집계 → 회귀식)를 따릅니다.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Activity, AlertTriangle, BookOpen, CheckCircle2, CircleStop, ClipboardCopy, Download, FlaskConical, Play, RefreshCw, Repeat, Route, Save, ScanLine, Sigma, Trash2, Wrench } from 'lucide-react';
import { apiDownload } from './services/api';
import {
  bumpRadiusMm, DEFAULT_SETTINGS, effectiveDuty, fmt, G, modelPredict, modelSolveSpeed, OUTLIER_LABEL, outputLabel, paramText, PURPOSE_LABEL, readCondition, writeCondition, type OutlierMethod, type Purpose,
  type AutoRunState, type BumpProfile, type ExperimentCondition, type ExperimentSettings, type RunDetail, type RunsResponse,
} from './services/experiment';
import { PiChart, SpeedImpactChart, XYChart } from './components/ExperimentCharts';
import RunViewer, { limitFor } from './experiment/RunViewer';
import PlanCompare, { cellId, type CellKey } from './experiment/PlanCompare';
import ModelExplain from './experiment/ModelExplain';
import ValidationTab from './experiment/ValidationTab';
import LineTab from './experiment/LineTab';
import ToolsTab from './experiment/ToolsTab';
import GuideTab from './experiment/GuideTab';
import TheoryTab from './experiment/TheoryTab';
import HelpBox from './components/HelpBox';
import type { ExperimentContext } from './experiment/context';
import NumInput from './components/NumInput';
import { usePersistentState } from './services/persist';

type SubTab = 'measure' | 'theory' | 'validate' | 'line' | 'tools' | 'guide';
const SUB_TABS: { id: SubTab; label: string; icon: typeof Activity }[] = [
  { id: 'measure', label: '측정·분석', icon: FlaskConical }, { id: 'theory', label: '이론·예측·제어', icon: Sigma }, { id: 'validate', label: '식 검증', icon: CheckCircle2 },
  { id: 'line', label: '라인 추종', icon: Route }, { id: 'tools', label: '점검·데이터', icon: Wrench }, { id: 'guide', label: '해석 가이드', icon: BookOpen },
];
const readSubTab = (): SubTab => { try { const v = localStorage.getItem('picarx-exp-subtab'); return (SUB_TABS.some(t => t.id === v) ? v : 'measure') as SubTab; } catch { return 'measure'; } };

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
const BUSY: AutoRunState['state'][] = ['pre_roll', 'driving', 'tail', 'analyzing', 'returning', 'turning', 'aligning', 'rest'];
const PHASE_LABEL: Record<string, string> = { pre_roll: '정지 기록', driving: '직진 측정', tail: '정지 후 기록', analyzing: '분석·저장', returning: '복귀', turning: '제자리 회전', aligning: '위치 맞춤', rest: '대기', done: '완료', error: '오류', aborted: '중단', idle: '대기' };

export default function ExperimentPage({ request: requestProp, controlMode, connected, serverMode, vehicleConnected, impact, onOpenSensors }: Props) {
  const [settings, setSettings] = useState<ExperimentSettings | null>(null);
  const [draft, setDraft] = useState<ExperimentSettings | null>(null);
  const [condition, setConditionState] = useState<ExperimentCondition>(readCondition);
  const [data, setData] = useState<RunsResponse | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [compareCells, setCompareCells] = useState<CellKey[]>([]);
  const toggleCells = (list: CellKey[]) => setCompareCells(cur => { const ids = new Set(cur.map(cellId)); const allIn = list.every(c => ids.has(cellId(c)));
    return allIn ? cur.filter(c => !list.some(l => cellId(l) === cellId(c))) : [...cur, ...list.filter(c => !ids.has(cellId(c)))]; });
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [auto, setAuto] = useState<AutoRunState | null>(null);
  const [analysisBump, setAnalysisBump] = usePersistentState<string>('analysis-bump', '');
  const [calcMass, setCalcMass] = usePersistentState<number | ''>('calc-mass', '');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [savingSettings, setSavingSettings] = useState(false);
  const [outdated, setOutdated] = useState(false);
  const [tab, setTabState] = useState<SubTab>(readSubTab);
  const setTab = (t: SubTab) => { setTabState(t); try { localStorage.setItem('picarx-exp-subtab', t); } catch { /* 무시 */ } };
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [copied, setCopied] = useState(false);
  const seenRuns = useRef(0);
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
      let s: ExperimentSettings;
      try {
        // 이전 버전 서버에는 새 항목이 없을 수 있으므로 기본값 위에 덮어씁니다.
        s = { ...DEFAULT_SETTINGS, ...(await request('/api/experiment/settings') as ExperimentSettings) };
        setOutdated(false);
      } catch (e) {
        if (cancelled) return;
        const notFound = e instanceof Error && e.message.includes('404');
        setOutdated(notFound);
        if (!notFound) fail(e);
        // 설정을 못 받아도 방지턱 선택과 조건 입력은 할 수 있게 기본값을 씁니다.
        setSettings(DEFAULT_SETTINGS); setDraft(DEFAULT_SETTINGS);
        return;
      }
      try {
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
        const done = state.completed_run_ids ?? [];
        if (done.length > seenRuns.current) {
          // 반복 측정 중에도 한 회가 끝날 때마다 목록과 그래프를 갱신합니다.
          seenRuns.current = done.length;
          await loadRuns(done[done.length - 1]);
        }
        if (!BUSY.includes(state.state)) {
          window.clearInterval(pollRef.current); pollRef.current = undefined;
          if (state.state === 'done' || state.state === 'aborted') {
            // 다음 측정 회차를 먼저 올린 뒤 목록을 새로 고칩니다.
            const count = done.length || (state.result ? 1 : 0);
            if (count) setConditionState(prev => { const next = { ...prev, trial: prev.trial + count }; writeCondition(next); return next; });
            await loadRuns(state.result?.id ?? null);
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
  const canAutoRun = !!(serverAvailable && vehicleConnected && !busy && !conditionError && sensorReady && !outdated);
  const runOpts = draft ?? DEFAULT_SETTINGS;
  const setRunOpts = (patch: Partial<ExperimentSettings>) => setDraft(d => ({ ...(d ?? DEFAULT_SETTINGS), ...patch }));
  const retMode: 'none' | 'reverse' | 'uturn' = runOpts.return_mode ?? (runOpts.return_enabled ? 'reverse' : 'none');
  const endMode: 'time' | 'stop_line' = retMode === 'uturn' ? 'stop_line' : (runOpts.end_mode ?? 'time');
  const lineReady = !!(settings?.line?.floor && settings?.line?.line);
  const [floorCheck, setFloorCheck] = useState<{ weights: number[] | null; cross: boolean | null; guide: boolean; calibrated: boolean } | null>(null);
  async function checkFloor() { try { setFloorCheck(await request('/api/experiment/course/sensors')); } catch (e) { fail(e); } }

  async function startAutoRun(override?: Partial<ExperimentCondition>, extra?: Record<string, unknown>) {
    setError(''); setMessage('');
    try {
      const body = {
        condition: { ...condition, ...(override ?? {}), total_mass_g: undefined },
        repeats: runOpts.repeats, run_seconds: runOpts.run_seconds, return_enabled: retMode !== 'none', return_mode: retMode, return_output: runOpts.return_output || null,
        return_scale: runOpts.return_scale, return_stop_on_tape: !!runOpts.return_stop_on_tape, rest_s: runOpts.rest_s, steering_source: runOpts.steering_source ?? 'gyro', kick_ms: runOpts.kick_ms ?? 0,
        end_mode: endMode, accel_mode: runOpts.accel_mode ?? 'instant', accel_s: runOpts.accel_s ?? 0.6, decel_s: runOpts.decel_s ?? 0.3,
        spin_output: runOpts.spin_output ?? 100, spin_direction: runOpts.spin_direction ?? 'left', creep_duty: runOpts.creep_duty ?? 40, leg_timeout_s: runOpts.leg_timeout_s ?? 8,
        ...(extra ?? {}),
      };
      seenRuns.current = 0;
      setAuto(await request('/api/experiment/auto-run', { method: 'POST', body: JSON.stringify(body) }) as AutoRunState);
      startPolling();
    } catch (e) { fail(e); }
  }
  async function stopNow() {
    try { await request('/api/stop', { method: 'POST' }); setMessage('정지 명령을 보냈습니다.'); } catch (e) { fail(e); }
  }
  async function applyCriterion(patch: Partial<ExperimentSettings>) { return patchSettings(patch); }
  async function patchSettings(patch: Partial<ExperimentSettings>) {
    const next = { ...(settings ?? DEFAULT_SETTINGS), ...patch };
    setDraft(d => ({ ...(d ?? next), ...patch }));
    try {
      const body = { ...next, bumps: next.bumps.map(({ id, name, height_mm, width_mm }) => ({ id, name, height_mm, width_mm })) } as Partial<ExperimentSettings>;
      delete body.max_output; delete body.a_limit_presets;
      if (!('heading_ff_forward' in patch)) { delete body.heading_ff_forward; delete body.heading_ff_backward; }
      const saved = { ...DEFAULT_SETTINGS, ...(await request('/api/experiment/settings', { method: 'PUT', body: JSON.stringify(body) }) as ExperimentSettings) };
      setSettings(saved);
      await loadRuns();
    } catch (e) { fail(e); }
  }
  const [reanalyzing, setReanalyzing] = useState(false);
  /** 진동 거르기·감지 민감도를 바꾸면 저장된 원자료로 모든 시행을 바로 다시 분석합니다. */
  async function applyAnalysisOption(patch: Partial<ExperimentSettings>) {
    setReanalyzing(true); setError('');
    try {
      await patchSettings(patch);
      const r = await request('/api/experiment/reanalyze', { method: 'POST', timeoutMs: 120000 });
      await loadRuns();
      setMessage(`분석 옵션을 바꾸고 ${r.reanalyzed}개 시행을 다시 분석했습니다.`);
    } catch (e) { fail(e); } finally { setReanalyzing(false); }
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
      const body = { ...draft, bumps: draft.bumps.map(({ id, name, height_mm, width_mm }) => ({ id, name, height_mm, width_mm })) } as Partial<ExperimentSettings>;
      delete body.max_output; delete body.heading_ff_forward; delete body.heading_ff_backward; delete body.a_limit_presets;
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
  async function deleteChecked() {
    const ids = Array.from(checked);
    if (!ids.length || !window.confirm(`선택한 시행 ${ids.length}개와 원자료를 삭제할까요? 되돌릴 수 없습니다. (전체를 되돌릴 수 있게 지우려면 점검·데이터 탭의 초기화를 쓰세요)`)) return;
    try { const r = await request('/api/experiment/runs/delete', { method: 'POST', body: JSON.stringify({ ids }) }); setChecked(new Set()); setMessage(`${r.deleted.length}개 시행을 삭제했습니다.`); await loadRuns(null); } catch (e) { fail(e); }
  }
  async function patchRun(id: string, body: Record<string, unknown>, note?: string) {
    try { await request(`/api/experiment/runs/${id}`, { method: 'PATCH', body: JSON.stringify(body), timeoutMs: 15000 }); if (note) setMessage(note); await loadRuns(id); } catch (e) { fail(e); }
  }
  const download = (path: string, name: string) => { apiDownload(path, name).catch(fail); };
  const updateBump = (index: number, patch: Partial<BumpProfile>) => setDraft(d => d ? { ...d, bumps: d.bumps.map((b, i) => i === index ? { ...b, ...patch } : b) } : d);

  // ------------------------------------------------------------ 분석 데이터
  const runs = data?.runs ?? [];
  const [purposeFilter, setPurposeFilter] = usePersistentState<'all' | Purpose>('runs-purpose-filter', 'all');
  const shown = purposeFilter === 'all' ? runs : runs.filter(r => (r.condition.purpose ?? 'main') === purposeFilter);
  async function clearPurpose(p: Purpose) {
    const n = runs.filter(r => (r.condition.purpose ?? 'main') === p).length;
    if (window.prompt(`${PURPOSE_LABEL[p]} 시행 ${n}개를 휴지통으로 옮깁니다(점검·데이터 탭 휴지통에서 되돌릴 수 있음). 계속하려면 '삭제'를 입력하세요.`)?.trim() !== '삭제') return;
    try { const r = await request('/api/experiment/runs/clear-purpose', { method: 'POST', body: JSON.stringify({ purpose: p, confirm: '삭제' }) }); setMessage(`${PURPOSE_LABEL[p]} 시행 ${r.moved}개를 휴지통으로 옮겼습니다.`); setChecked(new Set()); await loadRuns(null); } catch (e) { fail(e); }
  }
  const bumpIds = useMemo(() => Array.from(new Set(runs.map(r => r.condition.bump_id))).sort(), [runs]);
  const bump = analysisBump && bumpIds.includes(analysisBump) ? analysisBump : bumpIds[0] ?? '';
  const bumpRuns = runs.filter(r => r.condition.bump_id === bump && !r.excluded);
  const bumpGroups = (data?.groups ?? []).filter(g => g.bump_id === bump);
  const fit = data?.fits?.[bump];
  const criterion = data?.criteria?.[bump];
  const aLimit = criterion ? criterion.a_limit_g : (settings?.a_limit_mode === 'manual' ? settings?.a_limit_g ?? null : null);
  const masses = Array.from(new Set(bumpGroups.map(g => g.total_mass_g))).sort((a, b) => a - b);
  const vmaxMass = calcMass === '' ? (masses[0] ?? totalMass) : calcMass;
  const bumpInfo = settings?.bumps.find(b => b.id === bump);
  const rEff = bumpInfo?.radius_mm && settings?.wheel_radius_mm ? (bumpInfo.radius_mm + settings.wheel_radius_mm) / 1000 : null;
  const comparison = data?.models?.[bump];
  const chosenId = data?.chosen_models?.[bump] ?? null;
  const chosenModel = comparison?.models.find(m => m.id === chosenId) ?? null;
  const minSpeed = Math.min(...bumpGroups.map(g => g.mean_speed_mps ?? Infinity));
  const vmaxRaw = aLimit && chosenModel ? modelSolveSpeed(chosenModel, aLimit - 1, vmaxMass / 1000) : null;
  // 측정한 가장 느린 속도의 절반보다 낮으면 식을 범위 밖으로 늘린 값이라 보여 주지 않습니다.
  const vmaxBelow = vmaxRaw != null && Number.isFinite(minSpeed) && vmaxRaw < 0.5 * minSpeed;
  const vmaxChosen = vmaxBelow ? null : vmaxRaw;
  const interpretation = data?.interpretation?.[bump] ?? [];
  const progress = data?.progress;
  const ctx: ExperimentContext = {
    request, data, settings: settings ?? DEFAULT_SETTINGS, loadRuns, condition, setCondition, startAutoRun, auto, busy, serverAvailable,
    simulated, download, fail, setMessage, patchSettings, impact, canAutoRun, stopNow, vehicleConnected,
  };

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
    {outdated && <div className="error-banner exp-outdated"><AlertTriangle size={17} /><span><b>라즈베리파이의 백엔드가 이전 버전이라 실험 기능(/api/experiment)이 없습니다.</b> 화면은 기본값으로 표시합니다. Pi 터미널에서 <code>cd ~/PiCarX-Control && git fetch && git checkout feature/mpu6050-impact-analysis && git pull</code> 후 서버를 재시작하세요(systemd를 쓰면 <code>sudo systemctl restart picarx-control</code>).</span></div>}
    {error && <div className="error-banner"><AlertTriangle size={17} /><span>{error}</span><button aria-label="닫기" onClick={() => setError('')}>×</button></div>}
    {message && <p className="data-tag exp-message">{message}</p>}
    <nav className="exp-subtabs" aria-label="실험 관리 하위 탭">{SUB_TABS.map(t => <button key={t.id} type="button" className={tab === t.id ? 'selected' : ''} onClick={() => setTab(t.id)}><t.icon size={15} />{t.label}</button>)}</nav>
    {tab === 'theory' && <TheoryTab ctx={ctx} />}
    {tab === 'validate' && <ValidationTab ctx={ctx} />}
    {tab === 'line' && <LineTab ctx={ctx} />}
    {tab === 'tools' && <ToolsTab ctx={ctx} />}
    {tab === 'guide' && <GuideTab ctx={ctx} />}
    {tab === 'measure' && <>
    <HelpBox id="measure" title="측정·분석 탭 사용 순서">
      <ol>
        <li><b>차량·분석 설정</b>(오른쪽 카드): 축간거리 L, 바퀴 반지름 r, 방지턱 높이 h·폭 w를 자로 재서 넣고 '설정 저장'. 속도와 이론값 계산에 쓰입니다.</li>
        <li><b>실험 조건</b>(왼쪽 카드): 방지턱, 차체 질량(저울), 추가 질량, 출력(PWM 또는 직접 듀티)을 고릅니다. 측정 목적은 보통 '본 실험'.</li>
        <li><b>측정</b>: 차를 출발 위치에 놓고 '자동 측정 주행' 또는 '반복 측정 시작'을 누릅니다. 바닥에 출발선·정지선·유도선 테이프를 붙였다면 반복 측정의 '멈추는 곳'을 정지선으로, '복귀 방식'을 후진 또는 제자리 180° 회전으로 고르면 사람이 차를 다시 놓지 않아도 자동으로 왕복합니다. 측정 전에 센서 화면에서 MPU-6050 정지 보정을 한 번 하세요.</li>
        <li><b>확인</b>: '시행 결과'에서 그래프의 앞바퀴·뒷바퀴 오름·내림 표시가 맞는지 보고, 이상한 회차는 '집계에서 빼기'.</li>
        <li><b>진행표</b>: 계획(질량 × 출력 × 반복)의 빈칸을 채웁니다. 칸을 누르면 그 조건 그래프가 겹쳐 나옵니다.</li>
        <li><b>분석 및 관계식</b>(맨 아래): 조건별 평균, 관계식 후보, 허용 기준과 v_max. 더 깊은 해석과 예측·제어는 '이론·예측·제어' 탭에서.</li>
      </ol>
    </HelpBox>
    <section className="experiment-layout">
      <article className="card experiment-card"><div className="card-head"><div><h2>실험 조건</h2><p>조작 변인(방지턱·질량·출력)을 정하고 자동 측정 주행을 누르세요. 차는 정지 기록 → 직진 → 정지 순으로 움직입니다.</p></div></div>
        <div className="experiment-fields">
          <label>반복 회차<NumInput min={1} value={condition.trial} onChange={e => setCondition({ trial: Math.max(1, Math.round(Number(e.target.value) || 1)) })} /></label>
          <label>방지턱<select value={condition.bump_id} onChange={e => setCondition({ bump_id: e.target.value })}>{(settings?.bumps ?? []).map(b => <option key={b.id} value={b.id}>{b.name || b.id} · h {b.height_mm} / w {b.width_mm} mm · R {fmt(b.radius_mm, 1)} mm</option>)}</select></label>
          <label>차체 기본 질량 (g)<NumInput min={1} value={condition.base_mass_g || ''} placeholder="저울 측정값 (차체+센서)" onChange={e => setCondition({ base_mass_g: Math.max(0, Number(e.target.value) || 0) })} /></label>
          <label>추가 질량 (g)<div className="exp-inline"><NumInput min={0} value={condition.added_mass_g} onChange={e => setCondition({ added_mass_g: Math.max(0, Number(e.target.value) || 0) })} />{[0, 150, 300].map(v => <button key={v} type="button" className={condition.added_mass_g === v ? 'secondary selected' : 'secondary'} onClick={() => setCondition({ added_mass_g: v })}>+{v}</button>)}</div></label>
          <label>측정 방향<select value={condition.direction ?? 'forward'} onChange={e => { const d = e.target.value as 'forward' | 'backward'; setCondition({ direction: d }); void patchSettings({ analysis_direction: d }); }}><option value="forward">전진 (앞바퀴가 먼저 턱에)</option><option value="backward">후진 (구동 바퀴가 먼저 턱에)</option></select></label>
          <label>출력 방식<select value={condition.output_mode} onChange={e => setCondition({ output_mode: e.target.value as ExperimentCondition['output_mode'] })}><option value="pwm">라이브러리 PWM (실제 듀티 50~100%)</option><option value="duty">직접 듀티비 (저속까지 사용)</option></select></label>
          <label>출력값 ({condition.output_mode === 'duty' ? '듀티 %' : 'PWM'})<NumInput min={1} max={maxOutput} value={condition.output_value} onChange={e => setCondition({ output_value: Math.max(0, Math.min(maxOutput, Math.round(Number(e.target.value) || 0))) })} /></label>
          <label>영상 실측 속도 (m/s, 선택)<NumInput min={0} step={0.01} value={condition.video_speed_mps ?? ''} placeholder="슬로모션 교차 검증" onChange={e => setCondition({ video_speed_mps: e.target.value === '' ? null : Math.max(0, Number(e.target.value) || 0) })} /></label>
          <label>측정 목적<select value={condition.purpose ?? 'main'} onChange={e => setCondition({ purpose: e.target.value as Purpose })}><option value="main">본 실험 (관계식 도출에 사용)</option><option value="validation">식 검증 (관계식에서 제외, 예측과 비교)</option><option value="pilot">예비 실험 (집계 제외)</option></select></label>
          <label>실험 메모<input value={condition.note} onChange={e => setCondition({ note: e.target.value })} placeholder="예: 배터리 완충 직후" /></label>
        </div>
        <div className="experiment-mass"><span>총 질량</span><strong>{totalMass.toLocaleString()} g</strong><small>실제 모터 듀티 약 {effectiveDuty(condition.output_mode, condition.output_value)}% · 축간거리 {settings?.wheelbase_mm ? `${settings.wheelbase_mm} mm` : '미입력 (속도 계산 불가)'}</small></div>
        <div className="exp-repeat">
          <div className="exp-repeat-head"><Repeat size={14} /><b>반복 측정</b><small>측정 주행 → 출발 위치로 복귀 → 대기를 정해진 횟수만큼 반복합니다. 회차는 자동으로 1씩 늘어납니다.</small></div>
          <div className="experiment-fields">
            <label>반복 횟수<NumInput min={1} max={20} value={runOpts.repeats} onChange={e => setRunOpts({ repeats: Math.max(1, Math.min(20, Math.round(Number(e.target.value) || 1))) })} /></label>
            <label>측정 중 조향<select value={runOpts.steering_source ?? 'gyro'} onChange={e => setRunOpts({ steering_source: e.target.value as ExperimentSettings['steering_source'], heading_hold: e.target.value === 'gyro' })}><option value="gyro">자이로 직진 보정 (반듯하게)</option><option value="line">바닥 유도선 + 자이로 (조향 최소)</option><option value="none">조향 없음</option></select></label>
            <label>멈추는 곳<select value={endMode} disabled={retMode === 'uturn'} onChange={e => setRunOpts({ end_mode: e.target.value as 'time' | 'stop_line' })}><option value="time">주행 시간 / 방지턱 통과 후 자동 정지</option><option value="stop_line" disabled={!lineReady}>정지선(가로 테이프)에서 서서히 정지{lineReady ? '' : ' – 라인 보정 먼저'}</option></select></label>
            <label>출발<div className="exp-inline"><div className="segmented"><button type="button" className={(runOpts.accel_mode ?? 'instant') === 'instant' ? 'selected' : ''} onClick={() => setRunOpts({ accel_mode: 'instant' })}>바로</button><button type="button" className={runOpts.accel_mode === 'ramp' ? 'selected' : ''} onClick={() => setRunOpts({ accel_mode: 'ramp' })}>서서히</button></div>
              {runOpts.accel_mode === 'ramp' && <><NumInput min={0.05} max={3} step={0.1} value={runOpts.accel_s ?? 0.6} onChange={e => setRunOpts({ accel_s: Math.max(0.05, Math.min(3, Number(e.target.value) || 0.6)) })} /><small>초</small></>}</div></label>
            {endMode === 'time' ? <>
              <label>주행 시간 (s, 최대)<NumInput min={0.3} max={6} step={0.1} value={runOpts.run_seconds} onChange={e => setRunOpts({ run_seconds: Math.max(0.3, Math.min(6, Number(e.target.value) || 2.5)) })} /></label>
              <label className="exp-check"><input type="checkbox" checked={settings?.stop_after_bump ?? true} onChange={e => void patchSettings({ stop_after_bump: e.target.checked })} /> 방지턱을 다 넘으면 자동 정지 (주행 시간은 최대 시간이 됨)</label>
            </> : <>
              <label>정지선을 본 뒤 멈추는 시간 (s)<NumInput min={0.05} max={3} step={0.05} value={runOpts.decel_s ?? 0.3} onChange={e => setRunOpts({ decel_s: Math.max(0.05, Math.min(3, Number(e.target.value) || 0.3)) })} /></label>
            </>}
            <label>복귀 방식<select value={retMode} onChange={e => setRunOpts({ return_mode: e.target.value as 'none' | 'reverse' | 'uturn', return_enabled: e.target.value !== 'none' })}>
              <option value="reverse">후진으로 출발선까지</option>
              <option value="uturn" disabled={!lineReady}>제자리 180° 회전 (왕복 모두 측정){lineReady ? '' : ' – 라인 보정 먼저'}</option>
              <option value="none">복귀 안 함</option></select></label>
            {retMode === 'reverse' && <label>후진 출력 (빈칸 = {endMode === 'stop_line' ? `측정 × 1.3 = ${Math.min(100, Math.round(condition.output_value * 1.3))}` : '측정과 같음'})<NumInput min={1} max={maxOutput} value={runOpts.return_output ?? ''} placeholder={String(endMode === 'stop_line' ? Math.min(100, Math.round(condition.output_value * 1.3)) : condition.output_value)} onChange={e => setRunOpts({ return_output: e.target.value === '' ? null : Math.max(1, Math.min(maxOutput, Number(e.target.value))) })} /></label>}
            {retMode === 'reverse' && endMode === 'time' && <><label>복귀 시간 배율{runOpts.return_stop_on_tape ? ' (테이프 못 찾을 때 최대 ×1.6)' : ''}<NumInput min={0.3} max={3} step={0.05} value={runOpts.return_scale} onChange={e => setRunOpts({ return_scale: Math.max(0.3, Math.min(3, Number(e.target.value) || 1)) })} /></label>
              <label className="exp-check"><input type="checkbox" checked={!!runOpts.return_stop_on_tape} disabled={!lineReady} onChange={e => setRunOpts({ return_stop_on_tape: e.target.checked })} /> 출발선 테이프에서 멈추기{lineReady ? '' : ' (라인 추종 탭에서 바닥·테이프 보정 먼저)'}</label></>}
            {retMode === 'uturn' && <><label>회전 출력 (앞바퀴가 끌려 돌아 높게)<NumInput min={30} max={100} value={runOpts.spin_output ?? 100} onChange={e => setRunOpts({ spin_output: Math.max(30, Math.min(100, Number(e.target.value) || 100)) })} /></label>
              <label>회전 방향<select value={runOpts.spin_direction ?? 'left'} onChange={e => setRunOpts({ spin_direction: e.target.value as 'left' | 'right' })}><option value="left">왼쪽 (반시계)</option><option value="right">오른쪽 (시계)</option></select></label></>}
            <label>회차 사이 대기 (s)<NumInput min={0.3} max={10} step={0.5} value={runOpts.rest_s} onChange={e => setRunOpts({ rest_s: Math.max(0.3, Number(e.target.value) || 1.5) })} /></label>
            <label>출발 밀어주기 (ms, 0 = 끔)<NumInput min={0} max={400} step={20} value={runOpts.kick_ms ?? 0} onChange={e => setRunOpts({ kick_ms: Math.max(0, Math.min(400, Math.round(Number(e.target.value) || 0))) })} /></label>
            <label className="exp-check"><input type="checkbox" checked={!!settings?.ultrasonic_log} onChange={e => void patchSettings({ ultrasonic_log: e.target.checked })} /> 초음파 거리 기록 (벽까지 거리 → 속력, 이론·예측·제어 탭에서 분석){settings?.wall_stop_cm ? ` · 벽 ${settings.wall_stop_cm} cm 앞 자동 정지` : ''}</label>
          </div>
          {endMode === 'stop_line' && <details className="exp-tips"><summary>정지선 코스 세부 설정 · 바닥 센서 확인</summary>
            <div className="experiment-fields">
              <label>위치 맞춤 듀티 (%, 차가 겨우 움직일 정도)<NumInput min={15} max={100} value={runOpts.creep_duty ?? 40} onChange={e => setRunOpts({ creep_duty: Math.max(15, Math.min(100, Number(e.target.value) || 40)) })} /></label>
              <label>정지선을 못 볼 때 최대 주행 시간 (s)<NumInput min={1} max={30} value={runOpts.leg_timeout_s ?? 8} onChange={e => setRunOpts({ leg_timeout_s: Math.max(1, Math.min(30, Number(e.target.value) || 8)) })} /></label>
            </div>
            <div className="button-row"><button type="button" className="secondary" disabled={!serverAvailable} onClick={() => void checkFloor()}><ScanLine size={14} /> 바닥 센서 확인</button>
              {floorCheck && <span className="exp-hint">{!floorCheck.calibrated ? '보정 필요' : floorCheck.cross ? '가로 테이프 위 ✔ (출발 가능)' : floorCheck.guide ? '유도선 위 (가로 테이프 아님)' : '바닥 위'} · {floorCheck.weights?.map(w => Math.round(w * 100) + '%').join(' / ')}</span>}</div>
          </details>}
          <small className="exp-hint">{endMode === 'stop_line'
            ? <>정지선 코스: 바닥 센서를 출발선(가로 테이프) 위에 두고 시작합니다. 가로선은 20 cm 이상 길게, 방지턱과 가로선 사이는 30 cm 이상 두세요. 유도선을 따라 방지턱을 넘고, 정지선을 보면 {(runOpts.decel_s ?? 0.3).toFixed(2)}초에 걸쳐 멈춘 뒤 {retMode === 'uturn' ? '제자리에서 180° 돌아 정지선 위치를 맞추고 반대 방향(B→A)으로 다음 회차를 측정합니다. 출발선에 오면 다시 180° 돌아 원래 방향으로 맞춥니다.' : retMode === 'reverse' ? '더 센 출력으로 방지턱을 후진으로 넘어 출발선을 볼 때까지 돌아와 위치를 맞춥니다.' : '멈춥니다.'} 이 값들은 '설정 저장'을 누르면 기본값으로 남습니다.</>
            : <>한 회 약 {(runOpts.pre_roll_s + runOpts.run_seconds + runOpts.tail_s + (retMode !== 'none' ? runOpts.run_seconds * runOpts.return_scale + 0.3 : 0) + runOpts.rest_s).toFixed(1)}초 · 전체 약 {((runOpts.pre_roll_s + runOpts.run_seconds + runOpts.tail_s + (retMode !== 'none' ? runOpts.run_seconds * runOpts.return_scale + 0.3 : 0) + runOpts.rest_s) * runOpts.repeats).toFixed(0)}초. 후진은 실제로 앞으로 달린 시간만큼 하므로(자동 정지했으면 그 시간), 출발선보다 덜 오면 배율을 늘리고 더 가면 줄이세요. 바닥에 출발선·정지선 테이프를 붙였다면 '멈추는 곳'을 정지선으로 바꾸면 매번 같은 자리에서 서고 출발합니다. 이 값들은 '설정 저장'을 누르면 기본값으로 남습니다.</>}</small>
        </div>
        {!simulated && !sensorReady && <div className="warning">센서 화면에서 MPU-6050 정지 상태 보정을 먼저 하세요. {impact?.recording ? '수동 측정이 진행 중입니다.' : ''} <button className="text-button" onClick={onOpenSensors}>센서 화면으로</button></div>}
        {conditionError && <p className="diagnostic-error">{conditionError}</p>}
        <div className="button-row"><button className="primary" disabled={!canAutoRun} onClick={() => void startAutoRun()}><Play size={15} /> {runOpts.repeats > 1 ? `반복 측정 시작 (${runOpts.repeats}회)` : '자동 측정 주행'}</button>
          <button className="emergency exp-stop" disabled={!serverAvailable} onClick={() => void stopNow()}><CircleStop size={15} /> 정지</button>
          {!simulated && <button className="secondary" disabled={!serverAvailable || busy || !!conditionError || !impact?.recorded_sample_count} onClick={() => void saveManualRecording()}><Save size={15} /> 수동 측정 기록 저장</button>}</div>
        {auto && auto.state !== 'idle' && <div className={'exp-auto-state ' + auto.state}><span className="exp-phase">{PHASE_LABEL[auto.state] ?? auto.state}</span>{auto.cycles && auto.cycles > 1 ? <span className="exp-cycle">{Math.min(auto.cycle ?? 0, auto.cycles)}/{auto.cycles}</span> : null}<span>{auto.message}</span>{auto.heading_deg != null && busy && <span className="exp-heading">방향 {auto.heading_deg > 0 ? '+' : ''}{auto.heading_deg.toFixed(1)}°</span>}{auto.phase_remaining_s != null && busy && <b>{auto.phase_remaining_s.toFixed(1)} s</b>}</div>}
        <p className="data-tag">정지를 누르면 즉시 멈추고 남은 반복을 취소합니다. 측정 중이던 시행은 '제외' 상태로 저장됩니다.</p>
      </article>

      <article className="card experiment-card"><div className="card-head"><div><h2>차량·분석 설정</h2><p>축간거리와 바퀴 반지름은 자로 실측해 입력하세요. 속도와 Π 계산에 쓰입니다.</p></div></div>
        {draft && <>
          <div className="experiment-fields">
            <label>축간거리 L (mm)<NumInput min={10} value={draft.wheelbase_mm ?? ''} placeholder="앞·뒤 바퀴 중심 거리" onChange={e => setDraft({ ...draft, wheelbase_mm: e.target.value === '' ? null : Number(e.target.value) })} /></label>
            <label>바퀴 반지름 r (mm)<NumInput min={5} value={draft.wheel_radius_mm ?? ''} onChange={e => setDraft({ ...draft, wheel_radius_mm: e.target.value === '' ? null : Number(e.target.value) })} /></label>
            <label>기본 질량 기본값 (g)<NumInput min={1} value={draft.base_mass_g ?? ''} onChange={e => setDraft({ ...draft, base_mass_g: e.target.value === '' ? null : Number(e.target.value) })} /></label>
            <label>직진 보정 세기 (기울어진 만큼 핸들을 얼마나 꺾나)<NumInput min={0} max={5} step={0.1} value={draft.heading_gain} onChange={e => setDraft({ ...draft, heading_gain: Math.max(0, Number(e.target.value) || 0) })} /></label>
            <label>쌓인 오차 보정 (계속 한쪽으로 휘면 올리기)<NumInput min={0} max={5} step={0.1} value={draft.heading_ki ?? 0.8} onChange={e => setDraft({ ...draft, heading_ki: Math.max(0, Number(e.target.value) || 0) })} /></label>
            <label>방지턱 후보로 볼 최소 충격 (g, 평소보다 이만큼 더)<NumInput min={0.02} step={0.05} value={draft.threshold_delta_g} onChange={e => setDraft({ ...draft, threshold_delta_g: Number(e.target.value) || 0.3 })} /></label>
            <label>앞·뒤 바퀴 충격 사이 최소 시간 (s)<NumInput min={0.005} step={0.005} value={draft.min_gap_s} onChange={e => setDraft({ ...draft, min_gap_s: Number(e.target.value) || 0.03 })} /></label>
            <label>바퀴가 떴다고 볼 기준 (g, 이보다 작으면)<NumInput min={0} max={0.9} step={0.05} value={draft.jump_g} onChange={e => setDraft({ ...draft, jump_g: Number(e.target.value) })} /></label>
            <label>출발 전 기록 / 주행 / 멈춘 뒤 기록 (초)<div className="exp-inline"><NumInput min={0.3} step={0.1} value={draft.pre_roll_s} onChange={e => setDraft({ ...draft, pre_roll_s: Number(e.target.value) || 1 })} /><NumInput min={0.3} max={6} step={0.1} value={draft.run_seconds} onChange={e => setDraft({ ...draft, run_seconds: Number(e.target.value) || 2.5 })} /><NumInput min={0.1} step={0.1} value={draft.tail_s} onChange={e => setDraft({ ...draft, tail_s: Number(e.target.value) || 0.6 })} /></div></label>
          </div>
          <div className="exp-bumps"><div className="exp-bumps-head"><b>방지턱 형상</b><button type="button" className="text-button" disabled={draft.bumps.length >= 4} onClick={() => setDraft({ ...draft, bumps: [...draft.bumps, { id: String.fromCharCode(65 + draft.bumps.length), name: '', height_mm: 10, width_mm: 40 }] })}>+ 추가</button></div>
            {draft.bumps.map((b, i) => <div className="exp-bump-row" key={i}><input aria-label="ID" value={b.id} maxLength={12} onChange={e => updateBump(i, { id: e.target.value.replace(/[^A-Za-z0-9_-]/g, '') })} /><input aria-label="이름" value={b.name} placeholder="이름" onChange={e => updateBump(i, { name: e.target.value })} /><label>h<NumInput min={1} value={b.height_mm ?? ''} onChange={e => updateBump(i, { height_mm: Number(e.target.value) || null })} /></label><label>w<NumInput min={1} value={b.width_mm ?? ''} onChange={e => updateBump(i, { width_mm: Number(e.target.value) || null })} /></label><span>R {fmt(bumpRadiusMm(b.height_mm, b.width_mm), 1)} mm</span><button type="button" className="text-button" disabled={draft.bumps.length <= 1} onClick={() => setDraft({ ...draft, bumps: draft.bumps.filter((_, n) => n !== i) })}>삭제</button></div>)}
            <small>곡률 반경 R = (w²/4 + h²) / (2h)</small></div>
          <div className="exp-learn"><label><input type="checkbox" checked={draft.heading_learn ?? true} onChange={e => setDraft({ ...draft, heading_learn: e.target.checked })} /> 직진 습관 자동 학습 (매 회 끝에 '평소 한쪽으로 쏠리는 정도'를 기억해 다음 회 처음부터 미리 보정)</label>
            <span>지금 기억한 값: 앞으로 {(settings?.heading_ff_forward ?? 0).toFixed(1)}° · 뒤로 {(settings?.heading_ff_backward ?? 0).toFixed(1)}°</span>
            <button type="button" className="text-button" onClick={() => void patchSettings({ heading_ff_forward: 0, heading_ff_backward: 0 })}>잊기(0으로)</button></div>
          <div className="button-row"><button className="secondary" disabled={savingSettings} onClick={() => void saveSettings(false)}>설정 저장</button><button className="primary" disabled={savingSettings || busy} onClick={() => void saveSettings(true)}>저장 후 전체 재분석</button></div>
        </>}
      </article>
    </section>

    {progress && progress.cells.length > 0 && <section className="card experiment-card exp-progress">
      <div className="card-head"><div><h2>실험 진행표 · {progress.done}/{progress.need}회</h2><p>계획(방지턱 × 추가 질량 × 출력 × 반복)에 대비해 본 실험이 몇 회 끝났는지 보여 줍니다. 칸을 누르면 그 조건이 입력되고 아래에 그래프가 나옵니다(여러 칸을 누르면 겹쳐 비교, 다시 누르면 빠짐).{progress.next ? ` 다음 추천: 방지턱 ${progress.next.bump_id}, +${progress.next.added_mass_g} g, 출력 ${progress.next.output_value}` : ' 계획을 모두 마쳤습니다.'}</p></div>
        {progress.next && <button className="secondary" onClick={() => progress.next && setCondition({ bump_id: progress.next.bump_id, added_mass_g: progress.next.added_mass_g, output_value: progress.next.output_value, purpose: 'main' })}>다음 조건 입력</button>}</div>
      {Array.from(new Set(progress.cells.map(c => c.bump_id))).map(b => { const cells = progress.cells.filter(c => c.bump_id === b); const outs = Array.from(new Set(cells.map(c => c.output_value))); const ms = Array.from(new Set(cells.map(c => c.added_mass_g)));
        return <div className="experiment-table-wrap" key={b}><table className="experiment-table exp-progress-table"><thead><tr><th>방지턱 {b}</th>{outs.map(o => <th key={o}><button type="button" className="exp-headbtn" title="이 출력의 모든 질량 고르기" onClick={() => toggleCells(ms.map(m => ({ bump_id: b, added_mass_g: m, output_value: o })))}>출력 {o}</button></th>)}</tr></thead><tbody>{ms.map(m => <tr key={m}><td><button type="button" className="exp-headbtn" title="이 질량의 모든 출력 고르기" onClick={() => toggleCells(outs.map(o => ({ bump_id: b, added_mass_g: m, output_value: o })))}>+{m} g</button></td>{outs.map(o => { const c = cells.find(x => x.added_mass_g === m && x.output_value === o)!; const cls = c.done >= c.need ? 'full' : c.done > 0 ? 'part' : 'none';
          return <td key={o}><button type="button" className={'exp-cell ' + cls + (condition.bump_id === b && condition.added_mass_g === m && condition.output_value === o ? ' current' : '') + (compareCells.some(x => cellId(x) === cellId({ bump_id: b, added_mass_g: m, output_value: o })) ? ' picked' : '')} onClick={() => { setCondition({ bump_id: b, added_mass_g: m, output_value: o, purpose: 'main' }); toggleCells([{ bump_id: b, added_mass_g: m, output_value: o }]); }}>{c.done}/{c.need}</button></td>; })}</tr>)}</tbody></table></div>; })}
      <PlanCompare cells={compareCells} runs={runs} settings={settings ?? DEFAULT_SETTINGS} aLimit={aLimit} request={request} onClear={() => setCompareCells([])} onRemove={c => setCompareCells(cur => cur.filter(x => cellId(x) !== cellId(c)))} />
      <details className="exp-plan-edit"><summary>계획 바꾸기</summary>
        <div className="exp-learn"><span>실험할 방지턱:</span>{(settings?.bumps ?? []).map(b => { const sel = settings?.plan_bumps?.length ? settings.plan_bumps.includes(b.id) : true;
          return <label key={b.id}><input type="checkbox" checked={sel} onChange={e => { const cur = settings?.plan_bumps?.length ? settings.plan_bumps : (settings?.bumps ?? []).map(x => x.id); const next = e.target.checked ? Array.from(new Set([...cur, b.id])) : cur.filter(x => x !== b.id); void patchSettings({ plan_bumps: next.length ? next : null }); }} /> {b.id}{b.name ? ` (${b.name})` : ''}</label>; })}</div>
        <div className="experiment-fields">
        <label>추가 질량 목록 (g, 쉼표)<input defaultValue={(settings?.plan_added_masses ?? []).join(', ')} onBlur={e => void patchSettings({ plan_added_masses: e.target.value.split(',').map(Number).filter(v => Number.isFinite(v) && v >= 0) })} /></label>
        <label>출력값 목록 (쉼표)<input defaultValue={(settings?.plan_outputs ?? []).join(', ')} onBlur={e => void patchSettings({ plan_outputs: e.target.value.split(',').map(Number).filter(v => Number.isFinite(v) && v > 0 && v <= 100) })} /></label>
        <label>조건당 반복 횟수<NumInput min={1} max={20} defaultValue={settings?.plan_repeats ?? 5} onBlur={e => void patchSettings({ plan_repeats: Math.max(1, Math.min(20, Math.round(Number(e.target.value) || 5))) })} /></label>
      </div></details>
    </section>}

    <section className="experiment-runs"><div className="section-subhead"><h2>시행 결과</h2><p>한 번 넘을 때마다 1회차가 기록됩니다. ◀ ▶(키보드 ← →)로 한 회씩 넘겨 보거나 '모두 겹쳐 보기'로 여러 회차를 한 그래프에서 비교하세요. 빨간색은 허용 기준을 넘은 충격입니다.</p></div>
      <div className="card exp-options">
        <label>진동 거르기<select value={settings?.filter_hz ?? 0} disabled={reanalyzing || busy} onChange={e => void applyAnalysisOption({ filter_hz: Number(e.target.value) })}>
          <option value={0}>끔 (원래 기록 그대로)</option><option value={30}>약하게 (30 Hz)</option><option value={20}>보통 (20 Hz, 추천)</option><option value={15}>강하게 (15 Hz)</option><option value={10}>아주 강하게 (10 Hz)</option></select></label>
        <label>방지턱 감지 민감도<select value={settings?.detect_level ?? 'normal'} disabled={reanalyzing || busy} onChange={e => void applyAnalysisOption({ detect_level: e.target.value as ExperimentSettings['detect_level'] })}>
          <option value="strict">엄격 – 확실한 충격만 (평지 오검출 거의 없음)</option><option value="normal">보통 (기본)</option><option value="sensitive">민감 – 작은 방지턱·느린 속도도 잡기</option></select></label>
        <p className="exp-hint">{reanalyzing ? '모든 시행을 다시 분석하는 중…' : <>바꾸면 저장된 기록 전체를 바로 다시 분석합니다(측정을 다시 할 필요 없음). <b>진동 거르기</b>는 차체가 빠르게 출렁이는 떨림을 지워 방지턱 충격을 잘 보이게 하지만 최대값도 조금 작아지므로, 보고서에는 같은 설정으로 분석한 값끼리 비교하세요. <b>민감</b>으로 하면 작은 충격도 잡지만, 평지에서 진동을 방지턱으로 착각할 수 있으니 겹쳐 보기로 확인하세요.</>}</p>
      </div>
      <RunViewer runs={shown} selectedId={selectedId} setSelectedId={setSelectedId} detail={detail} settings={settings ?? DEFAULT_SETTINGS} data={data} request={request} download={download} patchRun={patchRun} toggleExcluded={toggleExcluded} deleteRun={deleteRun} />

      <article className="card experiment-card"><div className="card-head"><div><h2>시행 목록 ({shown.length}{purposeFilter !== 'all' ? ` / 전체 ${runs.length}` : ''})</h2><p>최신 순. 회색 줄은 계산에서 뺀 회차, 빨간 줄은 허용 기준을 넘은 회차입니다. '방지턱 못 찾음'은 평지처럼 뚜렷한 충격이 없어 자동으로 계산에서 빠진 회차입니다.</p></div><div className="button-row"><label className="exp-inline">보기<select value={purposeFilter} onChange={e => { setPurposeFilter(e.target.value as 'all' | Purpose); setChecked(new Set()); }}><option value="all">전체</option><option value="main">본 실험만</option><option value="validation">식 검증만</option><option value="pilot">예비만</option></select></label>
          {purposeFilter !== 'all' && <button className="secondary" disabled={!shown.length || busy} onClick={() => void clearPurpose(purposeFilter)}><Trash2 size={14} /> {PURPOSE_LABEL[purposeFilter]} 전부 휴지통으로</button>}
          <button className="secondary" disabled={!checked.size || busy} onClick={() => void deleteChecked()}><Trash2 size={14} /> 선택 삭제 ({checked.size})</button><button className="primary" disabled={!shown.length} onClick={() => download('/api/experiment/export/summary.csv', 'picarx_summary.csv')} title="방지턱·질량·출력(PWM) 순으로 정리한 회차 표와 조건별 평균, 자동 해석"><Download size={14} /> 핵심 요약 CSV</button><button className="secondary" disabled={!shown.length} onClick={() => download(`/api/experiment/export/raw.zip${checked.size ? `?ids=${Array.from(checked).join(',')}` : ''}`, 'picarx_raw_runs.zip')}><Download size={14} /> 원자료 ZIP ({checked.size ? `선택 ${checked.size}개` : '전체'})</button><button className="secondary" disabled={!shown.length} title="앞바퀴 −0.15 ~ +0.45초(방지턱 부분)의 가속도만 잘라 회차별로 모음: 긴 표(long), 1 ms 간격 넓은 표(wide, 엑셀 겹친 그래프용), 회차별 핵심값" onClick={() => download(`/api/experiment/export/bump_windows.zip${checked.size ? `?ids=${Array.from(checked).join(',')}` : ''}`, 'picarx_bump_windows.zip')}><Download size={14} /> 방지턱 구간만 ZIP ({checked.size ? `선택 ${checked.size}개` : '전체'})</button><button className="secondary" disabled={!shown.length} onClick={() => download('/api/experiment/export/shown.csv', 'picarx_runs.csv')}><Download size={14} /> 시행 CSV(전체 항목)</button></div></div>
        {shown.length === 0 ? <div className="empty-chart">기록 없음</div> : <div className="experiment-table-wrap"><table className="experiment-table exp-clickable"><thead><tr><th><input type="checkbox" aria-label="모두 선택" checked={checked.size > 0 && checked.size === shown.length} onChange={e => setChecked(e.target.checked ? new Set(shown.map(r => r.id)) : new Set())} /></th><th>회차</th><th>목적</th><th>방지턱</th><th>총질량</th><th>출력</th><th>통과 속도 (m/s)</th><th>가장 큰 충격 (g)</th><th>평소보다 더 (g)</th><th>Π</th><th>바퀴 뜸</th><th>상태</th></tr></thead>
          <tbody>{shown.map(r => { const lim = limitFor(r, data, settings ?? DEFAULT_SETTINGS); const over = lim != null && r.analysis.peak_g != null && r.analysis.peak_g > lim; return <tr key={r.id} className={(r.id === selectedId ? 'selected ' : '') + (r.excluded ? 'excluded ' : '') + (over ? 'over' : '')} onClick={() => setSelectedId(r.id)}><td onClick={e => e.stopPropagation()}><input type="checkbox" aria-label="선택" checked={checked.has(r.id)} onChange={e => setChecked(prev => { const next = new Set(prev); if (e.target.checked) next.add(r.id); else next.delete(r.id); return next; })} /></td><td>{r.condition.trial}</td><td>{PURPOSE_LABEL[(r.condition.purpose ?? 'main') as Purpose]}</td><td>{r.condition.bump_id}</td><td>{r.condition.total_mass_g} g</td><td>{outputLabel(r.condition.output_mode, r.condition.output_value)}{r.condition.direction === 'backward' ? ' · 후진' : ''}</td><td>{fmt(r.analysis.speed_mps, 3)}</td><td>{fmt(r.analysis.peak_g, 3)}{over && <span className="exp-over">기준 초과</span>}</td><td>{fmt(r.analysis.peak_delta_g, 3)}</td><td>{fmt(r.analysis.pi, 2)}</td><td>{r.analysis.jump == null ? '—' : r.analysis.jump ? '예' : '아니오'}</td><td>{r.aborted ? '중단' : r.excluded ? '제외' : r.analysis.pair_source === 'manual' ? '수동 지정' : r.analysis.bump_detected === false ? '방지턱 못 찾음' : r.analysis.flags.length ? '확인 필요' : '정상'}</td></tr>; })}</tbody></table></div>}
      </article>
    </section>

    <section className="experiment-runs"><div className="section-subhead"><h2>분석 및 관계식 <span className="exp-badge">{settings?.analysis_direction === 'backward' ? '후진 측정만' : '전진 측정만'}</span></h2><p>조건별 평균은 아래에서 고른 이상치 규칙으로 구하고, 여러 후보식을 맞춰 '새 데이터를 얼마나 잘 맞히는가(교차검증 오차)'로 비교합니다. 식 검증용·예비 시행은 여기서 빠집니다.</p></div>
      <div className="card exp-options exp-outlier">
        <label>조건별 평균의 이상치 처리<select value={settings?.outlier_method ?? 'trim'} disabled={busy} onChange={e => void patchSettings({ outlier_method: e.target.value as OutlierMethod })}>
          {(Object.keys(OUTLIER_LABEL) as OutlierMethod[]).map(k => <option key={k} value={k}>{OUTLIER_LABEL[k]}</option>)}</select></label>
        <div className="exp-hint">{{
          trim: <>계획서의 원래 규칙입니다. 3회 이상이면 가장 큰 값과 가장 작은 값을 하나씩 무조건 뺍니다. 간단하지만 이상치가 2개 이상이면 다 못 빼고, 이상치가 없어도 정상값 2개를 버립니다.</>,
          sigma2: <>엑셀의 <code>AVERAGE</code>와 <code>STDEV.S</code>로 평균(x̄)과 표준편차(s)를 구해 <b>x̄ − 2s ~ x̄ + 2s 밖</b>의 값을 뺍니다. 주의: 이상치도 s를 키우므로 <b>반복이 5회 이하면 2s 밖으로 나가는 값이 수학적으로 거의 없습니다</b>(5개일 때 최대 1.79s). 반복을 8회 이상 했을 때 쓰세요.</>,
          iqr: <>값을 작은 것부터 늘어놓아 25% 위치 값 Q1(엑셀 <code>QUARTILE.INC(범위,1)</code>), 75% 위치 값 Q3(<code>QUARTILE.INC(범위,3)</code>)을 구하고 D = Q3 − Q1이라 할 때 <b>Q1 − 1.5D보다 작거나 Q3 + 1.5D보다 큰 값</b>을 뺍니다(투키 울타리). 이상치 자체에 흔들리지 않아 5회 반복에도 잘 동작합니다. (큰 값부터 늘어놓으면 '상위 25%' 값이 Q3, '상위 75%' 값이 Q1이 되므로, 아래쪽 울타리는 Q1 쪽에서, 위쪽 울타리는 Q3 쪽에서 잽니다.)</>,
          none: <>모든 값을 평균합니다. 이상치 처리 전후를 비교해 보고서에 적을 때 쓰세요.</>,
        }[settings?.outlier_method ?? 'trim']} 바꾸면 표·그래프·관계식이 바로 다시 계산됩니다(측정을 다시 할 필요 없음).</div>
      </div>
      {bumpIds.length === 0 ? <div className="card empty-chart">집계할 시행이 없습니다.</div> : <>
        <div className="card experiment-card"><div className="card-head"><div><h2>방지턱 {bump} 결과</h2><p>{bumpInfo ? `h ${bumpInfo.height_mm} mm · w ${bumpInfo.width_mm} mm · R ${fmt(bumpInfo.radius_mm, 1)} mm` : ''}{rEff ? ` · R_eff ${(rEff * 1000).toFixed(1)} mm · 이론 점프 속도 v_c = ${Math.sqrt(G * rEff).toFixed(3)} m/s` : ''}</p></div>
          <div className="button-row">{bumpIds.length > 1 && <div className="segmented">{bumpIds.map(id => <button key={id} type="button" className={id === bump ? 'selected' : ''} onClick={() => setAnalysisBump(id)}>방지턱 {id}</button>)}</div>}<button className="secondary" onClick={() => download('/api/experiment/export/conditions.csv', 'picarx_conditions.csv')}><Download size={14} /> 조건별 CSV</button></div></div>
          {!chosenModel && (() => {
            const main = bumpRuns.filter(r => (r.condition.purpose ?? 'main') === 'main');
            const crossed = main.filter(r => r.analysis.speed_mps != null);
            const speeds = new Set(bumpGroups.filter(g => g.mean_speed_mps != null).map(g => `${g.output_mode}${g.output_value}·${g.total_mass_g}`));
            const items: [boolean, string, string][] = [
              [!!settings?.wheelbase_mm, '축간거리 입력', settings?.wheelbase_mm ? `${settings.wheelbase_mm} mm` : '차량·분석 설정에 앞·뒤 바퀴 중심 사이 거리를 자로 재서 넣으세요. 없으면 속도를 못 구합니다.'],
              [crossed.length > 0, '방지턱을 넘은 회차', crossed.length ? `${crossed.length}회 (전체 ${main.length}회 중)` : `0회 (전체 ${main.length}회 중) – 방지턱이 있는 주행로에서 측정해야 합니다. 평지 주행은 '방지턱 못 찾음'으로 빠집니다.`],
              [speeds.size >= 2, '서로 다른 속도 조건 2개 이상', `${speeds.size}개 – PWM을 2단계 이상(좋기로는 4단계: 예 40·55·70·85) 바꿔 측정하세요.`],
            ];
            return <div className="exp-why"><b>그래프와 관계식(n)이 아직 비어 있는 이유</b>
              <ul>{items.map(([ok, name, text]) => <li key={name} className={ok ? 'ok' : 'no'}><span>{ok ? '✔' : '✘'}</span><b>{name}</b> {text}</li>)}</ul>
              <small>속도(m/s)는 "1초에 몇 m 가는 빠르기"입니다. 앞바퀴와 뒷바퀴가 방지턱에 차례로 부딪힌 시간 차이로 계산하므로, 방지턱을 넘어야만 생깁니다.</small></div>;
          })()}
          <SpeedImpactChart runs={bumpRuns.filter(r => (r.condition.purpose ?? 'main') === 'main')} groups={bumpGroups} fit={fit} model={chosenModel} aLimit={aLimit} title={`그래프 1. 통과 속도와 최대 수직 가속도 (방지턱 ${bump})`} name={`graph1_speed_impact_${bump}`} />
          <div className="exp-fit">
            <div><small>선택한 관계식{settings?.model_id && settings.model_id !== 'auto' ? ' (직접 선택)' : ' (자동: 교차검증 오차 최소)'}</small><b>{chosenModel ? `${chosenModel.name}: ${chosenModel.formula}` : '—'}</b>
              {chosenModel && <span>{chosenModel.params.map(p => paramText(p)).join(' · ')}</span>}
              {chosenModel && <span>R² = {chosenModel.r2.toFixed(3)} · RMSE {chosenModel.rmse.toFixed(3)} g · 교차검증 RMSE {fmt(chosenModel.loocv_rmse, 3, 'g')}{chosenModel.exact_fit ? ' · 점 수 = 매개변수 수(신뢰도 낮음)' : ''}</span>}
              <span>{comparison?.note ?? ''}</span></div>
            {fit && fit.per_mass.length > 0 && <div><small>질량별 거듭제곱 맞춤 (참고)</small>{fit.per_mass.map(p => <span key={p.mass_g}>{p.mass_g.toLocaleString()} g: Δa = {p.k.toPrecision(3)}·v^{p.n.toFixed(3)} (R² {p.r2.toFixed(3)}, {p.points}점)</span>)}</div>}
            <div className="exp-vmax"><small>사람이 견딜 수 있는 충격 기준 (허용 최대 수직 가속도)</small>
              <label className="exp-preset">기준 고르기<select aria-label="허용 기준" value={settings?.a_limit_mode === 'first_jump' ? 'first_jump' : settings?.a_limit_preset ?? 'trl614'} onChange={e => { const v = e.target.value; void applyCriterion(v === 'first_jump' ? { a_limit_mode: 'first_jump' } : { a_limit_mode: 'manual', a_limit_preset: v }); }}>
                {Object.entries(settings?.a_limit_presets ?? { trl614: { label: '0.7 g · TRL 614 (2004)', delta_g: 0.7, source: '' } }).map(([k, v]) => <option key={k} value={k}>{v.label}{k === 'trl614' ? ' (기본·추천)' : ''}</option>)}
                <option value="custom">직접 입력</option><option value="first_jump">이 실험에서 바퀴가 처음 뜬 조건</option></select></label>
              {settings?.a_limit_mode !== 'first_jump' && settings?.a_limit_preset === 'custom' && <div className="exp-inline"><label>기준 (g, 가만히 있을 때 1 g 포함)<NumInput min={1.05} max={8} step={0.1} value={draft?.a_limit_g ?? ''} placeholder="1.7" onChange={e => setDraft(d => ({ ...(d ?? DEFAULT_SETTINGS), a_limit_g: e.target.value === '' ? null : Number(e.target.value) }))} /></label><button type="button" className="secondary" onClick={() => void applyCriterion({ a_limit_mode: 'manual', a_limit_preset: 'custom', a_limit_g: draft?.a_limit_g ?? 1.7 })}>적용</button></div>}
              <b>{aLimit ? `${aLimit.toFixed(2)} g (평소보다 +${(aLimit - 1).toFixed(2)} g 더)` : '기준 없음'}</b>
              <span>{criterion?.source ?? '측정 후 계산됩니다.'}</span>
              {criterion && criterion.vmax_by_mass.length > 0 && <table className="exp-mini"><thead><tr><th>질량</th><th>v_max</th></tr></thead><tbody>{criterion.vmax_by_mass.map(v => <tr key={v.mass_g}><td>{v.mass_g.toLocaleString()} g</td><td>{(v as { note?: string | null }).note ?? fmt(v.vmax_mps, 3, 'm/s')}</td></tr>)}</tbody></table>}
              <div className="exp-inline"><label>다른 질량 (g)<NumInput min={1} value={calcMass === '' ? vmaxMass : calcMass} onChange={e => setCalcMass(e.target.value === '' ? '' : Number(e.target.value))} /></label><b>{vmaxBelow ? '가장 느린 측정 속도에서도 이미 기준 초과' : vmaxChosen == null ? '—' : `${vmaxChosen.toFixed(3)} m/s`}</b></div>
              <span>v_max = 이 속도보다 빠르면 기준을 넘는다고 예측되는 속도(선택한 관계식으로 계산){criterion?.critical_speed_mps ? ` · 이론 점프 속도 v_c = ${criterion.critical_speed_mps.toFixed(3)} m/s` : ''}. 검증: 실험하지 않은 질량을 넣고 그 속도 근처에서 다시 측정해 오차율을 구하세요.</span></div>
          </div>
          <div className="experiment-table-wrap"><table className="experiment-table"><thead><tr><th>총질량</th><th>출력</th><th>시행 (계산에 쓴 수)</th><th>평균 속도 (m/s)</th><th>평균 최대 충격 (g)</th><th>평균 '평소보다 더' (g)</th><th>표준편차 (g)</th><th>쓴 값 범위</th><th>뺀 값 (이상치)</th><th>충격력 (N)</th><th>Π</th><th>바퀴 뜸</th></tr></thead>
            <tbody>{bumpGroups.map(g => <tr key={`${g.total_mass_g}-${g.output_mode}-${g.output_value}`} className={aLimit != null && g.mean_peak_g != null && g.mean_peak_g > aLimit ? 'over' : ''}><td>{g.total_mass_g} g</td><td>{outputLabel(g.output_mode, g.output_value)}</td><td>{g.n_runs} ({g.n_used})</td><td>{fmt(g.mean_speed_mps, 3)}</td><td>{fmt(g.mean_peak_g, 3)}{aLimit != null && g.mean_peak_g != null && g.mean_peak_g > aLimit && <span className="exp-over">초과</span>}</td><td>{fmt(g.mean_peak_delta_g, 3)}</td><td>{fmt(g.sd_peak_delta_g, 3)}</td><td>{g.kept_min_delta_g.toFixed(2)}–{g.kept_max_delta_g.toFixed(2)}</td><td title={g.outlier?.low != null ? `기준: ${g.outlier.low} ~ ${g.outlier.high} g` : undefined}>{g.dropped_delta_g.map(v => v.toFixed(2)).join(', ') || '—'}{g.outlier?.low != null && <small className="exp-sub">범위 {g.outlier.low.toFixed(2)}~{(g.outlier.high ?? 0).toFixed(2)}</small>}</td><td>{fmt(g.mean_force_n, 2)}</td><td>{fmt(g.mean_pi, 2)}</td><td>{g.jump_count}/{g.n_runs}</td></tr>)}</tbody></table></div>
        </div>
        {chosenModel && comparison && <div className="card experiment-card"><ModelExplain model={chosenModel} comparison={comparison} aLimit={aLimit} vmax={vmaxChosen} /></div>}
        {comparison && comparison.models.length > 0 && <div className="card experiment-card"><div className="card-head"><div><h2>관계식 후보 비교</h2><p>식은 하나로 정해져 있지 않습니다. 같은 조건 평균 {comparison.points}개에 여러 식을 맞춰 비교합니다. <b>교차검증 RMSE</b>(한 점씩 빼고 맞춘 식이 그 점을 얼마나 틀리는지)가 작을수록 새 데이터를 잘 맞히는 식입니다. R²는 매개변수가 많을수록 커지므로 그것만으로 고르지 마세요.</p></div>
          <div className="segmented"><button type="button" className={(settings?.model_id ?? 'auto') === 'auto' ? 'selected' : ''} onClick={() => void patchSettings({ model_id: 'auto' })}>자동 선택</button></div></div>
          <div className="experiment-table-wrap"><table className="experiment-table exp-models"><thead><tr><th>사용</th><th>식</th><th>매개변수 (95% 신뢰구간)</th><th>R²</th><th>RMSE (g)</th><th>교차검증 RMSE (g)</th><th>AIC</th></tr></thead>
            <tbody>{comparison.models.map(m => <tr key={m.id} className={m.id === chosenId ? 'selected' : ''}><td><input type="radio" name={'model-' + bump} checked={m.id === chosenId} onChange={() => void patchSettings({ model_id: m.id })} aria-label={m.name} /></td><td><b>{m.name}</b><br /><small>{m.formula}</small>{m.id === comparison.best && <span className="exp-badge real">최적</span>}</td><td className="exp-params">{m.params.map(p => <div key={p.name}>{paramText(p)}</div>)}</td><td>{m.r2.toFixed(3)}</td><td>{m.rmse.toFixed(3)}</td><td>{fmt(m.loocv_rmse, 3)}</td><td>{fmt(m.aic, 1)}</td></tr>)}</tbody></table></div>
          {chosenModel && <XYChart name={`residual_${bump}`} title={`그래프 3. 관계식 예측과 실측 비교 (방지턱 ${bump}, ${chosenModel.name})`} xLabel="예측 Δa (g)" yLabel="실측 Δa (조건 평균, g)" identity band={0.1} xZero yZero
            series={Array.from(new Set(bumpGroups.map(g => g.total_mass_g))).map((m, i) => ({ name: `${m.toLocaleString()} g`, color: ['#2f6fb0', '#d0782a', '#3c9a5f', '#a04fa0'][i % 4],
              points: bumpGroups.filter(g => g.total_mass_g === m && g.mean_speed_mps && g.mean_peak_delta_g != null).map(g => ({ x: modelPredict(chosenModel, g.mean_speed_mps as number, m / 1000) ?? 0, y: g.mean_peak_delta_g as number })) }))}
            caption="점이 대각선(예측 = 실측)에 가까울수록 식이 잘 맞습니다. 초록 띠는 ±10%입니다. 한쪽으로 몰리면(예: 빠른 조건만 위) 식의 모양이 데이터와 다른 것입니다." />}
        </div>}
        {interpretation.length > 0 && <div className="card experiment-card exp-interpret"><div className="card-head"><div><h2>자동 해석 (방지턱 {bump})</h2><p>숫자는 모두 현재 데이터에서 계산했습니다. 보고서 결론 초안으로 옮겨 쓰고, 문장은 직접 다듬으세요.</p></div>
          <button className="secondary" onClick={() => { void navigator.clipboard?.writeText(interpretation.join('\n')).then(() => { setCopied(true); window.setTimeout(() => setCopied(false), 1500); }); }}><ClipboardCopy size={14} /> {copied ? '복사됨' : '복사'}</button></div>
          <ol>{interpretation.map((t, i) => <li key={i}>{t}</li>)}</ol></div>}
        <div className="card experiment-card"><div className="card-head"><div><h2>무차원 정리</h2><p>모든 방지턱의 포함된 시행을 함께 표시합니다.</p></div></div>
          <PiChart runs={runs.filter(r => !r.excluded)} title="그래프 2. 무차원수 Π와 최대 수직 가속도" name="graph2_pi_peak" />
        </div>
      </>}
    </section>
    </>}
    {!serverAvailable && <div className="card empty-chart"><Activity size={18} /> 서버 연결 후 사용할 수 있습니다.</div>}
  </>;
}
