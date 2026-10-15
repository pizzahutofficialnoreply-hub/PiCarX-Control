// 실험 그래프: 보고서 그림으로 바로 쓸 수 있도록 흰 바탕·고정 색으로 그리고,
// 화면에 보이는 그대로 PNG/SVG로 저장합니다.
import { useMemo, useRef, useState, type ReactNode } from 'react';
import { Download, Image as ImageIcon } from 'lucide-react';
import type { ConditionGroup, PowerFit, RunAnalysis, RunSummary } from '../services/experiment';

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
export function RunTraceChart({ series, analysis, thresholdDelta, jumpG, title, name }: {
  series: { t_s: number[]; vertical_g: number[] }; analysis: RunAnalysis; thresholdDelta: number; jumpG: number; title: string; name: string;
}) {
  const hasPair = analysis.front_t_s != null && analysis.rear_t_s != null;
  const [zoom, setZoom] = useState<'bump' | 'all'>(hasPair ? 'bump' : 'all');
  const view = useMemo(() => {
    let lo = series.t_s[0] ?? 0, hi = series.t_s[series.t_s.length - 1] ?? 1;
    if (zoom === 'bump' && hasPair) { lo = (analysis.front_t_s as number) - 0.15; hi = (analysis.rear_t_s as number) + 0.3; }
    const idx = series.t_s.map((t, i) => i).filter(i => series.t_s[i] >= lo && series.t_s[i] <= hi);
    return { lo, hi, idx };
  }, [series, zoom, hasPair, analysis.front_t_s, analysis.rear_t_s]);
  const values = view.idx.map(i => series.vertical_g[i]);
  const y = niceRange([...values, 0, 1 + thresholdDelta, 2], 0.06, true);
  const x: [number, number] = [view.lo, view.hi];
  const { sx, sy } = scales(x, y);
  const path = view.idx.map((i, n) => `${n ? 'L' : 'M'}${sx(series.t_s[i]).toFixed(1)},${sy(Math.max(y[0], Math.min(y[1], series.vertical_g[i]))).toFixed(1)}`).join('');
  const marker = (t: number | null, g: number | null, label: string, color: string) => t == null || g == null || t < x[0] || t > x[1] ? null :
    <g><line x1={sx(t)} x2={sx(t)} y1={M.t} y2={M.t + PH} stroke={color} strokeDasharray="3 3" /><circle cx={sx(t)} cy={sy(Math.min(g, y[1]))} r={4.5} fill={color} /><text x={sx(t) + 6} y={sy(Math.min(g, y[1])) - 6} fontSize={11} fill={color} fontFamily={FONT} fontWeight={600} {...HALO}>{label} {g.toFixed(2)} g</text></g>;
  return <Figure name={name} tools={<div className="segmented"><button type="button" className={zoom === 'bump' ? 'selected' : ''} disabled={!hasPair} onClick={() => setZoom('bump')}>방지턱 구간</button><button type="button" className={zoom === 'all' ? 'selected' : ''} onClick={() => setZoom('all')}>전체 기록</button></div>}
    caption="수직 가속도(정지 상태 = 1 g). 앞·뒤 바퀴 피크 사이 시간 Δt와 축간거리로 통과 속도를 계산합니다.">
    {ref => <svg ref={ref} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={title}>
      <Axes x={x} y={y} xLabel="시간 t (s)" yLabel="수직 가속도 a_z (g)" title={title} />
      <line x1={M.l} x2={M.l + PW} y1={sy(1)} y2={sy(1)} stroke="#9aa3ad" strokeDasharray="5 4" />
      <text x={M.l + PW - 4} y={sy(1) - 5} fontSize={10} textAnchor="end" fill={MUTED} fontFamily={FONT} {...HALO}>정지 1 g</text>
      <line x1={M.l} x2={M.l + PW} y1={sy(1 + thresholdDelta)} y2={sy(1 + thresholdDelta)} stroke="#d0a12a" strokeDasharray="2 4" />
      <text x={M.l + 4} y={sy(1 + thresholdDelta) - 4} fontSize={10} fill="#a77f12" fontFamily={FONT} {...HALO}>피크 검출 기준 {(1 + thresholdDelta).toFixed(2)} g</text>
      {y[0] <= jumpG && <><line x1={M.l} x2={M.l + PW} y1={sy(jumpG)} y2={sy(jumpG)} stroke="#c44" strokeDasharray="2 4" /><text x={M.l + PW - 4} y={sy(jumpG) + 13} fontSize={10} textAnchor="end" fill="#b33" fontFamily={FONT} {...HALO}>지면 이탈 기준 {jumpG} g</text></>}
      <path d={path} fill="none" stroke="#2f6fb0" strokeWidth={1.4} strokeLinejoin="round" />
      {marker(analysis.front_t_s, analysis.front_g, '앞바퀴', '#d0782a')}
      {marker(analysis.rear_t_s, analysis.rear_g, '뒷바퀴', '#3c9a5f')}
      {analysis.landing_t_s != null && analysis.landing_t_s !== analysis.rear_t_s && marker(analysis.landing_t_s, analysis.landing_g ?? null, '착지', '#a04fa0')}
      {hasPair && analysis.axle_dt_s != null && (analysis.front_t_s as number) >= x[0] && (analysis.rear_t_s as number) <= x[1] && (() => {
        const yy = sy(1) + (sy(Math.max(y[0], jumpG)) - sy(1)) * 0.55, x1 = sx(analysis.front_t_s as number), x2 = sx(analysis.rear_t_s as number);
        return <g fontFamily={FONT}><line x1={x1} x2={x2} y1={yy} y2={yy} stroke={INK} markerStart="url(#arr)" markerEnd="url(#arr)" /><text x={(x1 + x2) / 2} y={yy - 6} fontSize={11} textAnchor="middle" fill={INK} {...HALO}>Δt = {(analysis.axle_dt_s * 1000).toFixed(1)} ms{analysis.speed_mps ? ` → v = ${analysis.speed_mps.toFixed(3)} m/s` : ''}</text></g>;
      })()}
      <defs><marker id="arr" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill={INK} /></marker></defs>
    </svg>}
  </Figure>;
}

