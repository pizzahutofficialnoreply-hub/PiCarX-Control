// 진행표에서 고른 조건(복수 선택)의 충격 파형과 평균값을 한 그래프에 겹쳐 비교합니다.
import { useEffect, useMemo, useState } from 'react';
import { X } from 'lucide-react';
import { fmt, type ExperimentSettings, type RunSummary } from '../services/experiment';
import { MASS_COLORS, XYChart, type XYSeries } from '../components/ExperimentCharts';

export type CellKey = { bump_id: string; added_mass_g: number; output_value: number };
export const cellId = (c: CellKey) => `${c.bump_id}|${c.added_mass_g}|${c.output_value}`;

type Trace = { id: string; condition: RunSummary['condition']; t: number[]; g: number[]; bump_detected: boolean | null; aligned_on?: 'front' | 'peak'; peak_g: number | null; speed_mps: number | null };
type Props = {
  cells: CellKey[]; runs: RunSummary[]; settings: ExperimentSettings; aLimit: number | null;
  request: (path: string, options?: RequestInit & { timeoutMs?: number }) => Promise<any>;
  onClear: () => void; onRemove: (c: CellKey) => void;
};

const EXTRA = ['#1f9e9e', '#8c6d1f', '#c2185b', '#455a64'];
const COLORS = [...MASS_COLORS, ...EXTRA];
const DASHES = [undefined, '6 3', '2 3', '8 3 2 3'];
const GRID = Array.from({ length: Math.round((0.4 + 0.15) / 0.002) + 1 }, (_, i) => Number((-0.15 + i * 0.002).toFixed(4)));
const mean = (xs: number[]) => xs.length ? xs.reduce((s, x) => s + x, 0) / xs.length : null;
const sd = (xs: number[]) => { const m = mean(xs); return m == null || xs.length < 2 ? null : Math.sqrt(xs.reduce((s, x) => s + (x - m) ** 2, 0) / (xs.length - 1)); };

function interp(t: number[], g: number[], x: number): number | null {
  if (!t.length || x < t[0] || x > t[t.length - 1]) return null;
  let lo = 0, hi = t.length - 1;
  while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (t[mid] <= x) lo = mid; else hi = mid; }
  const span = t[hi] - t[lo];
  return span <= 0 ? g[lo] : g[lo] + (g[hi] - g[lo]) * (x - t[lo]) / span;
}

