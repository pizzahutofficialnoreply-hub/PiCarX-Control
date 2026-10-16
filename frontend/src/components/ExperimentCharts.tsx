// 실험 그래프: 보고서 그림으로 바로 쓸 수 있도록 흰 바탕·고정 색으로 그리고,
// 화면에 보이는 그대로 PNG/SVG로 저장합니다.
import { useMemo, useRef, useState, type ReactNode } from 'react';
import { Download, Image as ImageIcon } from 'lucide-react';
import { modelPredict, modelSolveSpeed, type ConditionGroup, type ModelResult, type PowerFit, type RunAnalysis, type RunSummary } from '../services/experiment';

const W = 640, H = 360, M = { l: 58, r: 18, t: 30, b: 48 };
const PW = W - M.l - M.r, PH = H - M.t - M.b;
const INK = '#1f2328', MUTED = '#6a737d', GRID = '#e3e6ea', FONT = "'Pretendard','Noto Sans KR','Malgun Gothic',system-ui,sans-serif";
export const MASS_COLORS = ['#2f6fb0', '#d0782a', '#3c9a5f', '#a04fa0', '#b33a3a', '#5d6b7a'];

function niceStep(span: number, count: number): number {
  const raw = span / Math.max(1, count);
  const pow = 10 ** Math.floor(Math.log10(raw || 1));
  const n = raw / pow;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * pow;
}
function ticks(min: number, max: number, count = 5): number[] {
  const step = niceStep(max - min, count);
  const out: number[] = [];
  for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-6; v += step) out.push(Number(v.toFixed(10)));
  return out;
}
function niceRange(values: number[], padRatio = 0.08, includeZero = false): [number, number] {
  const finite = values.filter(Number.isFinite);
  if (!finite.length) return [0, 1];
  let lo = Math.min(...finite), hi = Math.max(...finite);
  if (includeZero) lo = Math.min(0, lo);
  if (hi - lo < 1e-9) { hi += 0.5; lo -= includeZero ? 0 : 0.5; }
  const pad = (hi - lo) * padRatio;
  const step = niceStep(hi - lo + 2 * pad, 5);
  return [includeZero && lo >= 0 ? 0 : Math.floor((lo - pad) / step) * step, Math.ceil((hi + pad) / step) * step];
}
// 선 위 글자가 묻히지 않도록 흰 테두리를 두릅니다.
const HALO = { stroke: '#ffffff', strokeWidth: 3.5, paintOrder: 'stroke' as const, strokeLinejoin: 'round' as const };
const tickLabel = (v: number) => Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 10 ? v.toFixed(1).replace(/\.0$/, '') : Number(v.toPrecision(3)).toString();

function Axes({ x, y, xLabel, yLabel, title }: { x: [number, number]; y: [number, number]; xLabel: string; yLabel: string; title: string }) {
  const sx = (v: number) => M.l + (v - x[0]) / (x[1] - x[0]) * PW;
  const sy = (v: number) => M.t + PH - (v - y[0]) / (y[1] - y[0]) * PH;
  return <g fontFamily={FONT}>
    <rect x={0} y={0} width={W} height={H} fill="#ffffff" />
    <text x={M.l} y={18} fontSize={13} fontWeight={600} fill={INK}>{title}</text>
    {ticks(y[0], y[1]).map(v => <g key={'y' + v}><line x1={M.l} x2={M.l + PW} y1={sy(v)} y2={sy(v)} stroke={GRID} /><text x={M.l - 7} y={sy(v) + 4} fontSize={10.5} textAnchor="end" fill={MUTED}>{tickLabel(v)}</text></g>)}
    {ticks(x[0], x[1], 6).map(v => <g key={'x' + v}><line x1={sx(v)} x2={sx(v)} y1={M.t} y2={M.t + PH} stroke={GRID} /><text x={sx(v)} y={M.t + PH + 16} fontSize={10.5} textAnchor="middle" fill={MUTED}>{tickLabel(v)}</text></g>)}
    <rect x={M.l} y={M.t} width={PW} height={PH} fill="none" stroke="#c9ced4" />
    <text x={M.l + PW / 2} y={H - 10} fontSize={11.5} textAnchor="middle" fill={INK}>{xLabel}</text>
    <text transform={`translate(15 ${M.t + PH / 2}) rotate(-90)`} fontSize={11.5} textAnchor="middle" fill={INK}>{yLabel}</text>
  </g>;
}
function scales(x: [number, number], y: [number, number]) {
  return {
    sx: (v: number) => M.l + (v - x[0]) / (x[1] - x[0]) * PW,
    sy: (v: number) => M.t + PH - (v - y[0]) / (y[1] - y[0]) * PH,
  };
}

