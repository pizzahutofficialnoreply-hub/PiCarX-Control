// 실험 그래프: 보고서 그림으로 바로 쓸 수 있도록 흰 바탕·고정 색으로 그리고,
// 화면에 보이는 그대로 PNG/SVG로 저장합니다.
import { useMemo, useRef, useState, type ReactNode } from 'react';
import { Download, Image as ImageIcon } from 'lucide-react';
import { EVENT_INFO, modelPredict, modelSolveSpeed, type ConditionGroup, type ModelResult, type PowerFit, type RunAnalysis, type RunSummary } from '../services/experiment';
import { usePersistentState } from '../services/persist';

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
const tickFmt = (v: number) => Math.abs(v) >= 100 ? v.toFixed(1) : Math.abs(v) >= 1 ? v.toFixed(3) : v.toPrecision(3);
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


/* ------------------------------------------------------------------ 마우스 툴팁 */
// 그래프 위에 마우스를 올리면 가장 가까운 값(또는 같은 시각의 각 선 값)을 보여 줍니다.
// 그림 저장(PNG/SVG)에는 마우스가 버튼 위에 있으므로 툴팁이 들어가지 않습니다.
export type Tip = { px: number; py?: number; title: string; rows: { color?: string; text: string }[]; dots?: { px: number; py: number; color: string }[] };
function HoverLayer({ getTip, onClick, cursor }: { getTip: (px: number, py: number) => Tip | null; onClick?: (px: number, py: number) => void; cursor?: string }) {
  const [tip, setTip] = useState<Tip | null>(null);
  const toLocal = (e: React.MouseEvent<SVGRectElement>) => {
    const svg = e.currentTarget.ownerSVGElement; const m = svg?.getScreenCTM();
    if (!svg || !m) return null;
    const pt = svg.createSVGPoint(); pt.x = e.clientX; pt.y = e.clientY;
    const p = pt.matrixTransform(m.inverse());
    return { x: p.x, y: p.y };
  };
  const rows = tip ? tip.rows.slice(0, 12) : [];
  const more = tip ? tip.rows.length - rows.length : 0;
  const lineH = 14, boxW = tip ? Math.min(300, Math.max(110, ...[tip.title, ...rows.map(r => r.text)].map(t => t.length * 6.6 + (t === tip.title ? 0 : 14))) + 14) : 0;
  const boxH = (rows.length + (more > 0 ? 1 : 0)) * lineH + 26;
  const bx = tip ? (tip.px + 14 + boxW > W - 4 ? tip.px - 14 - boxW : tip.px + 14) : 0;
  const by = tip ? Math.max(M.t, Math.min(M.t + PH - boxH, (tip.py ?? M.t + 10) - boxH / 2)) : 0;
  return <g fontFamily={FONT} className="exp-hover">
    {tip && <g pointerEvents="none">
      <line x1={tip.px} x2={tip.px} y1={M.t} y2={M.t + PH} stroke="#6a737d" strokeWidth={0.8} strokeDasharray="2 2" />
      {(tip.dots ?? []).map((d, i) => <circle key={i} cx={d.px} cy={d.py} r={4} fill={d.color} stroke="#fff" strokeWidth={1.5} />)}
      <rect x={bx} y={by} width={boxW} height={boxH} rx={6} fill="#ffffff" stroke="#c9ced4" opacity={0.96} />
      <text x={bx + 8} y={by + 16} fontSize={11} fontWeight={700} fill={INK}>{tip.title}</text>
      {rows.map((r, i) => <g key={i} transform={`translate(${bx + 8} ${by + 16 + (i + 1) * lineH})`}>{r.color && <rect x={0} y={-8} width={8} height={8} rx={2} fill={r.color} />}<text x={r.color ? 13 : 0} y={0} fontSize={10.5} fill={INK}>{r.text}</text></g>)}
      {more > 0 && <text x={bx + 8} y={by + 16 + (rows.length + 1) * lineH} fontSize={10} fill={MUTED}>외 {more}개</text>}
    </g>}
    <rect x={M.l} y={M.t} width={PW} height={PH} fill="transparent" style={{ cursor: cursor ?? 'crosshair' }}
      onMouseMove={e => { const p = toLocal(e); setTip(p ? getTip(p.x, p.y) : null); }} onMouseLeave={() => setTip(null)}
      onClick={e => { const p = toLocal(e); if (p && onClick) onClick(p.x, p.y); }} />
  </g>;
}
const nearestIdx = (arr: number[], v: number) => { let lo = 0, hi = arr.length - 1; if (hi < 0) return -1; while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (arr[mid] < v) lo = mid; else hi = mid; } return Math.abs(arr[lo] - v) <= Math.abs(arr[hi] - v) ? lo : hi; };
function nearestPoint<T>(items: T[], px: number, py: number, pos: (t: T) => [number, number], maxDist = 28): T | null {
  let best: T | null = null, bd = maxDist * maxDist;
  for (const it of items) { const [x, y] = pos(it); const d = (x - px) ** 2 + (y - py) ** 2; if (d < bd) { bd = d; best = it; } }
  return best;
}