export default function PlanCompare({ cells, runs, settings, aLimit, request, onClear, onRemove }: Props) {
  const [show, setShow] = useState<'mean' | 'all'>(() => { try { return localStorage.getItem('picarx-compare-show') === 'all' ? 'all' : 'mean'; } catch { return 'mean'; } });
  const [by, setBy] = useState<'speed' | 'mass'>(() => { try { return localStorage.getItem('picarx-compare-by') === 'mass' ? 'mass' : 'speed'; } catch { return 'speed'; } });
  const [traces, setTraces] = useState<Trace[]>([]);
  const [loading, setLoading] = useState(false);
  useEffect(() => { try { localStorage.setItem('picarx-compare-show', show); localStorage.setItem('picarx-compare-by', by); } catch { /* 무시 */ } }, [show, by]);

  const dir = settings.analysis_direction ?? 'forward';
  const runsOf = useMemo(() => {
    const m = new Map<string, RunSummary[]>();
    for (const c of cells) m.set(cellId(c), runs.filter(r => !r.excluded && (r.condition.purpose ?? 'main') === 'main' && (r.condition.direction ?? 'forward') === dir
      && r.condition.bump_id === c.bump_id && Math.round(r.condition.added_mass_g) === Math.round(c.added_mass_g) && Math.round(r.condition.output_value) === Math.round(c.output_value)));
    return m;
  }, [cells, runs, dir]);
  const ids = cells.flatMap(c => (runsOf.get(cellId(c)) ?? []).map(r => r.id)).slice(0, 200).join(',');

  useEffect(() => {
    if (!ids) { setTraces([]); return; }
    let cancelled = false;
    setLoading(true);
    request(`/api/experiment/series?ids=${ids}&before=0.15&after=0.4&points=280`, { timeoutMs: 30000 })
      .then(r => { if (!cancelled) setTraces(r.series as Trace[]); })
      .catch(() => { if (!cancelled) setTraces([]); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [ids, request]);

  if (!cells.length) return <p className="exp-hint exp-compare-empty">표의 칸을 누르면 그 조건의 충격 그래프가 여기에 나옵니다. 여러 칸을 누르면 겹쳐 비교하고, 행(질량)·열(출력) 제목을 누르면 줄 전체를 고릅니다.</p>;

  // 색 = 비교하려는 변수(속도 비교면 출력, 질량 비교면 질량), 선 모양 = 나머지 변수
  const outs = Array.from(new Set(cells.map(c => c.output_value))).sort((a, b) => a - b);
  const masses = Array.from(new Set(cells.map(c => c.added_mass_g))).sort((a, b) => a - b);
  const bumps = Array.from(new Set(cells.map(c => c.bump_id)));
  const colorOf = (c: CellKey) => COLORS[(by === 'speed' ? outs.indexOf(c.output_value) : masses.indexOf(c.added_mass_g)) % COLORS.length];
  const dashOf = (c: CellKey) => DASHES[(by === 'speed' ? masses.indexOf(c.added_mass_g) : outs.indexOf(c.output_value)) % DASHES.length];
  const label = (c: CellKey) => `${bumps.length > 1 ? c.bump_id + ' · ' : ''}출력 ${c.output_value} · +${c.added_mass_g} g`;
  const keyOfRun = (cond: RunSummary['condition']) => cells.find(c => c.bump_id === cond.bump_id && Math.round(c.added_mass_g) === Math.round(cond.added_mass_g) && Math.round(c.output_value) === Math.round(cond.output_value));

  const good = traces.filter(s => s.bump_detected !== false && s.aligned_on === 'front');
  const series: XYSeries[] = [];
  const sorted = [...cells].sort((a, b) => by === 'speed' ? a.output_value - b.output_value || a.added_mass_g - b.added_mass_g : a.added_mass_g - b.added_mass_g || a.output_value - b.output_value);
  for (const c of sorted) {
    const mine = good.filter(s => keyOfRun(s.condition) && cellId(keyOfRun(s.condition) as CellKey) === cellId(c));
    if (!mine.length) continue;
    if (show === 'all') {
      mine.forEach(s => series.push({ name: `${label(c)} · ${s.condition.trial}회`, color: colorOf(c), dash: dashOf(c), line: true, dots: false, width: 1.2, points: s.t.map((t, k) => ({ x: t, y: s.g[k] })) }));
    } else {
      const pts = GRID.map(x => { const vals = mine.map(s => interp(s.t, s.g, x)).filter((v): v is number => v != null); return vals.length >= Math.max(1, Math.ceil(mine.length / 2)) ? { x, y: mean(vals) as number } : null; }).filter((p): p is { x: number; y: number } => p != null);
      series.push({ name: `${label(c)} (평균 n=${mine.length})`, color: colorOf(c), dash: dashOf(c), line: true, dots: false, width: 2, points: pts });
    }
  }

  // 조건별 요약값(평균 ± 표준편차)
  const rows = sorted.map(c => {
    const rs = (runsOf.get(cellId(c)) ?? []).filter(r => r.analysis.bump_detected !== false);
    const v = rs.map(r => r.analysis.speed_mps).filter((x): x is number => x != null);
    const d = rs.map(r => r.analysis.peak_delta_g).filter((x): x is number => x != null);
    const mass = rs[0]?.condition.total_mass_g ?? null;
    return { c, n: (runsOf.get(cellId(c)) ?? []).length, used: rs.length, v, d, mass, mv: mean(v), sv: sd(v), md: mean(d), sdd: sd(d) };
  });

  // 평균값 비교 그래프: 속도 비교면 x = 통과 속도(질량별 선), 질량 비교면 x = 총 질량(출력별 선)
  const groupsKey = by === 'speed' ? masses : outs;
  const summarySeries: XYSeries[] = groupsKey.map((gk, gi) => {
    const mine = rows.filter(r => (by === 'speed' ? r.c.added_mass_g : r.c.output_value) === gk);
    const color = COLORS[gi % COLORS.length];
    const name = by === 'speed' ? `+${gk} g` : `출력 ${gk}`;
    const points = show === 'all'
      ? mine.flatMap(r => (runsOf.get(cellId(r.c)) ?? []).filter(x => x.analysis.bump_detected !== false && x.analysis.peak_delta_g != null && (by === 'mass' || x.analysis.speed_mps != null))
          .map(x => ({ x: by === 'speed' ? x.analysis.speed_mps as number : x.condition.total_mass_g, y: x.analysis.peak_delta_g as number, label: undefined })))
      : mine.filter(r => r.md != null && (by === 'mass' ? r.mass != null : r.mv != null)).map(r => ({ x: by === 'speed' ? r.mv as number : r.mass as number, y: r.md as number, label: `출력 ${r.c.output_value} · +${r.c.added_mass_g} g · n=${r.used}` }))
          .sort((a, b) => a.x - b.x);
    return { name, color, points, line: show === 'mean' && points.length > 1, dots: true };
  }).filter(s => s.points.length);

  return <div className="exp-compare">
    <div className="exp-compare-bar">
      <div className="segmented"><button type="button" className={show === 'mean' ? 'selected' : ''} onClick={() => setShow('mean')}>평균값</button><button type="button" className={show === 'all' ? 'selected' : ''} onClick={() => setShow('all')}>모든 값</button></div>
      <div className="segmented"><button type="button" className={by === 'speed' ? 'selected' : ''} onClick={() => setBy('speed')}>속도(출력)별 비교</button><button type="button" className={by === 'mass' ? 'selected' : ''} onClick={() => setBy('mass')}>질량별 비교</button></div>
      <div className="exp-compare-chips">{sorted.map(c => <span key={cellId(c)} className="exp-chip" style={{ borderColor: colorOf(c) }}><i style={{ background: colorOf(c) }} />{label(c)}<button type="button" aria-label="선택 빼기" onClick={() => onRemove(c)}><X size={11} /></button></span>)}</div>
      <button type="button" className="secondary" onClick={onClear}>선택 모두 해제</button>
    </div>
    {loading ? <div className="empty-chart">불러오는 중…</div> : series.length === 0 ? <div className="empty-chart">고른 조건에 방지턱 통과가 확인된 시행이 없습니다.</div> :
      <XYChart name={`compare_${show}_${by}`} title={`${show === 'mean' ? '조건별 평균 충격 파형' : '조건별 모든 회차 충격 파형'} (${by === 'speed' ? '색 = 출력, 선 모양 = 질량' : '색 = 질량, 선 모양 = 출력'})`}
        xLabel="앞바퀴 기준 시간 (s)" yLabel="위아래 가속도 (g, 정지 = 1)" series={series} legendMax={show === 'mean' ? 12 : 0}
        hLines={[{ y: 1, label: '정지 1 g', color: '#9aa3ad' }, ...(aLimit ? [{ y: aLimit, label: `허용 기준 ${aLimit.toFixed(2)} g`, color: '#c33' }] : [])]}
        vLines={[{ x: 0, label: '앞바퀴', color: '#d0782a' }]}
        caption={show === 'mean' ? '각 조건의 회차들을 앞바퀴가 방지턱에 닿은 순간(0초)에 맞춘 뒤, 같은 시각끼리 평균 낸 파형입니다. 평균하면 회차마다 다른 주행 진동은 줄고 방지턱 충격 모양만 남습니다. 그래프 위에 마우스를 올리면 그 시각의 각 조건 값이 나옵니다.' : '고른 조건의 모든 회차를 앞바퀴 기준으로 겹쳤습니다. 같은 색끼리 잘 겹치면 반복이 고르게 된 것입니다.'} />}
    {summarySeries.length > 0 && <XYChart name={`compare_summary_${show}_${by}`} title={by === 'speed' ? '통과 속도에 따른 충격 (선택 조건)' : '총 질량에 따른 충격 (선택 조건)'}
      xLabel={by === 'speed' ? '방지턱 통과 속도 v (m/s)' : '차 전체 질량 (g)'} yLabel="평소보다 더 받은 가속도 Δa (g)" series={summarySeries} yZero
      hLines={aLimit ? [{ y: aLimit - 1, label: `허용 기준 +${(aLimit - 1).toFixed(2)} g`, color: '#c33' }] : undefined}
      caption={show === 'mean' ? '점 하나 = 한 조건의 평균(방지턱을 못 찾은 회차는 뺌). 같은 색 선으로 이어 비교 변수에 따라 충격이 어떻게 변하는지 봅니다.' : '점 하나 = 한 회차. 같은 조건끼리 얼마나 흩어졌는지 봅니다.'} />}
    <div className="experiment-table-wrap"><table className="experiment-table"><thead><tr><th>조건</th><th>측정 (사용)</th><th>통과 속도 (m/s)</th><th>Δa 평균 (g)</th><th>Δa 범위</th></tr></thead>
      <tbody>{rows.map(r => <tr key={cellId(r.c)}><td><i className="exp-swatch" style={{ background: colorOf(r.c) }} />{label(r.c)}</td><td>{r.n} ({r.used})</td><td>{fmt(r.mv, 3)}{r.sv != null ? ` ± ${r.sv.toFixed(3)}` : ''}</td><td>{r.md == null ? '—' : `+${r.md.toFixed(3)}`}{r.sdd != null ? ` ± ${r.sdd.toFixed(3)}` : ''}</td><td>{r.d.length ? `${Math.min(...r.d).toFixed(2)} ~ ${Math.max(...r.d).toFixed(2)}` : '—'}</td></tr>)}</tbody></table></div>
  </div>;
}