async function saveSvg(svg: SVGSVGElement, name: string, format: 'png' | 'svg') {
  const clone = svg.cloneNode(true) as SVGSVGElement;
  clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
  clone.setAttribute('width', String(W));
  clone.setAttribute('height', String(H));
  const text = new XMLSerializer().serializeToString(clone);
  const download = (url: string, ext: string) => { const a = document.createElement('a'); a.href = url; a.download = `${name}.${ext}`; a.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000); };
  if (format === 'svg') { download(URL.createObjectURL(new Blob([text], { type: 'image/svg+xml' })), 'svg'); return; }
  const img = new Image();
  const src = URL.createObjectURL(new Blob([text], { type: 'image/svg+xml;charset=utf-8' }));
  await new Promise<void>((resolve, reject) => { img.onload = () => resolve(); img.onerror = () => reject(new Error('그래프 이미지를 만들지 못했습니다.')); img.src = src; });
  const scale = 3; // 보고서 인쇄용 고해상도(1920×1080)
  const canvas = document.createElement('canvas');
  canvas.width = W * scale; canvas.height = H * scale;
  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  ctx.fillStyle = '#ffffff'; ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
  URL.revokeObjectURL(src);
  canvas.toBlob(blob => { if (blob) download(URL.createObjectURL(blob), 'png'); }, 'image/png');
}

export function Figure({ name, caption, tools, children }: { name: string; caption?: ReactNode; tools?: ReactNode; children: (ref: React.RefObject<SVGSVGElement>) => ReactNode }) {
  const ref = useRef<SVGSVGElement>(null);
  return <figure className="exp-figure">
    <div className="exp-figure-tools">{tools}<span className="exp-figure-spacer" /><button type="button" className="secondary" onClick={() => ref.current && void saveSvg(ref.current, name, 'png')}><ImageIcon size={14} /> PNG</button><button type="button" className="secondary" onClick={() => ref.current && void saveSvg(ref.current, name, 'svg')}><Download size={14} /> SVG</button></div>
    <div className="exp-figure-canvas">{children(ref)}</div>
    {caption && <figcaption>{caption}</figcaption>}
  </figure>;
}

