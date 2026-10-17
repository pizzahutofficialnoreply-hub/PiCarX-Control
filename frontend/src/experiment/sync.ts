// 겹쳐 보기 자동 동기화: 앞바퀴 시각으로 맞춘 회차들을, 기준 회차와 충격 모양이 아주 비슷할 때만
// 몇 ms 더 밀거나 당겨 봉우리가 정확히 겹치게 합니다(모양이 다르면 건드리지 않음).

export type SyncTrace = { id: string; t: number[]; g: number[]; rear_t_s: number | null; front_g?: number | null };
export type SyncResult = { shift_s: number; corr: number | null; applied: boolean; reason: string };

export function interp(t: number[], g: number[], x: number): number | null {
  if (!t.length || x < t[0] || x > t[t.length - 1]) return null;
  let lo = 0, hi = t.length - 1;
  while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (t[mid] <= x) lo = mid; else hi = mid; }
  const span = t[hi] - t[lo];
  return span <= 0 ? g[lo] : g[lo] + (g[hi] - g[lo]) * (x - t[lo]) / span;
}

function pearson(a: number[], b: number[]): number | null {
  const n = a.length;
  if (n < 10) return null;
  const ma = a.reduce((s, x) => s + x, 0) / n, mb = b.reduce((s, x) => s + x, 0) / n;
  let sab = 0, saa = 0, sbb = 0;
  for (let i = 0; i < n; i++) { const da = a[i] - ma, db = b[i] - mb; sab += da * db; saa += da * da; sbb += db * db; }
  return saa > 0 && sbb > 0 ? sab / Math.sqrt(saa * sbb) : null;
}

/**
 * 조건(모두 만족해야 맞춤):
 *  - 앞·뒤 바퀴 간격(통과 속도)이 기준과 15% 안
 *  - 앞바퀴 충격 크기(평소보다 더 받은 g)가 기준의 0.67~1.5배
 *  - ±maxShift 안에서 옮겼을 때 파형 상관계수가 0.8 이상
 */
export function syncToReference(ref: SyncTrace, s: SyncTrace, maxShift = 0.015, minCorr = 0.8): SyncResult {
  if (s.id === ref.id) return { shift_s: 0, corr: 1, applied: false, reason: '기준 회차' };
  if (ref.rear_t_s && s.rear_t_s && Math.abs(s.rear_t_s / ref.rear_t_s - 1) > 0.15)
    return { shift_s: 0, corr: null, applied: false, reason: '앞·뒤 바퀴 간격(속도)이 15% 넘게 달라 그대로' };
  if (ref.front_g != null && s.front_g != null && ref.front_g > 1 && s.front_g > 1) {
    const r = (s.front_g - 1) / (ref.front_g - 1);
    if (r < 0.67 || r > 1.5) return { shift_s: 0, corr: null, applied: false, reason: '앞바퀴 충격 크기가 많이 달라 그대로' };
  }
  const end = (ref.rear_t_s ?? 0.12) + 0.05;
  const grid: number[] = [];
  for (let x = -0.03; x <= end + 1e-9; x += 0.001) grid.push(Number(x.toFixed(4)));
  const refVals = grid.map(x => interp(ref.t, ref.g, x));
  let best = { shift: 0, corr: -2 };
  for (let k = -Math.round(maxShift * 1000); k <= Math.round(maxShift * 1000); k++) {
    const sh = k / 1000;
    const a: number[] = [], b: number[] = [];
    grid.forEach((x, i) => { const r = refVals[i]; const v = interp(s.t, s.g, x + sh); if (r != null && v != null) { a.push(r); b.push(v); } });
    const c = pearson(a, b);
    if (c != null && (c > best.corr + 1e-9 || (Math.abs(c - best.corr) < 1e-9 && Math.abs(sh) < Math.abs(best.shift)))) best = { shift: sh, corr: c };
  }
  if (best.corr < minCorr) return { shift_s: 0, corr: best.corr > -2 ? best.corr : null, applied: false, reason: `파형이 충분히 닮지 않아(상관 ${best.corr > -2 ? best.corr.toFixed(2) : '—'}) 그대로` };
  if (Math.abs(best.shift) < 0.0005) return { shift_s: 0, corr: best.corr, applied: false, reason: '이미 맞음' };
  return { shift_s: best.shift, corr: best.corr, applied: true, reason: `${(best.shift * 1000).toFixed(0)} ms 맞춤` };
}
