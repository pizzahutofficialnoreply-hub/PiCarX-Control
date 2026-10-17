// 직접 만드는 관계식: 글자로 쓴 식을 읽어(파서) 데이터에 맞추는(최소제곱, 레벤버그–마쿼트) 작은 엔진.
//
// 쓸 수 있는 것
//   변수: v(통과 속도 m/s), m(총질량 kg), P(출력값 PWM 또는 듀티), g(9.81), R(유효 곡률 반경 m = 방지턱 R + 바퀴 r),
//         h(방지턱 높이 m), w(방지턱 폭 m), r(바퀴 반지름 m), L(축간거리 m), s(sin α, 방지턱에 오를 때 꺾이는 각), pi, e
//   연산: + − × ÷ 거듭제곱(^ 또는 **), 괄호, 함수 exp ln log sqrt abs pow(a,b) min(a,b) max(a,b)
//   그 밖의 이름(k, n, p, a, b, c, C1 …)은 모두 '데이터로 정할 매개변수'가 됩니다.

export type Env = Record<string, number>;
type Node =
  | { t: 'num'; v: number } | { t: 'var'; name: string }
  | { t: 'neg'; a: Node } | { t: 'bin'; op: string; a: Node; b: Node } | { t: 'fn'; name: string; args: Node[] };

export const KNOWN_VARS: Record<string, string> = {
  v: '통과 속도 (m/s)', m: '총질량 (kg)', P: '출력값 (PWM·듀티)', g: '중력가속도 9.81 m/s²', R: '유효 곡률 반경 R+r (m)',
  h: '방지턱 높이 (m)', w: '방지턱 폭 (m)', r: '바퀴 반지름 (m)', L: '축간거리 (m)', s: 'sin α (턱에 오를 때 꺾이는 각)', pi: 'π', e: '자연상수 e',
};
const FUNCS: Record<string, (...x: number[]) => number> = {
  exp: Math.exp, ln: Math.log, log: Math.log, log10: Math.log10, sqrt: Math.sqrt, abs: Math.abs,
  pow: (a, b) => a ** b, min: Math.min, max: Math.max, sin: Math.sin, cos: Math.cos, tan: Math.tan,
};