/* ------------------------------------------------------------------ 시행 원자료 */
export function RunTraceChart({ series, analysis, thresholdDelta, jumpG, title, name, showCandidates = false, aLimit = null }: {
  series: { t_s: number[]; vertical_g: number[]; filtered_g?: number[] }; analysis: RunAnalysis; thresholdDelta: number; jumpG: number; title: string; name: string;
  showCandidates?: boolean; aLimit?: number | null;
}) {
  const hasPair = analysis.front_t_s != null && analysis.rear_t_s != null;
  const [zoom, setZoom] = useState<'bump' | 'all'>(hasPair ? 'bump' : 'all');
  const view = useMemo(() => {
    let lo = series.t_s[0] ?? 0, hi = series.t_s[series.t_s.length - 1] ?? 1;
    if (zoom === 'bump' && hasPair) { lo = (analysis.front_t_s as number) - 0.15; hi = (analysis.rear_t_s as number) + 0.3; }
    const idx = series.t_s.map((t, i) => i).filter(i => series.t_s[i] >= lo && series.t_s[i] <= hi);
    return { lo, hi, idx };
  }, [series, zoom, hasPair, analysis.front_t_s, analysis.rear_t_s]);
  const main = series.filtered_g && series.filtered_g.length === series.t_s.length ? series.filtered_g : series.vertical_g;
  const filtered = main !== series.vertical_g;
  const values = view.idx.map(i => main[i]);
  const y = niceRange([...values, 0, 1 + thresholdDelta, 2, ...(aLimit ? [aLimit] : [])], 0.06, true);
  const x: [number, number] = [view.lo, view.hi];
  const { sx, sy } = scales(x, y);
  const toPath = (arr: number[]) => view.idx.map((i, n) => `${n ? 'L' : 'M'}${sx(series.t_s[i]).toFixed(1)},${sy(Math.max(y[0], Math.min(y[1], arr[i]))).toFixed(1)}`).join('');
  const path = toPath(main);
  const rawPath = filtered ? toPath(series.vertical_g) : '';
  const overColor = '#c62828';
  const marker = (t: number | null, g: number | null, label: string, color: string) => {
    if (t == null || g == null || t < x[0] || t > x[1]) return null;
    const isOver = aLimit != null && g > aLimit;
    const c = isOver ? overColor : color;
    return <g><line x1={sx(t)} x2={sx(t)} y1={M.t} y2={M.t + PH} stroke={color} strokeDasharray="3 3" />
      <circle cx={sx(t)} cy={sy(Math.min(g, y[1]))} r={isOver ? 6 : 4.5} fill={c} stroke={isOver ? '#fff' : 'none'} strokeWidth={1.5} />
      <text x={sx(t) + 7} y={sy(Math.min(g, y[1])) - 6} fontSize={11.5} fill={c} fontFamily={FONT} fontWeight={700} {...HALO}>{label} {g.toFixed(2)} g{isOver ? ' · 기준 초과' : ''}</text></g>;
  };
  const cands = (analysis.candidates ?? []).map((c, i) => ({ ...c, n: i + 1 }));
  return <Figure name={name} tools={<div className="segmented"><button type="button" className={zoom === 'bump' ? 'selected' : ''} disabled={!hasPair} onClick={() => setZoom('bump')}>방지턱 부분 확대</button><button type="button" className={zoom === 'all' ? 'selected' : ''} onClick={() => setZoom('all')}>전체 기록</button></div>}
    caption={<>파란 선 = 차체가 위아래로 받은 가속도(가만히 있으면 1 g){filtered ? ', 진동을 거른 값 · 연한 회색 = 거르기 전 원래 기록' : ''}. 주황 ● 앞바퀴, 초록 ● 뒷바퀴가 방지턱에 닿은 순간이고, 두 점 사이 시간으로 속도를 계산합니다.{aLimit ? ' 빨간 선 위로 올라가면 허용 기준을 넘은 충격입니다.' : ''}</>}>
    {ref => <svg ref={ref} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={title}>
      <Axes x={x} y={y} xLabel="시간 (초)" yLabel="위아래 가속도 (g)" title={title} />
      {aLimit != null && aLimit <= y[1] && <><rect x={M.l} y={M.t} width={PW} height={Math.max(0, sy(aLimit) - M.t)} fill={overColor} opacity={0.06} />
        <line x1={M.l} x2={M.l + PW} y1={sy(aLimit)} y2={sy(aLimit)} stroke={overColor} strokeWidth={1.4} strokeDasharray="7 4" />
        <text x={M.l + 4} y={sy(aLimit) - 5} fontSize={10.5} fill={overColor} fontWeight={600} fontFamily={FONT} {...HALO}>허용 기준 {aLimit.toFixed(2)} g</text></>}
      <line x1={M.l} x2={M.l + PW} y1={sy(1)} y2={sy(1)} stroke="#9aa3ad" strokeDasharray="5 4" />
      <text x={M.l + PW - 4} y={sy(1) - 5} fontSize={10} textAnchor="end" fill={MUTED} fontFamily={FONT} {...HALO}>가만히 있을 때 1 g</text>
      {showCandidates && <><line x1={M.l} x2={M.l + PW} y1={sy(1 + thresholdDelta)} y2={sy(1 + thresholdDelta)} stroke="#d0a12a" strokeDasharray="2 4" />
        <text x={M.l + PW - 4} y={sy(1 + thresholdDelta) + 13} fontSize={10} textAnchor="end" fill="#a77f12" fontFamily={FONT} {...HALO}>이 선 위의 봉우리만 후보 ({(1 + thresholdDelta).toFixed(2)} g)</text></>}
      {y[0] <= jumpG && <><line x1={M.l} x2={M.l + PW} y1={sy(jumpG)} y2={sy(jumpG)} stroke="#8a939c" strokeDasharray="2 4" /><text x={M.l + PW - 4} y={sy(jumpG) + 13} fontSize={10} textAnchor="end" fill={MUTED} fontFamily={FONT} {...HALO}>이 아래면 바퀴가 뜬 것 ({jumpG} g)</text></>}
      {filtered && <path d={rawPath} fill="none" stroke="#9aa3ad" strokeWidth={0.8} opacity={0.55} />}
      <path d={path} fill="none" stroke="#2f6fb0" strokeWidth={filtered ? 1.8 : 1.4} strokeLinejoin="round" />
      {showCandidates && cands.map(c => c.t_s < x[0] || c.t_s > x[1] ? null :
        <g key={'cand' + c.n} fontFamily={FONT}><line x1={sx(c.t_s)} x2={sx(c.t_s)} y1={M.t + 18} y2={M.t + PH} stroke="#6a737d" strokeDasharray="1 3" />
          <rect x={sx(c.t_s) - 22} y={M.t + 2} width={44} height={15} rx={4} fill="#fff" stroke="#6a737d" /><text x={sx(c.t_s)} y={M.t + 13} fontSize={10} textAnchor="middle" fill={INK} fontWeight={600}>후보 {c.n}</text></g>)}
      {marker(analysis.front_t_s, analysis.front_g, '앞바퀴', '#d0782a')}
      {marker(analysis.rear_t_s, analysis.rear_g, '뒷바퀴', '#3c9a5f')}
      {analysis.landing_t_s != null && analysis.landing_t_s !== analysis.rear_t_s && marker(analysis.landing_t_s, analysis.landing_g ?? null, '착지', '#a04fa0')}
      {!hasPair && analysis.peak_t_s != null && marker(analysis.peak_t_s, analysis.peak_g, '가장 큰 값', '#5d6b7a')}
      {hasPair && analysis.axle_dt_s != null && (analysis.front_t_s as number) >= x[0] && (analysis.rear_t_s as number) <= x[1] && (() => {
        const yy = sy(1) + (sy(Math.max(y[0], jumpG)) - sy(1)) * 0.55, x1 = sx(analysis.front_t_s as number), x2 = sx(analysis.rear_t_s as number);
        return <g fontFamily={FONT}><line x1={x1} x2={x2} y1={yy} y2={yy} stroke={INK} markerStart="url(#arr)" markerEnd="url(#arr)" /><text x={(x1 + x2) / 2} y={yy - 6} fontSize={11} textAnchor="middle" fill={INK} {...HALO}>{(analysis.axle_dt_s * 1000).toFixed(0)} ms 사이에 바퀴 간격만큼 이동{analysis.speed_mps ? ` → ${analysis.speed_mps.toFixed(3)} m/s` : ''}</text></g>;
      })()}
      <defs><marker id="arr" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill={INK} /></marker></defs>
    </svg>}
  </Figure>;
}