/* ------------------------------------------------------------------ 속도–Δa 산점도 + 회귀곡선 */
export function SpeedImpactChart({ runs, groups, fit, aLimit, title, name }: {
  runs: RunSummary[]; groups: ConditionGroup[]; fit?: PowerFit; aLimit: number | null; title: string; name: string;
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
    const o = fit?.overall; const per = fit?.per_mass.find(p => p.mass_g === m);
    return o ? (v: number) => o.k0 * v ** o.n * (m / 1000) ** o.p : per ? (v: number) => per.k * v ** per.n : null;
  };
  // 허용 기준 Δa_lim = a_lim − 1 과 회귀곡선의 교점 = 그 질량의 v_max
  const vmaxOf = (m: number): number | null => {
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
  return <Figure name={name} caption={<>점: 개별 시행 · 큰 점과 막대: 조건 평균(상·하위 1회 제외)과 남은 값의 범위 · 선: 회귀식 Δa = k₀·vⁿ·mᵖ{aLimit ? ` · 빨간 점선: 허용 기준 ${aLimit} g, ◆: 질량별 v_max` : ''}</>}>
    {ref => <svg ref={ref} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={title}>
      <Axes x={x} y={y} xLabel="측정 속도 v (m/s)" yLabel="최대 수직 가속도 증가분 Δa (g)" title={title} />
      {aLimit && aLimit - 1 <= y[1] && <><line x1={M.l} x2={M.l + PW} y1={sy(aLimit - 1)} y2={sy(aLimit - 1)} stroke="#c33" strokeDasharray="6 4" /><text x={M.l + PW - 4} y={sy(aLimit - 1) - 5} fontSize={10.5} textAnchor="end" fill="#b22" fontFamily={FONT} {...HALO}>허용 기준 a = {aLimit} g</text></>}
      {masses.map(m => { const d = curve(m); return d ? <path key={'c' + m} d={d} fill="none" stroke={color(m)} strokeWidth={1.6} opacity={0.85} /> : null; })}
      {pts.map(r => <circle key={r.id} cx={sx(r.analysis.speed_mps as number)} cy={sy(r.analysis.peak_delta_g as number)} r={2.8} fill={color(Math.round(r.condition.total_mass_g / 5) * 5)} opacity={0.35} />)}
      {groups.filter(g => g.mean_speed_mps && g.mean_peak_delta_g != null).map(g => <g key={`${g.total_mass_g}-${g.output_mode}-${g.output_value}`}>
        <line x1={sx(g.mean_speed_mps as number)} x2={sx(g.mean_speed_mps as number)} y1={sy(g.kept_min_delta_g)} y2={sy(g.kept_max_delta_g)} stroke={color(g.total_mass_g)} strokeWidth={1.4} />
        {g.speed_min_mps != null && g.speed_max_mps != null && <line x1={sx(g.speed_min_mps)} x2={sx(g.speed_max_mps)} y1={sy(g.mean_peak_delta_g as number)} y2={sy(g.mean_peak_delta_g as number)} stroke={color(g.total_mass_g)} strokeWidth={1.1} opacity={0.7} />}
        <circle cx={sx(g.mean_speed_mps as number)} cy={sy(g.mean_peak_delta_g as number)} r={5} fill="#fff" stroke={color(g.total_mass_g)} strokeWidth={2} />
      </g>)}
      {aLimit && masses.map((m, i) => { const v = vmaxOf(m); if (v == null || v < x[0] || v > x[1] || aLimit - 1 > y[1]) return null; return <g key={'v' + m} fontFamily={FONT}><line x1={sx(v)} x2={sx(v)} y1={sy(aLimit - 1)} y2={M.t + PH} stroke={color(m)} strokeDasharray="2 3" /><path d={`M${sx(v)},${sy(aLimit - 1) - 7} l5,7 l-5,7 l-5,-7 z`} fill={color(m)} /><text x={sx(v)} y={M.t + PH - 6 - i * 13} fontSize={10} textAnchor="middle" fill={color(m)} fontWeight={600} {...HALO}>v_max {v.toFixed(3)}</text></g>; })}
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
