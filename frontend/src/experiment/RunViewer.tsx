// 시행 보기: 한 회씩 넘겨 보기(◀ ▶, 키보드 ←/→)와 여러 회차 겹쳐 보기. 허용 기준을 넘은 충격은 빨간색으로 강조합니다.
import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, ChevronLeft, ChevronRight, Download, Layers, Square, Trash2 } from 'lucide-react';
import { fmt, outputLabel, PURPOSE_LABEL, type ExperimentSettings, type Purpose, type RunDetail, type RunsResponse, type RunSummary } from '../services/experiment';
import { MASS_COLORS, RunTraceChart, XYChart } from '../components/ExperimentCharts';

type Props = {
  runs: RunSummary[]; selectedId: string | null; setSelectedId: (id: string) => void; detail: RunDetail | null;
  settings: ExperimentSettings; data: RunsResponse | null;
  request: (path: string, options?: RequestInit & { timeoutMs?: number }) => Promise<any>;
  download: (path: string, name: string) => void;
  patchRun: (id: string, body: Record<string, unknown>, note?: string) => Promise<void>;
  toggleExcluded: (id: string, excluded: boolean) => Promise<void>;
  deleteRun: (id: string) => Promise<void>;
};
type Series = { id: string; condition: RunSummary['condition']; t: number[]; g: number[]; rear_t_s: number | null; peak_g: number | null; speed_mps: number | null; bump_detected: boolean | null; aligned_on?: 'front' | 'peak' };

const sameCondition = (a: RunSummary, b: RunSummary) => a.condition.bump_id === b.condition.bump_id && a.condition.total_mass_g === b.condition.total_mass_g
  && a.condition.output_mode === b.condition.output_mode && a.condition.output_value === b.condition.output_value && (a.condition.purpose ?? 'main') === (b.condition.purpose ?? 'main') && (a.condition.direction ?? 'forward') === (b.condition.direction ?? 'forward');
const readPref = <T extends string>(key: string, allowed: T[], fallback: T): T => { try { const v = localStorage.getItem(key) as T; return allowed.includes(v) ? v : fallback; } catch { return fallback; } };
const TRIAL_COLORS = ['#2f6fb0', '#d0782a', '#3c9a5f', '#a04fa0', '#b33a3a', '#5d6b7a', '#1f9e9e', '#8c6d1f'];

export function limitFor(run: RunSummary | null | undefined, data: RunsResponse | null, settings: Props['settings']): number | null {
  if (!run) return null;
  const c = data?.criteria?.[run.condition.bump_id];
  return c ? c.a_limit_g : settings.a_limit_effective_g ?? null;
}