/* ------------------------------------------------------------------ 속도–Δa 산점도 + 회귀곡선 */
export function SpeedImpactChart({ runs, groups, fit, model: chosen, aLimit, title, name }: {
  runs: RunSummary[]; groups: ConditionGroup[]; fit?: PowerFit; model?: ModelResult | null; aLimit: number | null; title: string; name: string;
}) {
  const masses = Array.from(new Set(groups.map(g => g.total_mass_g).concat(runs.map(r => Math.round(r.condition.total_mass_g / 5) * 5)))).sort((a, b) => a - b);
  const color = (m: number) => MASS_COLORS[Math.max(0, masses.indexOf(m)) % MASS_COLORS.length];
  const pts = runs.filter(r => r.analysis.speed_mps && r.analysis.peak_delta_g != null);
  const xs = pts.map(r => r.analysis.speed_mps as number).concat(groups.map(g => g.mean_speed_mps || NaN));
  const ys = pts.map(r => r.analysis.peak_delta_g as number).concat(groups.map(g => g.kept_max_delta_g));
  if (aLimit) ys.push(aLimit - 1);
  const x = niceRange(xs, 0.1);
  const y = niceRange([...ys, 0], 0.08, true);
  const { sx, sy } = scales(x, y);
  const model = (m: number): ((v: number) => number) | null => {
    if (chosen) return (v: number) => modelPredict(chosen, v, m / 1000) ?? 0;
    const o = fit?.overall; const per = fit?.per_mass.find(p => p.mass_g === m);
    return o ? (v: number) => o.k0 * v ** o.n * (m / 1000) ** o.p : per ? (v: number) => per.k * v ** per.n : null;
  };
  // 허용 기준 Δa_lim = a_lim − 1 과 회귀곡선의 교점 = 그 질량의 v_max
  const vmaxOf = (m: number): number | null => {
    if (chosen) return aLimit && aLimit > 1 ? modelSolveSpeed(chosen, aLimit - 1, m / 1000) : null;
    const o = fit?.overall; const per = fit?.per_mass.find(p => p.mass_g === m);
    if (!aLimit || aLimit <= 1) return null;
    const k = o ? o.k0 * (m / 1000) ** o.p : per?.k, n = o ? o.n : per?.n;
    return k && n && n > 0 ? ((aLimit - 1) / k) ** (1 / n) : null;
  };
  const curve = (m: number) => {
    const f = model(m);
    if (!f) return null;
    const mv = groups.filter(g => g.total_mass_g === m && g.mean_speed_mps).map(g => g.mean_speed_mps as number);
    if (mv.length < 2) return null;
    const lo = Math.max(x[0], 1e-3, Math.min(...mv) * 0.9), hi = Math.min(x[1], Math.max(...mv) * 1.1);
    return Array.from({ length: 50 }, (_, i) => lo + (hi - lo) * i / 49).map((v, i) => `${i ? 'L' : 'M'}${sx(v).toFixed(1)},${sy(Math.min(y[1], f(v))).toFixed(1)}`).join('');
  };
  return <Figure name={name} caption={<>작은 점 = 한 번 넘은 기록, 큰 점과 세로 막대 = 같은 조건 평균(가장 큰 값·작은 값 1개씩 뺌)과 범위, 곡선 = {chosen ? `고른 관계식 ${chosen.formula}` : '관계식 Δa = k₀·vⁿ·mᵖ'}. 오른쪽 위로 갈수록 빠를수록 충격이 크다는 뜻입니다.{aLimit ? ` 빨간 점선 = 허용 기준(평소보다 +${(aLimit - 1).toFixed(2)} g), 빨간 테두리 점 = 기준을 넘은 회차, ◆ = 질량별 넘지 않는 최대 속도 v_max.` : ''}</>}>
    {ref => <svg ref={ref} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={title}>
      <Axes x={x} y={y} xLabel="방지턱 통과 속도 v (m/s)" yLabel="충격: 평소보다 더 받은 가속도 Δa (g)" title={title} />
      {aLimit && aLimit - 1 <= y[1] && <><line x1={M.l} x2={M.l + PW} y1={sy(aLimit - 1)} y2={sy(aLimit - 1)} stroke="#c33" strokeDasharray="6 4" /><text x={M.l + PW - 4} y={sy(aLimit - 1) - 5} fontSize={10.5} textAnchor="end" fill="#b22" fontFamily={FONT} {...HALO}>허용 기준 +{(aLimit - 1).toFixed(2)} g</text></>}
      {masses.map(m => { const d = curve(m); return d ? <path key={'c' + m} d={d} fill="none" stroke={color(m)} strokeWidth={1.6} opacity={0.85} /> : null; })}
      {pts.map(r => { const o = aLimit != null && (r.analysis.peak_delta_g as number) > aLimit - 1;
        return <circle key={r.id} cx={sx(r.analysis.speed_mps as number)} cy={sy(r.analysis.peak_delta_g as number)} r={o ? 3.6 : 2.8} fill={color(Math.round(r.condition.total_mass_g / 5) * 5)} opacity={o ? 0.9 : 0.35} stroke={o ? '#c62828' : 'none'} strokeWidth={1.4} />; })}
      {groups.filter(g => g.mean_speed_mps && g.mean_peak_delta_g != null).map(g => <g key={`${g.total_mass_g}-${g.output_mode}-${g.output_value}`}>
        <line x1={sx(g.mean_speed_mps as number)} x2={sx(g.mean_speed_mps as number)} y1={sy(g.kept_min_delta_g)} y2={sy(g.kept_max_delta_g)} stroke={color(g.total_mass_g)} strokeWidth={1.4} />
        {g.speed_min_mps != null && g.speed_max_mps != null && <line x1={sx(g.speed_min_mps)} x2={sx(g.speed_max_mps)} y1={sy(g.mean_peak_delta_g as number)} y2={sy(g.mean_peak_delta_g as number)} stroke={color(g.total_mass_g)} strokeWidth={1.1} opacity={0.7} />}
        <circle cx={sx(g.mean_speed_mps as number)} cy={sy(g.mean_peak_delta_g as number)} r={5} fill="#fff" stroke={color(g.total_mass_g)} strokeWidth={2} />
      </g>)}
      {aLimit && masses.map((m, i) => { const v = vmaxOf(m); const lo = Math.min(...groups.filter(g => g.mean_speed_mps).map(g => g.mean_speed_mps as number)); if (v == null || v < 0.5 * lo || v < x[0] || v > x[1] || aLimit - 1 > y[1]) return null; return <g key={'v' + m} fontFamily={FONT}><line x1={sx(v)} x2={sx(v)} y1={sy(aLimit - 1)} y2={M.t + PH} stroke={color(m)} strokeDasharray="2 3" /><path d={`M${sx(v)},${sy(aLimit - 1) - 7} l5,7 l-5,7 l-5,-7 z`} fill={color(m)} /><text x={sx(v)} y={M.t + PH - 6 - i * 13} fontSize={10} textAnchor="middle" fill={color(m)} fontWeight={600} {...HALO}>v_max {v.toFixed(3)}</text></g>; })}
      <g fontFamily={FONT} fontSize={10.5}>{masses.map((m, i) => <g key={'l' + m} transform={`translate(${M.l + 10} ${M.t + 14 + i * 16})`}><circle r={4.5} cx={0} cy={-3.5} fill="#fff" stroke={color(m)} strokeWidth={2} /><text x={10} y={0} fill={INK}>{m.toLocaleString()} g</text></g>)}</g>
    </svg>}
  </Figure>;
}