export function parse(src: string): Node {
  const s = src.replace(/×/g, '*').replace(/÷/g, '/').replace(/\*\*/g, '^').replace(/·/g, '*').replace(/−/g, '-');
  let i = 0;
  const peek = () => { while (s[i] === ' ') i++; return s[i]; };
  const err = (msg: string) => new Error(`${msg} (위치 ${i + 1})`);
  function primary(): Node {
    const c = peek();
    if (c === '(') { i++; const n = expr(); if (peek() !== ')') throw err("')'가 필요합니다"); i++; return n; }
    if (c === '-') { i++; return { t: 'neg', a: power() }; }
    if (c === '+') { i++; return power(); }
    const num = /^(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?/.exec(s.slice(i));
    if (num) { i += num[0].length; return { t: 'num', v: Number(num[0]) }; }
    const id = /^[A-Za-z_가-힣][A-Za-z0-9_가-힣]*/.exec(s.slice(i));
    if (id) {
      i += id[0].length;
      if (peek() === '(') {
        if (!FUNCS[id[0]]) throw err(`모르는 함수 '${id[0]}'`);
        i++; const args: Node[] = [];
        if (peek() !== ')') { args.push(expr()); while (peek() === ',') { i++; args.push(expr()); } }
        if (peek() !== ')') throw err("')'가 필요합니다"); i++;
        return { t: 'fn', name: id[0], args };
      }
      return { t: 'var', name: id[0] };
    }
    throw err(c == null ? '식이 중간에 끝났습니다' : `'${c}'를 읽을 수 없습니다`);
  }
  function power(): Node { const b = primary(); if (peek() === '^') { i++; return { t: 'bin', op: '^', a: b, b: unary() }; } return b; }
  function unary(): Node { if (peek() === '-') { i++; return { t: 'neg', a: unary() }; } return power(); }
  function term(): Node {
    let n = unary();
    for (;;) {
      const c = peek();
      if (c === '*' || c === '/') { i++; n = { t: 'bin', op: c, a: n, b: unary() }; }
      else if (c && /[A-Za-z_(0-9가-힣]/.test(c)) { n = { t: 'bin', op: '*', a: n, b: unary() }; } // 2v, k v 처럼 곱하기 생략
      else return n;
    }
  }
  function expr(): Node {
    let n = term();
    for (;;) { const c = peek(); if (c === '+' || c === '-') { i++; n = { t: 'bin', op: c, a: n, b: term() }; } else return n; }
  }
  const tree = expr();
  if (peek() != null) throw err(`'${peek()}' 앞까지만 읽었습니다`);
  return tree;
}

export function evaluate(n: Node, env: Env): number {
  switch (n.t) {
    case 'num': return n.v;
    case 'var': { const v = env[n.name]; if (v === undefined) throw new Error(`값이 없는 이름 '${n.name}'`); return v; }
    case 'neg': return -evaluate(n.a, env);
    case 'fn': return FUNCS[n.name](...n.args.map(a => evaluate(a, env)));
    case 'bin': {
      const a = evaluate(n.a, env), b = evaluate(n.b, env);
      return n.op === '+' ? a + b : n.op === '-' ? a - b : n.op === '*' ? a * b : n.op === '/' ? a / b : a ** b;
    }
  }
}

export function names(n: Node, out = new Set<string>()): Set<string> {
  if (n.t === 'var') out.add(n.name);
  else if (n.t === 'neg') names(n.a, out);
  else if (n.t === 'bin') { names(n.a, out); names(n.b, out); }
  else if (n.t === 'fn') n.args.forEach(a => names(a, out));
  return out;
}

export type Point = { env: Env; y: number; label?: string };
export type FitResult = {
  ok: boolean; error?: string; params: string[]; values: Record<string, number>; se: Record<string, number | null>;
  r2: number; rmse: number; loocv: number | null; aic: number | null; n: number; predict: (env: Env) => number; tree: Node;
};

function solve(A: number[][], b: number[]): number[] | null {
  const n = b.length, M = A.map((r, i) => [...r, b[i]]);
  for (let c = 0; c < n; c++) {
    let p = c; for (let r = c + 1; r < n; r++) if (Math.abs(M[r][c]) > Math.abs(M[p][c])) p = r;
    if (Math.abs(M[p][c]) < 1e-14) return null;
    [M[c], M[p]] = [M[p], M[c]];
    for (let r = 0; r < n; r++) if (r !== c) { const f = M[r][c] / M[c][c]; for (let k = c; k <= n; k++) M[r][k] -= f * M[c][k]; }
  }
  return M.map((r, i) => r[n] / r[i]);
}
function inverse(A: number[][]): number[][] | null {
  const n = A.length, cols: number[][] = [];
  for (let j = 0; j < n; j++) { const e = Array.from({ length: n }, (_, i) => (i === j ? 1 : 0)); const x = solve(A, e); if (!x) return null; cols.push(x); }
  return A.map((_, i) => cols.map(c => c[i]));
}

/** 레벤버그–마쿼트: 여러 시작값에서 맞춰 가장 작은 오차를 고릅니다. */
function lm(tree: Node, params: string[], pts: Point[], starts: number[][]): { x: number[]; ssr: number } | null {
  const resid = (x: number[]) => pts.map(p => { const env = { ...p.env }; params.forEach((k, i) => { env[k] = x[i]; }); return evaluate(tree, env) - p.y; });
  const ssrOf = (r: number[]) => r.reduce((s, v) => s + v * v, 0);
  let best: { x: number[]; ssr: number } | null = null;
  for (const x0 of starts) {
    let x = [...x0], r = resid(x), ssr = ssrOf(r), lam = 1e-2;
    if (!Number.isFinite(ssr)) continue;
    for (let it = 0; it < 200; it++) {
      const J = pts.map(() => new Array(params.length).fill(0));
      for (let j = 0; j < params.length; j++) {
        const h = 1e-6 * Math.max(1, Math.abs(x[j]));
        const xp = [...x]; xp[j] += h; const rp = resid(xp);
        const xm = [...x]; xm[j] -= h; const rm = resid(xm);
        for (let i = 0; i < pts.length; i++) J[i][j] = (rp[i] - rm[i]) / (2 * h);
      }
      const JTJ = params.map((_, a) => params.map((__, b) => J.reduce((s, row) => s + row[a] * row[b], 0)));
      const JTr = params.map((_, a) => J.reduce((s, row, i) => s + row[a] * r[i], 0));
      let improved = false;
      for (let k = 0; k < 12; k++) {
        const A = JTJ.map((row, a) => row.map((v, b) => (a === b ? v * (1 + lam) + 1e-12 : v)));
        const d = solve(A, JTr.map(v => -v));
        if (!d) { lam *= 10; continue; }
        const xn = x.map((v, j) => v + d[j]); const rn = resid(xn); const sn = ssrOf(rn);
        if (Number.isFinite(sn) && sn < ssr) { const rel = (ssr - sn) / Math.max(ssr, 1e-30); x = xn; r = rn; ssr = sn; lam = Math.max(lam / 3, 1e-9); improved = rel > 1e-12; break; }
        lam *= 4;
      }
      if (!improved) break;
    }
    if (Number.isFinite(ssr) && (!best || ssr < best.ssr)) best = { x, ssr };
  }
  return best;
}

export function fitFormula(src: string, pts: Point[], known: string[]): FitResult {
  const empty = (error: string): FitResult => ({ ok: false, error, params: [], values: {}, se: {}, r2: 0, rmse: 0, loocv: null, aic: null, n: pts.length, predict: () => NaN, tree: { t: 'num', v: 0 } });
  let tree: Node;
  try { tree = parse(src); } catch (e) { return empty(e instanceof Error ? e.message : String(e)); }
  const params = Array.from(names(tree)).filter(n => !known.includes(n));
  if (!pts.length) return empty('맞출 데이터가 없습니다.');
  if (params.length === 0) {
    // 매개변수가 없는 '이론식'은 그대로 계산해 비교만 합니다.
    try {
      const pred = pts.map(p => evaluate(tree, p.env));
      const ssr = pred.reduce((s, v, i) => s + (v - pts[i].y) ** 2, 0);
      const my = pts.reduce((s, p) => s + p.y, 0) / pts.length, sst = pts.reduce((s, p) => s + (p.y - my) ** 2, 0);
      return { ok: true, params, values: {}, se: {}, r2: sst > 0 ? 1 - ssr / sst : 1, rmse: Math.sqrt(ssr / pts.length), loocv: Math.sqrt(ssr / pts.length), aic: null, n: pts.length, predict: env => evaluate(tree, env), tree };
    } catch (e) { return empty(e instanceof Error ? e.message : String(e)); }
  }
  if (pts.length <= params.length) return empty(`데이터 점(${pts.length}개)이 매개변수(${params.length}개: ${params.join(', ')})보다 많아야 합니다.`);
  const starts = [params.map(() => 1), params.map(() => 0.5), params.map(() => 2), params.map(() => 0.1), params.map((_, i) => (i % 2 ? 2 : 0.3))];
  let fit: { x: number[]; ssr: number } | null;
  try { fit = lm(tree, params, pts, starts); } catch (e) { return empty(e instanceof Error ? e.message : String(e)); }
  if (!fit) return empty('맞추지 못했습니다(식이 데이터에서 계산되지 않음: 0으로 나눔, 음수의 로그 등).');
  const n = pts.length, k = params.length;
  const my = pts.reduce((s, p) => s + p.y, 0) / n, sst = pts.reduce((s, p) => s + (p.y - my) ** 2, 0);
  const values: Record<string, number> = {}; params.forEach((p, i) => { values[p] = fit!.x[i]; });
  const predict = (env: Env) => evaluate(tree, { ...env, ...values });
  // 표준오차: σ²(JᵀJ)⁻¹
  const se: Record<string, number | null> = {};
  try {
    const J = pts.map(p => params.map((name, j) => {
      const h = 1e-6 * Math.max(1, Math.abs(fit!.x[j]));
      const up = { ...p.env, ...values, [name]: fit!.x[j] + h }, dn = { ...p.env, ...values, [name]: fit!.x[j] - h };
      return (evaluate(tree, up) - evaluate(tree, dn)) / (2 * h);
    }));
    const JTJ = params.map((_, a) => params.map((__, b) => J.reduce((s, row) => s + row[a] * row[b], 0)));
    const inv = inverse(JTJ);
    const s2 = n > k ? fit.ssr / (n - k) : NaN;
    params.forEach((p, i) => { se[p] = inv && Number.isFinite(s2) && inv[i][i] >= 0 ? Math.sqrt(s2 * inv[i][i]) : null; });
  } catch { params.forEach(p => { se[p] = null; }); }
  // 한 점씩 빼고 맞춰 그 점을 예측(교차검증)
  let loocv: number | null = null;
  if (n >= k + 2 && n <= 80) {
    let acc = 0, cnt = 0;
    for (let i = 0; i < n; i++) {
      const rest = pts.filter((_, j) => j !== i);
      const f = lm(tree, params, rest, [fit.x]);
      if (!f) continue;
      const env = { ...pts[i].env }; params.forEach((p, j) => { env[p] = f.x[j]; });
      const e = evaluate(tree, env) - pts[i].y; if (Number.isFinite(e)) { acc += e * e; cnt++; }
    }
    loocv = cnt ? Math.sqrt(acc / cnt) : null;
  }
  return { ok: true, params, values, se, r2: sst > 0 ? 1 - fit.ssr / sst : 1, rmse: Math.sqrt(fit.ssr / n), loocv,
    aic: fit.ssr > 0 ? n * Math.log(fit.ssr / n) + 2 * k : null, n, predict, tree };
}

/** 증가 함수라고 보고 f(v) = 목표 가 되는 v를 이분법으로 찾습니다. */
export function solveFor(f: (v: number) => number, target: number, lo = 1e-4, hi = 6): number | null {
  const g = (v: number) => f(v) - target;
  let a = lo, b = hi;
  const ga = g(a), gb = g(b);
  if (!Number.isFinite(ga) || !Number.isFinite(gb) || ga * gb > 0) return null;
  for (let i = 0; i < 80; i++) { const mid = (a + b) / 2; if (g(a) * g(mid) <= 0) b = mid; else a = mid; }
  return (a + b) / 2;
}
