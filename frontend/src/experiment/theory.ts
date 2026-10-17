// 이론·예측 탭이 함께 쓰는 계산: 방지턱 상수, 예측 모델(내장 관계식 또는 직접 만든 식), 데이터 점.
import { bumpRadiusMm, G, modelPredict, type ConditionGroup, type ExperimentSettings, type ModelResult, type RunSummary } from '../services/experiment';
import { evaluate, parse, type Env } from './formula';

export type Target = 'delta' | 'peak' | 'force' | 'dynforce' | 'impulse' | 'pulse';
export const TARGETS: Record<Target, { label: string; unit: string; short: string }> = {
  delta: { label: 'Δa: 평소보다 더 받은 위아래 가속도', unit: 'g', short: 'Δa' },
  peak: { label: 'a_peak: 최대 수직 가속도 (= 1 + Δa)', unit: 'g', short: 'a_peak' },
  force: { label: 'F: 최대 충격력 = m·g·a_peak', unit: 'N', short: 'F' },
  dynforce: { label: 'F_dyn: 무게보다 더 받은 힘 = m·g·Δa', unit: 'N', short: 'F_dyn' },
  impulse: { label: 'I: 충격량(앞·뒤 바퀴 펄스 넓이 합)', unit: 'N·s', short: 'I' },
  pulse: { label: 'Δt: 앞바퀴 충격 펄스 길이', unit: 's', short: 'Δt' },
};

export function entrySin(rMm: number | null | undefined, hMm: number | null | undefined, wheelMm: number | null | undefined): number | null {
  if (!rMm || !hMm || wheelMm == null) return null;
  const big = rMm + wheelMm, low = rMm - hMm + wheelMm;
  return big > 0 && low < big ? Math.sqrt(big * big - low * low) / big : null;
}

export type BumpConst = { R: number | null; h: number | null; w: number | null; r: number | null; L: number | null; s: number | null; vc: number | null };
export function bumpConst(settings: ExperimentSettings, bumpId: string): BumpConst {
  const b = settings.bumps.find(x => x.id === bumpId);
  const radius = b?.radius_mm ?? bumpRadiusMm(b?.height_mm, b?.width_mm);
  const wheel = settings.wheel_radius_mm;
  const R = radius && wheel != null ? (radius + wheel) / 1000 : null;
  return {
    R, h: b?.height_mm ? b.height_mm / 1000 : null, w: b?.width_mm ? b.width_mm / 1000 : null, r: wheel != null ? wheel / 1000 : null,
    L: settings.wheelbase_mm ? settings.wheelbase_mm / 1000 : null, s: entrySin(radius, b?.height_mm, wheel), vc: R ? Math.sqrt(G * R) : null,
  };
}
export function baseEnv(c: BumpConst): Env {
  const env: Env = { g: G, pi: Math.PI, e: Math.E };
  (['R', 'h', 'w', 'r', 'L', 's'] as const).forEach(k => { if (c[k] != null) env[k] = c[k] as number; });
  return env;
}

export function targetOfGroup(g: ConditionGroup, t: Target): number | null {
  const m = g.total_mass_g / 1000;
  switch (t) {
    case 'delta': return g.mean_peak_delta_g;
    case 'peak': return g.mean_peak_g;
    case 'force': return g.mean_force_n;
    case 'dynforce': return g.mean_peak_delta_g == null ? null : m * G * g.mean_peak_delta_g;
    case 'impulse': return g.mean_impulse_ns ?? null;
    case 'pulse': return g.mean_pulse_s ?? null;
  }
}
export function targetOfRun(r: RunSummary, t: Target): number | null {
  const a = r.analysis, m = r.condition.total_mass_g / 1000;
  switch (t) {
    case 'delta': return a.peak_delta_g;
    case 'peak': return a.peak_g;
    case 'force': return a.peak_force_n;
    case 'dynforce': return a.peak_delta_g == null ? null : m * G * a.peak_delta_g;
    case 'impulse': return a.impulse_ns ?? null;
    case 'pulse': return a.pulse_s ?? null;
  }
}