/* ------------------------------------------------------------------ Π–a_peak (무차원 정리) */
const SHAPES = ['circle', 'square', 'triangle', 'diamond'];
function Mark({ shape, x, y, color, hollow }: { shape: string; x: number; y: number; color: string; hollow?: boolean }) {
  const p = { fill: hollow ? '#fff' : color, stroke: color, strokeWidth: 1.6, opacity: 0.8 };
  if (shape === 'square') return <rect x={x - 4} y={y - 4} width={8} height={8} {...p} />;
  if (shape === 'triangle') return <path d={`M${x},${y - 5} L${x + 5},${y + 4} L${x - 5},${y + 4} Z`} {...p} />;
  if (shape === 'diamond') return <path d={`M${x},${y - 5} L${x + 5},${y} L${x},${y + 5} L${x - 5},${y} Z`} {...p} />;
  return <circle cx={x} cy={y} r={4.2} {...p} />;
}
export function PiChart({ runs, title, name }: { runs: RunSummary[]; title: string; name: string }) {
  const pts = runs.filter(r => r.analysis.pi != null && r.analysis.peak_g != null);
  const bumps = Array.from(new Set(pts.map(r => r.condition.bump_id))).sort();
  const piMax = Math.max(1.1, ...pts.map(r => r.analysis.pi as number));
  const [cap, setCap] = useState(true);
  // 점프 이후 Π가 매우 큰 시행이 있으면 경계(Π=1) 주변이 눌리므로 기본은 Π ≤ 2.5 구간을 확대합니다.
  const limit = cap && piMax > 2.5 ? 2.5 : piMax;
  const shown = pts.filter(r => (r.analysis.pi as number) <= limit);
  const x = niceRange([...shown.map(r => r.analysis.pi as number), 0, 1.1], 0.06, true);
  const y = niceRange([...shown.map(r => r.analysis.peak_g as number), 0, 2], 0.08, true);
  const { sx, sy } = scales(x, y);
  return <Figure name={name} tools={piMax > 2.5 ? <div className="segmented"><button type="button" className={cap ? 'selected' : ''} onClick={() => setCap(true)}>Π ≤ 2.5 확대</button><button type="button" className={!cap ? 'selected' : ''} onClick={() => setCap(false)}>전체 ({pts.length - shown.length || 0}개 더)</button></div> : undefined} caption="Π = v²/(g·R_eff), R_eff = 방지턱 곡률 반경 + 바퀴 반지름. 모양이 다른 방지턱의 결과가 하나의 곡선에 모이는지 확인합니다. 속이 빈 표시는 지면 이탈(점프)한 시행입니다.">
    {ref => <svg ref={ref} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={title}>
      <Axes x={x} y={y} xLabel="무차원수 Π = v² / (g · R_eff)" yLabel="최대 수직 가속도 a_peak (g)" title={title} />
      {x[1] >= 1 && <><line x1={sx(1)} x2={sx(1)} y1={M.t} y2={M.t + PH} stroke="#c33" strokeDasharray="6 4" /><text x={sx(1) + 5} y={M.t + PH - 8} fontSize={10.5} fill="#b22" fontFamily={FONT} {...HALO}>Π = 1 (점프 경계)</text></>}
      {shown.map(r => <Mark key={r.id} shape={SHAPES[bumps.indexOf(r.condition.bump_id) % SHAPES.length]} x={sx(r.analysis.pi as number)} y={sy(r.analysis.peak_g as number)} color={MASS_COLORS[bumps.indexOf(r.condition.bump_id) % MASS_COLORS.length]} hollow={!!r.analysis.jump} />)}
      <g fontFamily={FONT} fontSize={10.5}>{bumps.map((b, i) => <g key={b} transform={`translate(${M.l + PW - 80 * (bumps.length - i)} 18)`}><Mark shape={SHAPES[i % SHAPES.length]} x={0} y={-3.5} color={MASS_COLORS[i % MASS_COLORS.length]} /><text x={10} y={0} fill={INK}>방지턱 {b}</text></g>)}</g>
    </svg>}
  </Figure>;
}