/* ------------------------------------------------------------------ 시행 원자료 */
export const bumpWindowAfter = (rearRel: number | null | undefined) => Math.max(0.45, (rearRel ?? 0.15) + 0.3);
const shiftT = (t: number | null | undefined, t0: number) => t == null ? (t ?? null) as null : t - t0;

/** 시행 그래프. ``timeBase='front'``면 앞바퀴가 턱에 닿은 순간을 0초로 그려 겹쳐 보기와 시간축이 똑같습니다. */
export function RunTraceChart(props: {
  series: { t_s: number[]; vertical_g: number[]; filtered_g?: number[] }; analysis: RunAnalysis; thresholdDelta: number; jumpG: number; title: string; name: string;
  showCandidates?: boolean; aLimit?: number | null;
  pickMode?: 'front' | 'rear' | null; onPick?: (t: number) => void; picked?: { front?: number; rear?: number };
  zoom?: 'bump' | 'all'; onZoom?: (z: 'bump' | 'all') => void; timeBase?: 'front' | 'record'; extraMarks?: { t: number; label: string; color: string }[];
}) {
  const { series, analysis, timeBase = 'record' } = props;
  const t0 = timeBase === 'front' && analysis.front_t_s != null ? analysis.front_t_s : 0;
  const shifted = useMemo(() => !t0 ? { series, analysis } : {
    series: { ...series, t_s: series.t_s.map(t => t - t0) },
    analysis: {
      ...analysis,
      front_t_s: shiftT(analysis.front_t_s, t0), rear_t_s: shiftT(analysis.rear_t_s, t0), peak_t_s: shiftT(analysis.peak_t_s, t0), landing_t_s: shiftT(analysis.landing_t_s, t0),
      candidates: (analysis.candidates ?? []).map(c => ({ ...c, t_s: c.t_s - t0 })),
      events: (analysis.events ?? []).map(e => ({ ...e, t_s: e.t_s - t0, peak_t_s: e.peak_t_s - t0 })),
      auto_pair: analysis.auto_pair ? { ...analysis.auto_pair, front_t_s: analysis.auto_pair.front_t_s - t0, rear_t_s: analysis.auto_pair.rear_t_s - t0 } : analysis.auto_pair,
    } as RunAnalysis,
  }, [series, analysis, t0]);
  return <RunTraceChartInner {...props} series={shifted.series} analysis={shifted.analysis} relative={!!t0}
    picked={props.picked ? { front: props.picked.front != null ? props.picked.front - t0 : undefined, rear: props.picked.rear != null ? props.picked.rear - t0 : undefined } : undefined}
    onPick={props.onPick ? (t => props.onPick?.(t + t0)) : undefined}
    extraMarks={(props.extraMarks ?? []).map(m => ({ ...m, t: m.t - t0 }))} />;
}

