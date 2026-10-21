// 카메라 방지턱 감지 보정: 화면을 보며 감지 영역(초록 점선)과 방지턱으로 본 곳(초록 네모)을 확인하고,
// 자동으로 못 잡으면 화면에서 방지턱을 끌어서(또는 한 번 눌러서) 골라 그 색을 학습시킵니다.
import { useEffect, useRef, useState } from 'react';
import { Camera, RefreshCw } from 'lucide-react';
import NumInput from '../components/NumInput';
import type { VisionSettings } from '../services/experiment';
import type { ExperimentContext } from './context';

type Snap = { image: string | null; score: [number, number] | null; threshold: number; detected: boolean; box: number[] | null; mode: 'taught' | 'floor' | null;
  calibrated: boolean; roi: { top: number; bottom: number; center_gap: number }; vision?: VisionSettings; note?: string | null };

export default function VisionCal({ ctx }: { ctx: ExperimentContext }) {
  const vs = ctx.settings.vision as VisionSettings | undefined;
  const [snap, setSnap] = useState<Snap | null>(null);
  const [live, setLive] = useState(false);
  const [loading, setLoading] = useState(false);
  const [drag, setDrag] = useState<{ x0: number; y0: number; x1: number; y1: number } | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const busyRef = useRef(false);

  async function load() {
    if (busyRef.current) return;
    busyRef.current = true; setLoading(true);
    try { setSnap(await ctx.request('/api/experiment/vision/snapshot', { timeoutMs: 10000 }) as Snap); } catch (e) { setLive(false); ctx.fail(e); } finally { busyRef.current = false; setLoading(false); }
  }
  useEffect(() => {
    if (!live) return;
    void load();
    const id = window.setInterval(() => void load(), 800);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live]);

  async function patch(p: Partial<VisionSettings>) { await ctx.patchSettings({ vision: { ...(vs as VisionSettings), ...p } }); if (!live) void load(); }
  async function teach(r: { x0: number; y0: number; x1: number; y1: number }) {
    try {
      const s = await ctx.request('/api/experiment/vision/teach', { method: 'POST', body: JSON.stringify(r), timeoutMs: 10000 }) as Snap;
      setSnap(s); await ctx.loadRuns();
      ctx.setMessage(s.note ? `방지턱 색을 배웠습니다. 주의: ${s.note}` : `방지턱 색을 배웠습니다. 감지 기준을 ${(s.threshold * 100).toFixed(0)}%로 맞췄습니다.`);
    } catch (e) { ctx.fail(e); }
  }
  async function floorCal() {
    try { await ctx.request('/api/experiment/vision/calibrate', { method: 'POST', body: JSON.stringify({}), timeoutMs: 10000 }); await ctx.loadRuns(); await load(); ctx.setMessage('바닥 색을 저장했습니다(방지턱이 화면에 없는 곳에서 해야 합니다).'); } catch (e) { ctx.fail(e); }
  }
  async function forget() { try { await ctx.request('/api/experiment/vision/forget', { method: 'POST' }); await ctx.loadRuns(); await load(); } catch (e) { ctx.fail(e); } }

  function rel(e: React.PointerEvent) {
    const r = boxRef.current!.getBoundingClientRect();
    return { x: Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)), y: Math.max(0, Math.min(1, (e.clientY - r.top) / r.height)) };
  }
  function down(e: React.PointerEvent) { const p = rel(e); (e.target as Element).setPointerCapture?.(e.pointerId); setDrag({ x0: p.x, y0: p.y, x1: p.x, y1: p.y }); }
  function move(e: React.PointerEvent) { if (!drag) return; const p = rel(e); setDrag({ ...drag, x1: p.x, y1: p.y }); }
  function up() {
    if (!drag) return;
    const small = Math.abs(drag.x1 - drag.x0) < 0.02 && Math.abs(drag.y1 - drag.y0) < 0.02;
    // 한 번 누르면 그 점 둘레 작은 네모, 끌면 끈 네모
    const r = small ? { x0: drag.x0 - 0.03, y0: drag.y0 - 0.03, x1: drag.x0 + 0.03, y1: drag.y0 + 0.03 }
      : { x0: Math.min(drag.x0, drag.x1), y0: Math.min(drag.y0, drag.y1), x1: Math.max(drag.x0, drag.x1), y1: Math.max(drag.y0, drag.y1) };
    setDrag(null);
    void teach({ x0: Math.max(0, r.x0), y0: Math.max(0, r.y0), x1: Math.min(1, r.x1), y1: Math.min(1, r.y1) });
  }

  const roi = snap?.roi ?? { top: vs?.roi_top ?? 0.55, bottom: vs?.roi_bottom ?? 0.9, center_gap: vs?.center_gap ?? 0.2 };
  const b = snap?.box;
  const col = snap?.detected ? '#22c55e' : '#facc15';
  const sel = drag ? { x: Math.min(drag.x0, drag.x1), y: Math.min(drag.y0, drag.y1), w: Math.abs(drag.x1 - drag.x0), h: Math.abs(drag.y1 - drag.y0) } : null;
  const mode = vs?.bump_color ? 'taught' : vs?.floor_mean ? 'floor' : null;

  return <div className="exp-inverse">
    <b><Camera size={15} /> 카메라 보정 (방지턱 인식 확인 · 직접 골라 학습)</b>
    <p className="exp-hint">방지턱 앞 20~40 cm에 차를 두고 화면을 보세요. <b>초록 점선</b> = 감지 영역, <b style={{ color: '#16a34a' }}>초록 네모</b> = 방지턱으로 인식(기준 넘음), <b style={{ color: '#ca8a04' }}>노란 네모</b> = 보이긴 하나 기준 미달.
      네모가 엉뚱한 곳에 있거나 안 나오면, 화면에서 <b>방지턱을 손가락/마우스로 끌어 네모로 고르세요</b>(한 번 누르면 그 점 둘레). 그 색을 방지턱으로 배우고 감지 기준도 자동으로 맞춥니다.</p>
    <div className="button-row">
      <button className="secondary" disabled={!ctx.serverAvailable || loading || ctx.busy} onClick={() => void load()}><RefreshCw size={14} /> 화면 보기</button>
      <label className="exp-check"><input type="checkbox" checked={live} disabled={!ctx.serverAvailable || ctx.busy} onChange={e => setLive(e.target.checked)} /> 계속 보기(약 1초마다)</label>
      <button className="secondary" disabled={!ctx.serverAvailable || ctx.busy} onClick={() => void floorCal()}>바닥 보정 (방지턱 없는 곳에서)</button>
      {mode === 'taught' && <button className="secondary" onClick={() => void forget()}>학습한 색 지우기</button>}
    </div>
    {snap?.image ? <div ref={boxRef} style={{ position: 'relative', width: '100%', maxWidth: 640, touchAction: 'none', userSelect: 'none', cursor: 'crosshair' }}
        onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={() => setDrag(null)}>
      <img src={snap.image} alt="카메라 화면" draggable={false} style={{ width: '100%', display: 'block', borderRadius: 6 }} />
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'none' }}>
        <rect x={0} y={roi.top * 100} width={100} height={(roi.bottom - roi.top) * 100} fill="none" stroke="#22c55e" strokeWidth={0.5} strokeDasharray="2 1.5" vectorEffect="non-scaling-stroke" />
        {roi.center_gap > 0 && <rect x={50 - roi.center_gap * 50} y={roi.top * 100} width={roi.center_gap * 100} height={(roi.bottom - roi.top) * 100} fill="rgba(0,0,0,0.25)" />}
        {b && <rect x={b[0] * 100} y={b[1] * 100} width={(b[2] - b[0]) * 100} height={(b[3] - b[1]) * 100} fill="none" stroke={col} strokeWidth={2.5} vectorEffect="non-scaling-stroke" />}
        {sel && <rect x={sel.x * 100} y={sel.y * 100} width={sel.w * 100} height={sel.h * 100} fill="rgba(59,130,246,0.2)" stroke="#3b82f6" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />}
      </svg>
      {b && <span style={{ position: 'absolute', left: `${b[0] * 100}%`, top: `calc(${b[1] * 100}% - 20px)`, background: col, color: '#000', fontSize: 12, padding: '1px 5px', borderRadius: 3 }}>{snap.detected ? '방지턱' : '방지턱?'}</span>}
    </div> : <div className="empty-chart">{loading ? '화면 가져오는 중…' : "'화면 보기'를 누르세요."}</div>}
    <p className="exp-hint">방식: <b>{mode === 'taught' ? '학습한 방지턱 색' : mode === 'floor' ? '바닥과 다른 색' : '보정 안 됨'}</b>
      {snap?.score ? ` · 점수 왼쪽 ${(snap.score[0] * 100).toFixed(0)}% / 오른쪽 ${(snap.score[1] * 100).toFixed(0)}% (기준 ${(snap.threshold * 100).toFixed(0)}%${mode === 'taught' ? ', 한쪽만 넘어도 됨' : ', 양쪽 다 넘어야 함'}) → ${snap.detected ? '방지턱 있음' : '없음'}` : ''}</p>
    {snap?.note && <p className="diagnostic-error">{snap.note}</p>}
    <div className="experiment-fields">
      <label>카메라 틸트 (°, 아래 −, 빈칸 = 그대로)<NumInput min={-45} max={30} step={5} value={vs?.tilt_deg ?? ''} placeholder="그대로" onChange={e => void patch({ tilt_deg: e.target.value === '' ? null : Math.max(-45, Math.min(30, Number(e.target.value))) })} /></label>
      <label>감지 영역 위 (화면 높이 비율)<NumInput min={0} max={0.95} step={0.05} value={vs?.roi_top ?? 0.55} onChange={e => void patch({ roi_top: Number(e.target.value) })} /></label>
      <label>감지 영역 아래<NumInput min={0.05} max={1} step={0.05} value={vs?.roi_bottom ?? 0.9} onChange={e => void patch({ roi_bottom: Number(e.target.value) })} /></label>
      <label>가운데 뺄 폭<NumInput min={0} max={0.8} step={0.05} value={vs?.center_gap ?? 0.2} onChange={e => void patch({ center_gap: Number(e.target.value) })} /></label>
      <label>감지 기준 (영역 중 방지턱 비율)<NumInput min={0.01} max={0.9} step={0.01} value={vs?.threshold ?? 0.12} onChange={e => void patch({ threshold: Number(e.target.value) })} /></label>
    </div>
    <p className="exp-hint">감지 영역이 화면 아래쪽일수록 방지턱이 가까워졌을 때 알아챕니다. 감속이 늦으면 '감지 영역 위'를 줄여(더 위쪽, 더 먼 곳까지) 일찍 알아채게 하세요. 조향을 안 쓰면 '가운데 뺄 폭'은 0으로 해도 됩니다. 틸트를 정해 두면 측정할 때도 같은 각도로 맞춥니다.</p>
  </div>;
}