/* ------------------------------------------------------------------ 범용 XY 그래프 (검증·점검·라인 추종) */
export type XYSeries = { name: string; color: string; points: { x: number; y: number; label?: string }[]; line?: boolean; dots?: boolean; dash?: string; width?: number };
type RefLine = { label: string; color: string };
export function XYChart({ series, title, name, xLabel, yLabel, identity, band, zeroLine, caption, xZero, yZero, hLines, vLines, legendMax = 10 }: {
  series: XYSeries[]; title: string; name: string; xLabel: string; yLabel: string;
  identity?: boolean; band?: number; zeroLine?: boolean; caption?: ReactNode; xZero?: boolean; yZero?: boolean;
  hLines?: (RefLine & { y: number })[]; vLines?: (RefLine & { x: number })[]; legendMax?: number;
}) {
  const xs = series.flatMap(s => s.points.map(p => p.x)), ys = series.flatMap(s => s.points.map(p => p.y)).concat((hLines ?? []).map(h => h.y));
  let x = niceRange(xs.length ? xs : [0, 1], 0.08, xZero), y = niceRange(ys.length ? ys : [0, 1], 0.08, yZero);
  if (identity) { const lo = Math.min(x[0], y[0]), hi = Math.max(x[1], y[1]); x = [lo, hi]; y = [lo, hi]; }
  const { sx, sy } = scales(x, y);
  return <Figure name={name} caption={caption}>
    {ref => <svg ref={ref} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={title}>
      <Axes x={x} y={y} xLabel={xLabel} yLabel={yLabel} title={title} />
      <defs><clipPath id={`clip-${name}`}><rect x={M.l} y={M.t} width={PW} height={PH} /></clipPath></defs>
      {identity && band ? <path clipPath={`url(#clip-${name})`} d={`M${sx(x[0])},${sy(x[0] * (1 + band))} L${sx(x[1])},${sy(x[1] * (1 + band))} L${sx(x[1])},${sy(x[1] * (1 - band))} L${sx(x[0])},${sy(x[0] * (1 - band))} Z`} fill="#3c9a5f" opacity={0.12} /> : null}
      {identity && <><line x1={sx(x[0])} y1={sy(x[0])} x2={sx(x[1])} y2={sy(x[1])} stroke="#3c9a5f" strokeDasharray="5 4" /><text x={sx(x[1]) - 4} y={sy(x[1]) + 14} textAnchor="end" fontSize={10} fill="#2d7a4a" fontFamily={FONT} {...HALO}>예측 = 실측{band ? ` (±${Math.round(band * 100)}% 띠)` : ''}</text></>}
      {zeroLine && y[0] < 0 && y[1] > 0 && <line x1={M.l} x2={M.l + PW} y1={sy(0)} y2={sy(0)} stroke="#9aa3ad" strokeDasharray="5 4" />}
      {(hLines ?? []).filter(h => h.y >= y[0] && h.y <= y[1]).map(h => <g key={'h' + h.label} fontFamily={FONT}><line x1={M.l} x2={M.l + PW} y1={sy(h.y)} y2={sy(h.y)} stroke={h.color} strokeDasharray="6 4" strokeWidth={1.3} /><text x={M.l + PW - 4} y={sy(h.y) - 5} fontSize={10.5} textAnchor="end" fill={h.color} fontWeight={600} {...HALO}>{h.label}</text></g>)}
      {(vLines ?? []).filter(v => v.x >= x[0] && v.x <= x[1]).map(v => <g key={'v' + v.label} fontFamily={FONT}><line x1={sx(v.x)} x2={sx(v.x)} y1={M.t} y2={M.t + PH} stroke={v.color} strokeDasharray="3 3" /><text x={sx(v.x) + 4} y={M.t + PH - 6} fontSize={10.5} fill={v.color} fontWeight={600} {...HALO}>{v.label}</text></g>)}
      {series.map((s, si) => <g key={s.name + si}>
        {s.line && s.points.length > 1 && <path d={s.points.map((p, i) => `${i ? 'L' : 'M'}${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`).join('')} fill="none" stroke={s.color} strokeWidth={s.width ?? 1.6} strokeDasharray={s.dash} opacity={s.width && s.width < 2 ? 0.8 : 1} />}
        {(s.dots ?? !s.line) && s.points.map((p, i) => <g key={i}><circle cx={sx(p.x)} cy={sy(p.y)} r={4} fill="#fff" stroke={s.color} strokeWidth={2} />{p.label && <text x={sx(p.x) + 6} y={sy(p.y) - 6} fontSize={9.5} fill={MUTED} fontFamily={FONT} {...HALO}>{p.label}</text>}</g>)}
      </g>)}
      <g fontFamily={FONT} fontSize={10.5}>{series.length > 1 && series.length <= legendMax && series.map((s, i) => <g key={'l' + s.name + i} transform={`translate(${M.l + 10} ${M.t + 14 + i * 15})`}><line x1={-5} x2={7} y1={-4} y2={-4} stroke={s.color} strokeWidth={2.5} strokeDasharray={s.dash} /><text x={12} y={0} fill={INK} {...HALO}>{s.name}</text></g>)}</g>
    </svg>}
  </Figure>;
}