export default function RunViewer({ runs, selectedId, setSelectedId, detail, settings, data, request, download, patchRun, toggleExcluded, deleteRun }: Props) {
  const [mode, setModeState] = useState<'single' | 'overlay'>(() => readPref('picarx-viewer-mode', ['single', 'overlay'], 'single'));
  const [scope, setScopeState] = useState<'condition' | 'all'>(() => readPref('picarx-viewer-scope', ['condition', 'all'], 'condition'));
  const setMode = (m: 'single' | 'overlay') => { setModeState(m); try { localStorage.setItem('picarx-viewer-mode', m); } catch { /* 무시 */ } };
  const setScope = (s: 'condition' | 'all') => { setScopeState(s); try { localStorage.setItem('picarx-viewer-scope', s); } catch { /* 무시 */ } };
  const [editing, setEditing] = useState(false);
  const [pick, setPick] = useState<{ front?: number; rear?: number }>({});
  const [overlay, setOverlay] = useState<Series[]>([]);
  const [loadingOverlay, setLoadingOverlay] = useState(false);

  const selected = runs.find(r => r.id === selectedId) ?? null;
  // 넘겨 보기 순서: 측정한 순서(오래된 것 → 최신)
  const ordered = useMemo(() => [...runs].sort((a, b) => a.created_at.localeCompare(b.created_at)), [runs]);
  const scoped = useMemo(() => selected && scope === 'condition' ? ordered.filter(r => sameCondition(r, selected)) : ordered, [ordered, selected, scope]);
  const index = selected ? scoped.findIndex(r => r.id === selected.id) : -1;
  const go = (step: number) => { if (!scoped.length) return; const next = scoped[(index + step + scoped.length) % scoped.length]; setSelectedId(next.id); setPick({}); };

  useEffect(() => {
    if (mode !== 'single') return;
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;
      if (e.key === 'ArrowLeft') { e.preventDefault(); go(-1); } else if (e.key === 'ArrowRight') { e.preventDefault(); go(1); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  const overlayIds = scoped.slice(-40).map(r => r.id).join(',');
  useEffect(() => {
    if (mode !== 'overlay' || !overlayIds) { setOverlay([]); return; }
    let cancelled = false;
    setLoadingOverlay(true);
    request(`/api/experiment/series?ids=${overlayIds}&before=0.15&after=0.4&points=350`, { timeoutMs: 20000 })
      .then(r => { if (!cancelled) setOverlay(r.series as Series[]); })
      .catch(() => { if (!cancelled) setOverlay([]); })
      .finally(() => { if (!cancelled) setLoadingOverlay(false); });
    return () => { cancelled = true; };
  }, [mode, overlayIds, request]);

  if (!selected) return <div className="card empty-chart">{runs.length ? '시행을 고르세요.' : '아직 저장된 시행이 없습니다. 조건을 입력하고 자동 측정 주행을 하세요.'}</div>;

  const a = selected.analysis;
  const aLimit = limitFor(selected, data, settings);
  const over = aLimit != null && a.peak_g != null && a.peak_g > aLimit;
  const group = (data?.groups ?? []).find(g => g.run_ids.includes(selected.id));
  const speedOff = a.speed_mps && group?.mean_speed_mps ? (a.speed_mps - group.mean_speed_mps) / group.mean_speed_mps : null;
  const log = detail && detail.id === selected.id ? detail.drive_log ?? [] : [];
  const outputs = Array.from(new Set(log.map(r => r[1])));
  const steers = log.map(r => r[2]);
  const condLabel = (r: RunSummary | Series) => `${r.condition.bump_id} · ${r.condition.total_mass_g.toLocaleString()} g · ${outputLabel(r.condition.output_mode, r.condition.output_value)}${r.condition.direction === 'backward' ? ' · 후진' : ''}`;

  // 겹쳐 보기 색: 같은 조건이면 회차별, 전체면 출력값별(질량은 선 모양)
  const outputsAll = Array.from(new Set(overlay.map(s => `${s.condition.output_mode}${s.condition.output_value}`)));
  const massesAll = Array.from(new Set(overlay.map(s => s.condition.total_mass_g))).sort((x, y) => x - y);
  const overlaySeries = overlay.map((s, i) => ({
    name: scope === 'condition' ? `${s.condition.trial}회차` : `${outputLabel(s.condition.output_mode, s.condition.output_value)} · ${s.condition.total_mass_g}g · ${s.condition.trial}회`,
    color: scope === 'condition' ? TRIAL_COLORS[i % TRIAL_COLORS.length] : MASS_COLORS[outputsAll.indexOf(`${s.condition.output_mode}${s.condition.output_value}`) % MASS_COLORS.length],
    dash: scope === 'all' && massesAll.indexOf(s.condition.total_mass_g) > 0 ? (massesAll.indexOf(s.condition.total_mass_g) === 1 ? '5 3' : '2 3') : undefined,
    line: true, dots: false, width: s.id === selected.id ? 2.6 : 1.3,
    points: s.t.map((t, k) => ({ x: t, y: s.g[k] })),
  }));

  const noBump = overlay.filter(s => s.aligned_on !== 'front' || s.bump_detected === false).length;
  const allNoBump = overlay.length > 0 && noBump === overlay.length;
  const zeroLabel = allNoBump ? '가장 큰 봉우리' : noBump ? '앞바퀴(일부는 가장 큰 봉우리)' : '앞바퀴';

  return <article className="card experiment-run">
    <div className="exp-viewer-bar">
      <div className="segmented"><button type="button" className={mode === 'single' ? 'selected' : ''} onClick={() => setMode('single')}><Square size={13} /> 한 회씩</button><button type="button" className={mode === 'overlay' ? 'selected' : ''} onClick={() => setMode('overlay')}><Layers size={13} /> 겹쳐 보기</button></div>
      <div className="segmented"><button type="button" className={scope === 'condition' ? 'selected' : ''} onClick={() => setScope('condition')}>같은 조건만</button><button type="button" className={scope === 'all' ? 'selected' : ''} onClick={() => setScope('all')}>전체 시행</button></div>
      {mode === 'single' && <div className="exp-pager"><button type="button" className="secondary" onClick={() => go(-1)} aria-label="이전 시행"><ChevronLeft size={16} /></button><b>{index + 1} / {scoped.length}</b><button type="button" className="secondary" onClick={() => go(1)} aria-label="다음 시행"><ChevronRight size={16} /></button><small>키보드 ← → 로도 넘겨요</small></div>}
    </div>

    {mode === 'overlay' ? <>
      <h3>{scope === 'condition' ? `${condLabel(selected)} · ${overlay.length}회 겹쳐 보기` : `전체 시행 ${overlay.length}회 겹쳐 보기${scoped.length > 40 ? ' (최근 40회)' : ''}`}</h3>
      {noBump > 0 && <p className="exp-hint exp-warnline"><AlertTriangle size={13} /> {allNoBump ? '이 회차들은 모두 방지턱을 찾지 못했습니다(평지처럼 주행 진동만 있음).' : `${noBump}개 회차는 방지턱을 찾지 못했습니다.`} 그런 회차는 앞바퀴 대신 <b>가장 큰 봉우리</b>를 0초로 맞췄고, 계산(관계식·평균)에서는 자동으로 빠집니다.</p>}
      <p className="exp-hint">모든 회차를 <b>{allNoBump ? '가장 큰 봉우리' : '앞바퀴가 방지턱에 닿은 순간'}(0초)</b>에 맞춰 겹쳤습니다. 방지턱 충격은 모든 회차에서 같은 자리에 솟아야 하고, 그 사이의 지그재그는 바퀴·기어에서 오는 주행 진동이라 회차마다 모양이 달라도 정상입니다. 선들이 잘 겹치면 측정이 고르게 반복된 것이고, 하나만 튀면 그 회차를 확인해 보세요.{scope === 'all' ? ' 색은 출력, 선 모양은 질량(실선 → 점선 순으로 무거움)입니다.' : ''} 굵은 선은 지금 고른 시행입니다.</p>
      {loadingOverlay ? <div className="empty-chart">불러오는 중…</div> : overlay.length === 0 ? <div className="empty-chart">겹칠 시행이 없습니다(방지턱 통과가 확인된 시행만 맞춰 그릴 수 있습니다).</div> :
        <XYChart name={`overlay_${scope}_${selected.condition.bump_id}`} title={scope === 'condition' ? `회차별 충격 비교 – ${condLabel(selected)}` : '전체 시행 충격 비교'} xLabel={`${zeroLabel} 기준 시간 (s)`} yLabel="위아래 가속도 (g, 정지 = 1)"
          series={overlaySeries} legendMax={scope === 'condition' ? 12 : 8}
          hLines={[{ y: 1, label: '정지 1 g', color: '#9aa3ad' }, ...(aLimit ? [{ y: aLimit, label: `허용 기준 ${aLimit.toFixed(2)} g`, color: '#c33' }] : [])]}
          vLines={[{ x: 0, label: allNoBump ? '가장 큰 봉우리' : '앞바퀴', color: '#d0782a' }]} />}
      <div className="experiment-table-wrap"><table className="experiment-table exp-clickable"><thead><tr><th>회차</th><th>조건</th><th>통과 속도 (m/s)</th><th>가장 큰 충격 (g)</th><th>허용 기준</th></tr></thead>
        <tbody>{overlay.map(s => { const lim = limitFor(runs.find(r => r.id === s.id), data, settings); const o = lim != null && s.peak_g != null && s.peak_g > lim;
          return <tr key={s.id} className={(s.id === selected?.id ? 'selected ' : '') + (o ? 'over' : '')} onClick={() => { setSelectedId(s.id); setMode('single'); }}><td>{s.condition.trial}</td><td>{condLabel(s)}</td><td>{s.bump_detected === false ? <span className="exp-badge warn">방지턱 못 찾음 · 계산 제외</span> : fmt(s.speed_mps, 3)}</td><td>{fmt(s.peak_g, 2)}</td><td>{lim == null ? '—' : o ? <span className="exp-over">초과</span> : '이내'}</td></tr>; })}</tbody></table></div>
      <p className="exp-hint">표의 줄을 누르면 그 시행을 한 회씩 보기로 엽니다.</p>
    </> : <>
      <div className="card-head"><div><h3>{condLabel(selected)} · {selected.condition.trial}회차{selected.excluded ? ' · 집계 제외' : ''}</h3>
        <p><span className={'exp-badge ' + (selected.simulated ? 'sim' : 'real')}>{selected.simulated ? '합성 데이터' : '실제 측정'}</span><span className="exp-badge">{PURPOSE_LABEL[(selected.condition.purpose ?? 'main') as Purpose]}</span>{a.bump_detected === false && <span className="exp-badge warn">방지턱 통과 확인 안 됨</span>}{over && <span className="exp-badge over">허용 기준 초과</span>}{a.pair_source === 'manual' && <span className="exp-badge warn">바퀴 피크 직접 지정</span>}{a.pair_source === 'consistency' && <span className="exp-badge warn">같은 조건 회차에 맞춰 뒷바퀴 다시 고름</span>}{a.pair_source === 'pitch' && <span className="exp-badge real">차 기울기로 앞·뒤 바퀴 확인</span>} {new Date(selected.created_at).toLocaleString()}{selected.battery_v ? ` · 배터리 ${selected.battery_v.toFixed(2)} V` : ''}</p></div>
        <div className="button-row"><button className="secondary" onClick={() => download(`/api/experiment/runs/${selected.id}/raw.csv`, `run-${selected.id}.csv`)}><Download size={14} /> 원자료 CSV</button><button className="secondary" onClick={() => void toggleExcluded(selected.id, !selected.excluded)}>{selected.excluded ? '집계에 다시 넣기' : '집계에서 빼기'}</button><button className="secondary" onClick={() => void deleteRun(selected.id)}><Trash2 size={14} /> 삭제</button></div></div>
      {detail && detail.id === selected.id ? <RunTraceChart key={selected.id + (editing ? 'e' : '')} series={detail.series} analysis={a} aLimit={aLimit} showCandidates={editing}
        thresholdDelta={Number(a.threshold_used_g ?? a.params?.threshold_delta_g ?? settings.threshold_delta_g)} jumpG={Number(a.params?.jump_g ?? settings.jump_g)}
        title={`위아래 가속도 – ${condLabel(selected)}, ${selected.condition.trial}회차`} name={`trace_${selected.condition.bump_id}_m${selected.condition.total_mass_g}_${selected.condition.output_mode}${selected.condition.output_value}_t${selected.condition.trial}`} />
        : <div className="empty-chart">그래프 불러오는 중…</div>}
      <div className="exp-stats">
        <div className={over ? 'over' : ''}><small>가장 큰 충격</small><b>{fmt(a.peak_g, 2, 'g')}</b><span>정지(1 g)보다 {fmt(a.peak_delta_g, 2, 'g')} 더 받음{aLimit ? ` · 허용 기준 ${aLimit.toFixed(2)} g ${over ? '초과' : '이내'}` : ''}</span></div>
        <div><small>방지턱 통과 속도</small><b>{fmt(a.speed_mps, 3, 'm/s')}</b><span>앞·뒤 바퀴 사이 {a.axle_dt_s == null ? '—' : (a.axle_dt_s * 1000).toFixed(0) + ' ms'}{selected.condition.video_speed_mps ? ` · 영상 ${selected.condition.video_speed_mps} m/s` : ''}</span></div>
        <div><small>바퀴가 떴나?</small><b>{a.jump == null ? '판단 불가' : a.jump ? '떴음' : '안 뜸'}</b><span>{a.jump ? `공중 ${a.airborne_s ? (a.airborne_s * 1000).toFixed(0) + ' ms' : '—'}${a.landing_g ? ` · 착지 충격 ${a.landing_g.toFixed(2)} g` : ''}` : `가장 낮은 값 ${fmt(a.min_g, 2, 'g')} (0.2 g 아래면 뜬 것)`}</span></div>
        <div><small>충격력 (질량 × 가속도)</small><b>{fmt(a.peak_force_n, 1, 'N')}</b><span>정지 무게 대비 {fmt(a.dynamic_force_n, 1, 'N')} 더</span></div>
        <div><small>속도·곡률 지수 Π</small><b>{fmt(a.pi, 2)}</b><span>1을 넘으면 이론상 뜸 · 뜨기 시작하는 속도 {fmt(a.critical_speed_mps, 2, 'm/s')}</span></div>
        <div><small>주행 진동 크기</small><b>{fmt(a.noise_sigma_g, 3, 'g')}</b><span>피크로 인정한 기준 {fmt(a.threshold_used_g, 2, 'g')} 이상{a.standout_ratio ? ` · 바퀴 피크가 진동의 ${a.standout_ratio}배` : ''}{(a.noise_sigma_g ?? 0) > 0.12 ? ' · 진동이 큰 편: 센서 고정·바퀴 상태를 확인하세요' : ''}</span></div>
      </div>
      {log.length > 0 && <p className="exp-hint exp-drivelog">{(selected as RunSummary & { bump_passed_s?: number | null; elapsed_s?: number | null }).bump_passed_s != null ? `방지턱을 ${(selected as RunSummary & { bump_passed_s?: number }).bump_passed_s?.toFixed(2)}초에 다 넘어 자동 정지(총 ${(selected as RunSummary & { elapsed_s?: number }).elapsed_s?.toFixed(2)}초 주행) · ` : ''}모터 출력: {outputs.length === 1 ? `처음부터 끝까지 ${outputLabel(selected.condition.output_mode, outputs[0])}로 고정` : `${outputs.join(' → ')} (출발 밀어주기 후 고정)`} · 조향 {Math.min(...steers).toFixed(1)}° ~ {Math.max(...steers).toFixed(1)}°{selected.heading_max_deg != null ? ` · 차 방향이 가장 많이 틀어진 각도 ${selected.heading_max_deg.toFixed(1)}°` : ''}</p>}
      {a.flags.length > 0 && <ul className="exp-flags">{a.flags.map(f => <li key={f}><AlertTriangle size={13} />{f}</li>)}</ul>}
      {speedOff != null && Math.abs(speedOff) > 0.2 && <ul className="exp-flags"><li><AlertTriangle size={13} />이 시행 속도가 같은 조건 평균({fmt(group?.mean_speed_mps, 3)} m/s)과 {(speedOff * 100).toFixed(0)}% 다릅니다. 앞·뒤 바퀴 피크를 맞게 골랐는지 아래에서 확인하세요.</li></ul>}
      <details className="exp-peaks" open={editing} onToggle={e => setEditing((e.target as HTMLDetailsElement).open)}>
        <summary>앞·뒤 바퀴 피크 확인·직접 고르기</summary>
        <p className="exp-hint">차는 앞으로 가니까 <b>먼저 튄 것이 앞바퀴, 나중 것이 뒷바퀴</b>입니다. 이 칸을 열면 그래프에 회색 점선으로 <b>후보 1, 2, 3…</b>이 표시됩니다(아래 목록과 번호가 같습니다). 앞바퀴·뒷바퀴로 쓸 후보를 하나씩 고르고 '이걸로 계산'을 누르세요.</p>
        <div className="exp-cand-list">{(a.candidates ?? []).map((c, i) => { const isF = c.t_s === a.front_t_s, isR = c.t_s === a.rear_t_s;
          return <div key={i} className={'exp-cand' + (isF ? ' front' : isR ? ' rear' : '')}><b>후보 {i + 1}</b><span>{c.t_s.toFixed(3)}초 · {c.g.toFixed(2)} g{c.landing ? ' · 착지' : ''}{isF ? ' · 지금 앞바퀴' : isR ? ' · 지금 뒷바퀴' : ''}</span>
            <button type="button" className={pick.front === c.t_s ? 'secondary selected' : 'secondary'} onClick={() => setPick(p => ({ ...p, front: c.t_s }))}>앞바퀴로</button>
            <button type="button" className={pick.rear === c.t_s ? 'secondary selected' : 'secondary'} onClick={() => setPick(p => ({ ...p, rear: c.t_s }))}>뒷바퀴로</button></div>; })}
          {(a.candidates ?? []).length === 0 && <span className="exp-hint">진동보다 뚜렷한 피크가 없습니다.</span>}</div>
        <div className="button-row">
          <span className="exp-hint">{pick.front != null && pick.rear != null ? `간격 ${((pick.rear - pick.front) * 1000).toFixed(0)} ms → 속도 ${settings.wheelbase_mm ? (settings.wheelbase_mm / 1000 / (pick.rear - pick.front)).toFixed(3) + ' m/s' : '축간거리 필요'}` : '앞바퀴와 뒷바퀴 후보를 하나씩 고르세요.'}</span>
          <button className="primary" disabled={pick.front == null || pick.rear == null || (pick.rear ?? 0) <= (pick.front ?? 0)} onClick={() => void patchRun(selected.id, { peak_override: { front_t_s: pick.front, rear_t_s: pick.rear } }, '고른 피크로 다시 계산했습니다.').then(() => setPick({}))}>이걸로 계산</button>
          {a.pair_source === 'manual' && <button className="secondary" onClick={() => void patchRun(selected.id, { clear_peak_override: true }, '자동 선택으로 되돌렸습니다.')}>자동으로 되돌리기</button>}
          <select aria-label="측정 목적" value={selected.condition.purpose ?? 'main'} onChange={e => void patchRun(selected.id, { purpose: e.target.value }, '측정 목적을 바꿨습니다.')}>{(['main', 'validation', 'pilot'] as Purpose[]).map(p => <option key={p} value={p}>{PURPOSE_LABEL[p]}</option>)}</select>
        </div>
      </details>
    </>}
  </article>;
}