/** 계산기용 예측기: v(m/s), m(kg), P(출력) → Δa(g) */
export type Predictor = { id: string; name: string; formula: string; delta: (v: number, mKg: number, P?: number | null) => number | null; source: 'builtin' | 'custom' };

export function builtinPredictor(model: ModelResult | null | undefined): Predictor | null {
  if (!model) return null;
  return { id: 'builtin:' + model.id, name: model.name, formula: model.formula, source: 'builtin', delta: (v, m) => modelPredict(model, v, m) };
}

export type SavedFormula = { name: string; expr: string; target: Target; values: Record<string, number>; bump: string; r2?: number; loocv?: number | null };
export function customPredictor(f: SavedFormula, env: Env): Predictor | null {
  if (f.target === 'impulse' || f.target === 'pulse') return null;
  let tree: ReturnType<typeof parse>;
  try { tree = parse(f.expr); } catch { return null; }
  return {
    id: 'custom:' + f.name, name: f.name, formula: f.expr, source: 'custom',
    delta: (v, m, P) => {
      try {
        const y = evaluate(tree, { ...env, ...f.values, v, m, P: P ?? NaN });
        if (!Number.isFinite(y)) return null;
        return f.target === 'delta' ? y : f.target === 'peak' ? y - 1 : f.target === 'force' ? y / (m * G) - 1 : y / (m * G);
      } catch { return null; }
    },
  };
}

/** 표본이 적을 때의 95% t 값 */
export function t95(df: number): number {
  const tab = [12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228, 2.201, 2.179, 2.160, 2.145, 2.131];
  return df <= 0 ? NaN : df <= tab.length ? tab[df - 1] : df < 30 ? 2.06 : 1.96;
}

/** 원점을 지나는 직선 y = k·x 의 최소제곱 기울기와 결정계수 */
export function slopeThroughOrigin(pts: { x: number; y: number }[]): { k: number; r2: number } | null {
  const sxx = pts.reduce((s, p) => s + p.x * p.x, 0);
  if (pts.length < 2 || sxx <= 0) return null;
  const k = pts.reduce((s, p) => s + p.x * p.y, 0) / sxx;
  const my = pts.reduce((s, p) => s + p.y, 0) / pts.length;
  const sst = pts.reduce((s, p) => s + (p.y - my) ** 2, 0), ssr = pts.reduce((s, p) => s + (p.y - k * p.x) ** 2, 0);
  return { k, r2: sst > 0 ? 1 - ssr / sst : 1 };
}
export function linfit(pts: { x: number; y: number }[]): { a: number; b: number; r2: number } | null {
  const n = pts.length;
  if (n < 2) return null;
  const mx = pts.reduce((s, p) => s + p.x, 0) / n, my = pts.reduce((s, p) => s + p.y, 0) / n;
  const sxx = pts.reduce((s, p) => s + (p.x - mx) ** 2, 0);
  if (sxx <= 0) return null;
  const b = pts.reduce((s, p) => s + (p.x - mx) * (p.y - my), 0) / sxx, a = my - b * mx;
  const sst = pts.reduce((s, p) => s + (p.y - my) ** 2, 0), ssr = pts.reduce((s, p) => s + (p.y - a - b * p.x) ** 2, 0);
  return { a, b, r2: sst > 0 ? 1 - ssr / sst : 1 };
}

const KEY = 'picarx-p:saved-formulas';
export function loadFormulas(): SavedFormula[] { try { return JSON.parse(localStorage.getItem(KEY) || '[]') as SavedFormula[]; } catch { return []; } }
export function storeFormulas(list: SavedFormula[]) { try { localStorage.setItem(KEY, JSON.stringify(list.slice(0, 12))); } catch { /* 무시 */ } }