function RunTraceChartInner({ series, analysis, thresholdDelta, jumpG, title, name, showCandidates = false, aLimit = null, pickMode = null, onPick, picked, zoom: zoomProp, onZoom, relative = false, extraMarks = [] }: {
  series: { t_s: number[]; vertical_g: number[]; filtered_g?: number[] }; analysis: RunAnalysis; thresholdDelta: number; jumpG: number; title: string; name: string;
  showCandidates?: boolean; aLimit?: number | null;
  pickMode?: 'front' | 'rear' | null; onPick?: (t: number) => void; picked?: { front?: number; rear?: number };
  zoom?: 'bump' | 'all'; onZoom?: (z: 'bump' | 'all') => void; relative?: boolean; extraMarks?: { t: number; label: string; color: string }[];
}) {
  const hasPair = analysis.front_t_s != null && analysis.rear_t_s != null;
  const [zoomLocal, setZoomLocal] = useState<'bump' | 'all'>(hasPair ? 'bump' : 'all');
  const zoomWanted = zoomProp ?? zoomLocal;
  const zoom = zoomWanted === 'bump' && !hasPair ? 'all' : zoomWanted;
  const setZoom = (z: 'bump' | 'all') => { setZoomLocal(z); onZoom?.(z); };
  const view = useMemo(() => {
    let lo = series.t_s[0] ?? 0, hi = series.t_s[series.t_s.length - 1] ?? 1;
    if (zoom === 'bump' && hasPair) {
      const f = analysis.front_t_s as number;
      lo = f - 0.15; hi = f + bumpWindowAfter((analysis.rear_t_s as number) - f);
    }
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
      <Axes x={x} y={y} xLabel={relative ? '앞바퀴 기준 시간 (초, 0 = 앞바퀴가 턱에 닿은 순간)' : '시간 (초, 기록 시작 기준)'} yLabel="위아래 가속도 (g)" title={title} />
      {extraMarks.filter(m => m.t >= x[0] && m.t <= x[1]).map(m => <g key={m.label} fontFamily={FONT}><line x1={sx(m.t)} x2={sx(m.t)} y1={M.t} y2={M.t + PH} stroke={m.color} strokeWidth={1.5} strokeDasharray="6 3" /><text x={sx(m.t) + 4} y={M.t + 26} fontSize={10.5} fill={m.color} fontWeight={700} {...HALO}>{m.label}</text></g>)}
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
      {(() => { const ev = analysis.events ?? []; const f = ev.find(e => e.kind === 'FU'), fd = ev.find(e => e.kind === 'FD'), r = ev.find(e => e.kind === 'RU'), rd = ev.find(e => e.kind === 'RD');
        const band = (a?: { t_s: number }, b?: { t_s: number }, color?: string, label?: string) => a && b && b.t_s > x[0] && a.t_s < x[1] ? <g fontFamily={FONT}><rect x={sx(Math.max(x[0], a.t_s))} y={M.t} width={Math.max(0, sx(Math.min(x[1], b.t_s)) - sx(Math.max(x[0], a.t_s)))} height={PH} fill={color} opacity={0.08} /><text x={sx(Math.max(x[0], a.t_s)) + 3} y={M.t + PH - 20} fontSize={10} fill={color} {...HALO}>{label}</text></g> : null;
        return <>{band(f, fd, '#d0782a', '앞바퀴가 턱 위')}{band(r, rd, '#3c9a5f', '뒷바퀴가 턱 위')}</>; })()}
      {(analysis.events ?? []).length ? (analysis.events ?? []).map(e => <g key={e.kind}>{marker(e.peak_t_s, e.g, EVENT_INFO[e.kind].label, EVENT_INFO[e.kind].color)}</g>) : <>
      {marker(analysis.front_t_s, analysis.front_g, '앞바퀴', '#d0782a')}
      {marker(analysis.rear_t_s, analysis.rear_g, '뒷바퀴', '#3c9a5f')}</>}
      {analysis.landing_t_s != null && analysis.landing_t_s !== analysis.rear_t_s && marker(analysis.landing_t_s, analysis.landing_g ?? null, '착지', '#a04fa0')}
      {!hasPair && analysis.peak_t_s != null && marker(analysis.peak_t_s, analysis.peak_g, '가장 큰 값', '#5d6b7a')}
      {hasPair && analysis.axle_dt_s != null && (analysis.front_t_s as number) >= x[0] && (analysis.rear_t_s as number) <= x[1] && (() => {
        const yy = sy(1) + (sy(Math.max(y[0], jumpG)) - sy(1)) * 0.55, x1 = sx(analysis.front_t_s as number), x2 = sx(analysis.rear_t_s as number);
        return <g fontFamily={FONT}><line x1={x1} x2={x2} y1={yy} y2={yy} stroke={INK} markerStart="url(#arr)" markerEnd="url(#arr)" /><text x={(x1 + x2) / 2} y={yy - 6} fontSize={11} textAnchor="middle" fill={INK} {...HALO}>{(analysis.axle_dt_s * 1000).toFixed(0)} ms 사이에 바퀴 간격만큼 이동{analysis.speed_mps ? ` → ${analysis.speed_mps.toFixed(3)} m/s` : ''}</text></g>;
      })()}
      {analysis.pair_source === 'manual' && analysis.auto_pair && <g opacity={0.75}>{[['자동 앞', analysis.auto_pair.front_t_s, analysis.auto_pair.front_g], ['자동 뒤', analysis.auto_pair.rear_t_s, analysis.auto_pair.rear_g]].map(([l, t, g]) => (t as number) < x[0] || (t as number) > x[1] ? null :
        <g key={l as string} fontFamily={FONT}><circle cx={sx(t as number)} cy={sy(Math.min(g as number, y[1]))} r={5} fill="none" stroke="#6a737d" strokeWidth={1.5} strokeDasharray="2 2" /><text x={sx(t as number) - 6} y={sy(Math.min(g as number, y[1])) + 16} fontSize={10} textAnchor="end" fill={MUTED} {...HALO}>{l as string}</text></g>)}</g>}
      {picked && ([['front', '#d0782a', '고른 앞'], ['rear', '#3c9a5f', '고른 뒤']] as const).map(([k, c, l]) => { const t = picked[k]; if (t == null || t < x[0] || t > x[1]) return null;
        return <g key={k} fontFamily={FONT}><line x1={sx(t)} x2={sx(t)} y1={M.t} y2={M.t + PH} stroke={c} strokeWidth={2} /><rect x={sx(t) - 24} y={M.t + PH - 18} width={48} height={15} rx={4} fill={c} /><text x={sx(t)} y={M.t + PH - 7} fontSize={10} textAnchor="middle" fill="#fff" fontWeight={700}>{l}</text></g>; })}
      <HoverLayer cursor={pickMode ? 'pointer' : undefined}
        onClick={pickMode && onPick ? (px) => { const t = x[0] + (px - M.l) / PW * (x[1] - x[0]); const i = nearestIdx(series.t_s, t); if (i >= 0) onPick(series.t_s[i]); } : undefined}
        getTip={(px) => { const t = x[0] + (px - M.l) / PW * (x[1] - x[0]); const i = nearestIdx(series.t_s, t); if (i < 0) return null;
          const tt = series.t_s[i], g = main[i], rows: Tip['rows'] = [{ color: '#2f6fb0', text: `${filtered ? '거른 값' : '가속도'} ${g.toFixed(3)} g (Δa ${(g - 1 >= 0 ? '+' : '')}${(g - 1).toFixed(3)} g)` }];
          if (filtered) rows.push({ color: '#9aa3ad', text: `원래 기록 ${series.vertical_g[i].toFixed(3)} g` });
          if (analysis.front_t_s != null && !relative) rows.push({ text: `앞바퀴로부터 ${((tt - analysis.front_t_s) * 1000).toFixed(0)} ms` });
          const c = (analysis.candidates ?? []).findIndex(c => Math.abs(c.t_s - tt) < 0.006); if (c >= 0) rows.push({ text: `후보 ${c + 1} 근처${(analysis.candidates ?? [])[c].landing ? ' (착지)' : ''}` });
          if (pickMode) rows.push({ text: `클릭 → 여기를 ${pickMode === 'front' ? '앞바퀴' : '뒷바퀴'}로` });
          return { px: sx(tt), py: sy(Math.max(y[0], Math.min(y[1], g))), title: `${tt.toFixed(3)} 초`, rows, dots: [{ px: sx(tt), py: sy(Math.max(y[0], Math.min(y[1], g))), color: '#2f6fb0' }] }; }} />
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
      <HoverLayer getTip={(px, py) => {
        type It = { kind: 'run'; r: RunSummary } | { kind: 'grp'; g: ConditionGroup };
        const items: It[] = [...groups.filter(g => g.mean_speed_mps && g.mean_peak_delta_g != null).map(g => ({ kind: 'grp' as const, g })), ...pts.map(r => ({ kind: 'run' as const, r }))];
        const hit = nearestPoint(items, px, py, it => it.kind === 'grp' ? [sx(it.g.mean_speed_mps as number), sy(it.g.mean_peak_delta_g as number)] : [sx(it.r.analysis.speed_mps as number), sy(it.r.analysis.peak_delta_g as number)]);
        if (!hit) return null;
        if (hit.kind === 'grp') { const g = hit.g; const X = sx(g.mean_speed_mps as number), Y = sy(g.mean_peak_delta_g as number);
          return { px: X, py: Y, title: `평균 · ${g.total_mass_g.toLocaleString()} g · 출력 ${g.output_value}`, rows: [{ color: color(g.total_mass_g), text: `속도 ${(g.mean_speed_mps as number).toFixed(3)} m/s` }, { text: `Δa 평균 +${(g.mean_peak_delta_g as number).toFixed(3)} g (범위 ${g.kept_min_delta_g.toFixed(2)}~${g.kept_max_delta_g.toFixed(2)})` }, { text: `${g.run_ids.length}회 측정` }], dots: [{ px: X, py: Y, color: color(g.total_mass_g) }] }; }
        const r = hit.r, X = sx(r.analysis.speed_mps as number), Y = sy(r.analysis.peak_delta_g as number), m = Math.round(r.condition.total_mass_g / 5) * 5;
        const pred = model(m)?.(r.analysis.speed_mps as number);
        return { px: X, py: Y, title: `${r.condition.trial}회차 · ${r.condition.total_mass_g.toLocaleString()} g · 출력 ${r.condition.output_value}`, rows: [{ color: color(m), text: `속도 ${(r.analysis.speed_mps as number).toFixed(3)} m/s` }, { text: `Δa +${(r.analysis.peak_delta_g as number).toFixed(3)} g (최대 ${r.analysis.peak_g?.toFixed(2)} g)` }, ...(pred != null ? [{ text: `식 예측 +${pred.toFixed(3)} g` }] : [])], dots: [{ px: X, py: Y, color: color(m) }] };
      }} />
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
  const [cap, setCap] = usePersistentState('pi-cap', true);
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
      <HoverLayer getTip={(px, py) => { const r = nearestPoint(shown, px, py, r => [sx(r.analysis.pi as number), sy(r.analysis.peak_g as number)]); if (!r) return null;
        const X = sx(r.analysis.pi as number), Y = sy(r.analysis.peak_g as number), c = MASS_COLORS[bumps.indexOf(r.condition.bump_id) % MASS_COLORS.length];
        return { px: X, py: Y, title: `방지턱 ${r.condition.bump_id} · ${r.condition.trial}회차`, rows: [{ color: c, text: `Π = ${(r.analysis.pi as number).toFixed(3)}` }, { text: `최대 ${(r.analysis.peak_g as number).toFixed(3)} g` }, { text: `${r.condition.total_mass_g.toLocaleString()} g · 출력 ${r.condition.output_value} · ${r.analysis.speed_mps?.toFixed(3) ?? '—'} m/s` }, ...(r.analysis.jump ? [{ text: '점프함' }] : [])], dots: [{ px: X, py: Y, color: c }] }; }} />
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
      {(vLines ?? []).filter(v => v.x >= x[0] && v.x <= x[1]).map((v, vi) => <g key={'v' + v.label} fontFamily={FONT}><line x1={sx(v.x)} x2={sx(v.x)} y1={M.t} y2={M.t + PH} stroke={v.color} strokeDasharray="3 3" /><text x={sx(v.x) + 4} y={M.t + PH - 6 - vi * 14} fontSize={10.5} fill={v.color} fontWeight={600} {...HALO}>{v.label}</text></g>)}
      {series.map((s, si) => <g key={s.name + si}>
        {s.line && s.points.length > 1 && <path d={s.points.map((p, i) => `${i ? 'L' : 'M'}${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`).join('')} fill="none" stroke={s.color} strokeWidth={s.width ?? 1.6} strokeDasharray={s.dash} opacity={s.width && s.width < 2 ? 0.8 : 1} />}
        {(s.dots ?? !s.line) && s.points.map((p, i) => <g key={i}><circle cx={sx(p.x)} cy={sy(p.y)} r={4} fill="#fff" stroke={s.color} strokeWidth={2} />{p.label && <text x={sx(p.x) + 6} y={sy(p.y) - 6} fontSize={9.5} fill={MUTED} fontFamily={FONT} {...HALO}>{p.label}</text>}</g>)}
      </g>)}
      <HoverLayer getTip={(px, py) => {
        const xv = x[0] + (px - M.l) / PW * (x[1] - x[0]);
        const dense = series.some(s => s.line && s.points.length > 30);
        if (dense) {
          const rows: Tip['rows'] = [], dots: NonNullable<Tip['dots']> = [];
          for (const s of series) { if (!s.points.length) continue; const i = nearestIdx(s.points.map(p => p.x), xv); const p = s.points[i]; if (!p || Math.abs(sx(p.x) - px) > 12) continue;
            rows.push({ color: s.color, text: `${s.name}: ${tickFmt(p.y)}` }); dots.push({ px: sx(p.x), py: sy(Math.max(y[0], Math.min(y[1], p.y))), color: s.color }); }
          if (!rows.length) return null;
          rows.sort((a, b) => Number(b.text.split(': ').pop()) - Number(a.text.split(': ').pop()));
          return { px, py, title: `${xLabel.replace(/\s*\(.*$/, '')} ${tickFmt(xv)}`, rows, dots };
        }
        const all = series.flatMap(s => s.points.map(p => ({ s, p })));
        const hit = nearestPoint(all, px, py, it => [sx(it.p.x), sy(it.p.y)]);
        if (!hit) return null;
        const X = sx(hit.p.x), Y = sy(hit.p.y);
        return { px: X, py: Y, title: hit.p.label ?? hit.s.name, rows: [...(hit.p.label ? [{ color: hit.s.color, text: hit.s.name }] : []), { text: `${xLabel}: ${tickFmt(hit.p.x)}` }, { text: `${yLabel}: ${tickFmt(hit.p.y)}` }], dots: [{ px: X, py: Y, color: hit.s.color }] };
      }} />
      <g fontFamily={FONT} fontSize={10.5}>{series.length > 1 && series.length <= legendMax && series.map((s, i) => <g key={'l' + s.name + i} transform={`translate(${M.l + 10} ${M.t + 14 + i * 15})`}><line x1={-5} x2={7} y1={-4} y2={-4} stroke={s.color} strokeWidth={2.5} strokeDasharray={s.dash} /><text x={12} y={0} fill={INK} {...HALO}>{s.name}</text></g>)}</g>
    </svg>}
  </Figure>;
}
