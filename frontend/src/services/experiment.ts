// 방지턱 충격 실험 – 서버(/api/experiment) 데이터 타입과 보조 함수.
// 실험 기록은 Raspberry Pi 디스크에 저장되며, 브라우저에는 입력 중인 조건만 기억합니다.

export type OutputMode = 'pwm' | 'duty';
export type OutlierMethod = 'trim' | 'sigma2' | 'iqr' | 'none';
export const OUTLIER_LABEL: Record<OutlierMethod, string> = {
  trim: '최댓값·최솟값 1개씩 빼기', sigma2: '평균 ± 2×표준편차 밖 빼기', iqr: '사분위수 범위(IQR) 1.5배 밖 빼기', none: '빼지 않음(전부 평균)',
};

export type BumpProfile = { id: string; name: string; height_mm: number | null; width_mm: number | null; radius_mm?: number | null };

export type ExperimentSettings = {
  wheelbase_mm: number | null;
  wheel_radius_mm: number | null;
  base_mass_g: number | null;
  threshold_delta_g: number;
  min_gap_s: number;
  jump_g: number;
  filter_hz?: number;
  detect_level?: 'strict' | 'normal' | 'sensitive';
  a_limit_mode: 'manual' | 'first_jump';
  a_limit_g: number | null;
  a_limit_preset?: string;
  a_limit_effective_g?: number | null;
  a_limit_presets?: Record<string, { delta_g: number | null; label: string; source: string }>;
  pre_roll_s: number;
  run_seconds: number;
  tail_s: number;
  repeats: number;
  return_enabled: boolean;
  return_output: number | null;
  return_scale: number;
  return_stop_on_tape?: boolean;
  stop_after_bump?: boolean;
  after_bump_s?: number;
  rest_s: number;
  heading_hold: boolean;
  heading_gain: number;
  heading_ki?: number;
  heading_learn?: boolean;
  heading_ff_forward?: number;
  heading_ff_backward?: number;
  steering_source?: 'gyro' | 'line' | 'none';
  kick_ms?: number;
  model_id?: string;
  plan_added_masses?: number[];
  plan_outputs?: number[];
  plan_repeats?: number;
  plan_bumps?: string[] | null;
  analysis_direction?: 'forward' | 'backward';
  outlier_method?: OutlierMethod;
  ultrasonic_log?: boolean;
  ultrasonic_hz?: number;
  wall_stop_cm?: number;
  line?: LineSettings;
  vision?: VisionSettings;
  course_spin_lead_deg?: number;
  linecam?: LineCamSettings;
  /** 반복 측정: 끝내기(time = 시간·방지턱 통과 자동 정지, stop_line = 정지선 테이프에서 서서히 정지) */
  end_mode?: 'time' | 'stop_line';
  accel_mode?: 'instant' | 'ramp';
  accel_s?: number;
  decel_s?: number;
  /** 복귀: 없음 / 후진 / 제자리 180° 회전(비우면 return_enabled로) */
  return_mode?: 'none' | 'reverse' | 'uturn' | null;
  spin_output?: number;
  spin_direction?: 'left' | 'right';
  creep_duty?: number;
  leg_timeout_s?: number;
  cross_threshold?: number;
  bumps: BumpProfile[];
  max_output?: number;
};

/** 서버 설정을 받지 못했을 때(이전 버전 백엔드 등) 화면이 동작하도록 쓰는 기본값 */
export const DEFAULT_SETTINGS: ExperimentSettings = {
  wheelbase_mm: null, wheel_radius_mm: null, base_mass_g: null, threshold_delta_g: 0.3, min_gap_s: 0.03, jump_g: 0.2, filter_hz: 0, detect_level: 'normal',
  a_limit_mode: 'manual', a_limit_g: 1.7, a_limit_preset: 'trl614', a_limit_effective_g: 1.7, pre_roll_s: 1.0, run_seconds: 2.5, tail_s: 0.6,
  repeats: 5, return_enabled: true, return_output: null, return_scale: 1.0, rest_s: 1.5, heading_hold: true, heading_gain: 1.2, heading_ki: 0.8, heading_learn: true, heading_ff_forward: 0, heading_ff_backward: 0,
  steering_source: 'gyro', kick_ms: 0, end_mode: 'time', accel_mode: 'instant', accel_s: 0.6, decel_s: 0.3, return_mode: null, spin_output: 100, spin_direction: 'left', creep_duty: 40, leg_timeout_s: 8, cross_threshold: 0.5, model_id: 'auto', plan_added_masses: [0, 150, 300], plan_outputs: [40, 55, 70, 85, 100], plan_repeats: 5,
  bumps: [
    { id: 'A', name: 'A형 (완만)', height_mm: 10, width_mm: 60, radius_mm: 50 },
    { id: 'B', name: 'B형 (급함)', height_mm: 10, width_mm: 30, radius_mm: 16.25 },
  ],
  max_output: 100,
};

export type ExperimentCondition = {
  trial: number;
  bump_id: string;
  base_mass_g: number;
  added_mass_g: number;
  output_mode: OutputMode;
  output_value: number;
  direction?: 'forward' | 'backward';
  video_speed_mps: number | null;
  purpose?: Purpose;
  note: string;
  total_mass_g?: number;
};

export type RunAnalysis = {
  sample_count: number; duration_s: number; sample_rate_hz: number | null;
  peak_g: number | null; peak_delta_g: number | null; peak_t_s: number | null; peak_dynamic_g: number | null;
  front_t_s: number | null; front_g: number | null; rear_t_s: number | null; rear_g: number | null;
  axle_dt_s: number | null; speed_mps: number | null; min_g: number | null; jump: boolean | null;
  airborne_s?: number | null; landing_t_s?: number | null; landing_g?: number | null;
  candidates?: PeakCandidate[]; auto_pair?: { front_t_s: number; front_g: number; rear_t_s: number; rear_g: number; source: string | null } | null; pair_source?: 'auto' | 'manual' | 'consistency' | 'pattern' | 'pitch' | 'pitch4' | null;
  peak_force_n: number | null; dynamic_force_n: number | null; pi: number | null; r_eff_m: number | null;
  critical_speed_mps: number | null; peak_count: number; flags: string[];
  bump_detected?: boolean | null; noise_sigma_g?: number | null; threshold_used_g?: number | null; standout_ratio?: number | null;
  params?: Record<string, number | null>;
  impulse_front_ns?: number | null; impulse_rear_ns?: number | null; impulse_ns?: number | null; dvz_mps?: number | null;
  pulse_s?: number | null; pulse_rear_s?: number | null; avg_force_n?: number | null; momentum_nsx?: number | null;
  events?: BumpEvent[]; glitches_fixed?: number; max_gap_ms?: number | null;
  pitch?: { front_up_t_s: number; rear_up_t_s: number; front_down_t_s?: number; rear_down_t_s?: number; max_deg: number; cross_s?: number; speed_mps?: number } | null;
  entry_sin?: number | null; dvz_theory_mps?: number | null; impulse_theory_ns?: number | null; crest_min_g?: number | null; crest_theory_g?: number | null;
};

export type EventKind = 'FU' | 'FD' | 'RU' | 'RD';
export type BumpEvent = { kind: EventKind; label: string; t_s: number; peak_t_s: number; g: number };
export const EVENT_INFO: Record<EventKind, { label: string; short: string; color: string }> = {
  FU: { label: '앞바퀴 오름', short: '앞↑', color: '#d0782a' }, FD: { label: '앞바퀴 내림', short: '앞↓', color: '#9c4f12' },
  RU: { label: '뒷바퀴 오름', short: '뒤↑', color: '#3c9a5f' }, RD: { label: '뒷바퀴 내림', short: '뒤↓', color: '#1f6b3c' },
};
export type SlowdownInfo = {
  trigger: 'time' | 'tape' | 'distance' | 'camera'; trigger_requested?: string; at_s: number; at_cm: number; safety: number; a_limit_g: number; model_id: string; model_formula?: string;
  margin?: 'ratio' | 'sigma'; model_error_g?: number; target_delta_g?: number;
  v0_pred_mps: number; pred_delta_v0_g: number; vmax_mps: number; target_v_mps: number; pred_delta_target_g: number; needed: boolean;
  output: number | null; output_initial: number; output_clipped: boolean; ratio: number | null; slowed_at_s?: number | null;
  staged?: { v_by_event: Record<EventKind, number>; v_front_mps: number; v_rear_mps: number; output_front: number | null; output_rear: number | null; clipped: boolean; pred_by_event_g: Record<EventKind, number> } | null;
  resume?: boolean; stage_log?: [number, string, number][];
  measured_speed_mps?: number | null; measured_peak_g?: number | null; pass?: boolean;
};
export type EventModel = { k: number; n: number; p: number; uses_mass: boolean; points: number; r2: number; rmse: number };
export const eventPredict = (em: EventModel, v: number, mKg: number) => em.k * v ** em.n * (em.uses_mass ? mKg ** em.p : 1);
export const eventSolve = (em: EventModel, delta: number, mKg: number): number | null => {
  const base = em.k * (em.uses_mass ? mKg ** em.p : 1);
  return delta > 0 && em.n > 0 && base > 0 ? (delta / base) ** (1 / em.n) : null;
};
export type VisionSettings = { floor_mean: number[] | null; floor_std: number | null; roi_top: number; roi_bottom: number; center_gap: number; k_sigma: number; threshold: number };
export type DistanceSummary = {
  n_samples: number; n_valid: number; n_removed: number; rate_hz: number | null; approach_speed_mps: number | null; bump_speed_mps: number | null;
  min_speed_mps: number | null; drop_pct: number | null; us_bump_t_s: number | null; us_vs_imu_s: number | null; us_dip_pct?: number | null;
  distance_at_front_cm: number | null; axle_vs_us_pct: number | null; max_speed_mps: number | null;
};

export type RunSummary = {
  id: string; created_at: string; simulated: boolean; source: 'auto' | 'manual'; aborted: boolean; excluded: boolean;
  condition: ExperimentCondition & { total_mass_g: number };
  bump: BumpProfile;
  analysis: RunAnalysis;
  recording?: { sample_rate_hz?: number | null; count?: number | null };
  heading_hold?: boolean | null; heading_max_deg?: number | null; heading_end_deg?: number | null;
  battery_v?: number | null; steering_source?: string | null; data_source?: string | null; line_lost?: boolean | null; kick_ms?: number | null;
  peak_override?: { front_t_s: number; rear_t_s: number } | null;
  steer_mean_late?: number | null; heading_ff_learned?: number | null;
  slowdown?: SlowdownInfo | null; distance_summary?: DistanceSummary | null; stopped_by_wall?: boolean | null; bump_passed_s?: number | null; elapsed_s?: number | null;
  /** 정지선 코스로 잰 회차: 구간(A→B/B→A), 정지선 인식 시각 */
  course?: { leg?: string | null; end_mode?: string | null; accel?: string | null; detected?: boolean | null; detect_s?: number | null } | null;
  /** 유도선+자이로 조향 요약: 방지턱 위 방향 유지 시간, 유도선 복귀 시간, 최대 방향 차이 */
  guided?: { bump_hold_s: number; recover_s: number; max_offset_deg: number; ref_deg?: number | null; steer_ff_deg?: number; steer_calm_mean?: number | null; camera_used?: number } | null;
  camera_line?: { frames: number; stop_by_camera: boolean } | null; steer_ff_learned?: number | null;
};

export type RunDetail = RunSummary & { series: { t_s: number[]; vertical_g: number[]; dynamic_g: number[]; filtered_g?: number[] }; drive_log?: [number, number, number, number | null][] | null;
  distance_series?: { t_s: number[]; cm: number[]; t_clean: number[]; cm_clean: number[]; speed_mps: (number | null)[] } | null };

export type ConditionGroup = {
  bump_id: string; total_mass_g: number; output_mode: OutputMode; output_value: number;
  n_runs: number; n_used: number; mean_speed_mps: number | null; speed_min_mps: number | null; speed_max_mps: number | null;
  mean_peak_delta_g: number | null; mean_peak_g: number | null; kept_min_delta_g: number; kept_max_delta_g: number;
  dropped_delta_g: number[]; mean_force_n: number | null; mean_pi: number | null; jump_count: number; run_ids: string[];
  sd_peak_delta_g?: number | null; dropped_run_ids?: string[]; outlier?: { method: OutlierMethod; low?: number; high?: number; mean?: number; sd?: number; q1?: number; q3?: number; iqr?: number };
  event_delta_g?: Partial<Record<EventKind, number>>; event_n?: Partial<Record<EventKind, number>>;
  mean_impulse_ns?: number | null; mean_pulse_s?: number | null; mean_dvz_mps?: number | null; mean_crest_min_g?: number | null; mean_avg_force_n?: number | null;
  direction?: 'forward' | 'backward';
};
export type SpeedModel = { bump_id: string; output_mode: OutputMode; a: number | null; b: number | null; c: number; r2: number | null; uses_mass: boolean; n: number; output_range: [number, number]; mass_range_g: [number, number] };
export const speedFromOutput = (models: SpeedModel[] | undefined, bump: string, mode: OutputMode, output: number, massG: number): number | null => {
  const m = models?.find(x => x.bump_id === bump && x.output_mode === mode && x.b);
  return m && m.a != null && m.b != null ? m.a + m.b * output + m.c * massG / 1000 : null;
};
export const outputFromSpeed = (models: SpeedModel[] | undefined, bump: string, mode: OutputMode, v: number, massG: number): number | null => {
  const m = models?.find(x => x.bump_id === bump && x.output_mode === mode && x.b);
  return m && m.a != null && m.b != null && m.b > 0 ? (v - m.a - m.c * massG / 1000) / m.b : null;
};

export type PowerFit = {
  bump_id: string | null; points: number;
  per_mass: { mass_g: number; k: number; n: number; r2: number; points: number }[];
  overall: { k0: number; n: number; p: number; r2: number } | null;
  formula: string | null; note: string | null;
};

export type Criterion = {
  mode: 'manual' | 'first_jump'; a_limit_g: number | null; source: string; preset?: string;
  vmax_by_mass: { mass_g: number; vmax_mps: number | null }[]; critical_speed_mps: number | null;
};

export type RunsResponse = {
  simulated: boolean; runs: RunSummary[]; groups: ConditionGroup[]; fits: Record<string, PowerFit>; criteria?: Record<string, Criterion>;
  models?: Record<string, ModelComparison>; chosen_models?: Record<string, string | null>; interpretation?: Record<string, string[]>;
  speed_maps?: SpeedMap[]; speed_models?: SpeedModel[]; event_models?: Record<string, Partial<Record<EventKind, EventModel>>>; validation?: ValidationBundle; progress?: Progress;
};

export type AutoRunState = {
  state: 'idle' | 'pre_roll' | 'driving' | 'tail' | 'analyzing' | 'returning' | 'turning' | 'aligning' | 'rest' | 'done' | 'error' | 'aborted';
  message: string; run_id: string | null; phase_remaining_s: number | null; result: RunSummary | null;
  cycle?: number; cycles?: number; completed_run_ids?: string[]; heading_deg?: number | null;
};

export const EXPERIMENT_CONDITION_KEY = 'picarx-impact-experiment-condition-v2';
export const G = 9.80665;

export const DEFAULT_CONDITION: ExperimentCondition = {
  trial: 1, bump_id: 'A', base_mass_g: 0, added_mass_g: 0, output_mode: 'pwm', output_value: 50, video_speed_mps: null, note: '',
};

export function readCondition(): ExperimentCondition {
  try {
    const saved = JSON.parse(localStorage.getItem(EXPERIMENT_CONDITION_KEY) || 'null');
    if (!saved || typeof saved !== 'object') return { ...DEFAULT_CONDITION };
    return {
      trial: Math.max(1, Math.round(Number(saved.trial) || 1)),
      bump_id: typeof saved.bump_id === 'string' ? saved.bump_id : 'A',
      base_mass_g: Math.max(0, Number(saved.base_mass_g) || 0),
      added_mass_g: Math.max(0, Number(saved.added_mass_g) || 0),
      output_mode: saved.output_mode === 'duty' ? 'duty' : 'pwm',
      output_value: Math.max(0, Math.min(100, Number(saved.output_value) || 50)),
      video_speed_mps: typeof saved.video_speed_mps === 'number' ? saved.video_speed_mps : null,
      purpose: saved.purpose === 'validation' || saved.purpose === 'pilot' ? saved.purpose : 'main',
      note: typeof saved.note === 'string' ? saved.note : '',
    };
  } catch {
    return { ...DEFAULT_CONDITION };
  }
}

export function writeCondition(condition: ExperimentCondition): void {
  try { localStorage.setItem(EXPERIMENT_CONDITION_KEY, JSON.stringify(condition)); } catch { /* 저장 실패는 무시 */ }
}

/** 원호 단면 방지턱 곡률 반경 R = (w²/4 + h²) / (2h) */
export function bumpRadiusMm(heightMm: number | null | undefined, widthMm: number | null | undefined): number | null {
  if (!heightMm || !widthMm || heightMm <= 0 || widthMm <= 0) return null;
  return (widthMm ** 2 / 4 + heightMm ** 2) / (2 * heightMm);
}

/** 실제 모터 듀티비(%). 라이브러리 PWM은 내부에서 speed/2 + 50 으로 바뀝니다. */
export function effectiveDuty(mode: OutputMode, value: number): number {
  if (mode === 'duty') return value;
  return value > 0 ? Math.floor(value / 2) + 50 : 0;
}

/** v_max = ((a_lim − 1) / (k₀ · m^p))^(1/n),  m: kg */
export function predictVmax(fit: PowerFit | undefined, aLimitG: number, massG: number): number | null {
  const o = fit?.overall;
  if (!o || aLimitG <= 1 || massG <= 0 || o.n <= 0 || o.k0 <= 0) return null;
  const base = (aLimitG - 1) / (o.k0 * (massG / 1000) ** o.p);
  return base > 0 ? base ** (1 / o.n) : null;
}

export function fmt(value: number | null | undefined, digits = 3, unit = ''): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  return `${value.toFixed(digits)}${unit ? ' ' + unit : ''}`;
}

export function outputLabel(mode: OutputMode, value: number): string {
  return mode === 'duty' ? `듀티 ${value}%` : `PWM ${value}`;
}

// ---------------------------------------------------------------- 확장: 모델 비교·검증·진행표·라인
export type Purpose = 'main' | 'validation' | 'pilot';
export const PURPOSE_LABEL: Record<Purpose, string> = { main: '본 실험', validation: '식 검증', pilot: '예비' };

export type ModelParam = { name: string; value: number; se: number | null; ci95: [number, number] | null };
export type ModelResult = {
  id: string; kind: 'power' | 'linear' | 'quadratic'; coef: Record<string, number>;
  name: string; formula: string; explain: string; n_params: number; df: number;
  r2: number; rmse: number; loocv_rmse: number | null; aic: number | null; params: ModelParam[]; exact_fit: boolean;
};
export type ModelComparison = { points: number; masses: number; speeds: number; models: ModelResult[]; best: string | null; rank_by?: string; note: string | null };
export type SpeedMap = { bump_id: string; total_mass_g: number; output_mode: OutputMode; points: { output: number; speed_mps: number }[]; a: number | null; b: number | null; r2: number | null };
export type ValidationRow = {
  run_id: string; created_at: string; bump_id: string; total_mass_g: number; output_mode: OutputMode; output_value: number; trial: number;
  speed_mps: number | null; measured_delta_g: number | null; predicted_delta_g: number | null; error_pct: number | null; abs_error_g: number | null; model_id: string | null;
};
export type ValidationBundle = { rows: ValidationRow[]; summary: { count: number; evaluated: number; mape: number | null; bias_pct: number | null; within_10pct: number; within_20pct: number } };
export type ProgressCell = { bump_id: string; added_mass_g: number; output_value: number; done: number; need: number };
export type Progress = { cells: ProgressCell[]; done: number; need: number; next: ProgressCell | null };
export type LineSettings = { floor: number[] | null; line: number[] | null; gain: number; damping: number; max_steer_deg: number; min_weight: number; lost_timeout_s: number; auto_sign?: boolean; steer_sign?: number; mode?: 'gentle' | 'normal'; deadband?: number; gentle_max_deg?: number; rate_limit_dps?: number;
  /** 학습된 직진 조향(0.1° 단위) */ steer_ff_deg?: number; learn_steer?: boolean; bump_steer?: 'straight' | 'hold'; camera_assist?: boolean };
export type LineCamSettings = { tilt_deg: number; roi_top: number; flip: boolean; min_contrast: number; cross_frac: number; cross_wait_s: number };
export type PeakCandidate = { t_s: number; g: number; landing: boolean };

export function modelPredict(model: { kind: string; coef: Record<string, number> } | null | undefined, v: number, mKg: number): number | null {
  if (!model) return null;
  const c = model.coef;
  if (model.kind === 'power') return v > 0 && mKg > 0 ? c.k0 * v ** c.n * mKg ** (c.p ?? 0) : 0;
  if (model.kind === 'linear') return c.a + c.b * v + (c.c ?? 0) * mKg;
  return c.a + c.b * v * v + (c.c ?? 0) * mKg;
}

/** 예측 Δa가 목표값이 되는 속도(증가 함수 가정, 이분법) */
export function modelSolveSpeed(model: ModelResult | null | undefined, deltaTarget: number, mKg: number): number | null {
  if (!model || deltaTarget <= 0 || mKg <= 0) return null;
  let lo = 1e-4, hi = 6;
  const f = (v: number) => (modelPredict(model, v, mKg) ?? 0) - deltaTarget;
  if (f(lo) > 0 || f(hi) < 0) return null;
  for (let i = 0; i < 80; i++) { const mid = (lo + hi) / 2; if (f(mid) > 0) hi = mid; else lo = mid; }
  return (lo + hi) / 2;
}

/** 목표 속도에 필요한 출력(가장 가까운 질량의 출력→속도 직선) */
export function outputForSpeed(maps: SpeedMap[] | undefined, bumpId: string, massG: number, mode: OutputMode, targetV: number) {
  const cands = (maps ?? []).filter(m => m.bump_id === bumpId && m.output_mode === mode && m.b && m.b > 0);
  if (!cands.length) return null;
  const best = cands.reduce((a, b) => Math.abs(a.total_mass_g - massG) <= Math.abs(b.total_mass_g - massG) ? a : b);
  const out = (targetV - (best.a as number)) / (best.b as number);
  const xs = best.points.map(p => p.output);
  return { output: out, basedOnMass: best.total_mass_g, extrapolated: out < Math.min(...xs) || out > Math.max(...xs), r2: best.r2 };
}

export function paramText(p: ModelParam, digits = 3): string {
  const v = p.name === 'ln k₀' ? `k₀ = ${Math.exp(p.value).toPrecision(4)}` : `${p.name} = ${p.value.toFixed(digits)}`;
  if (!p.ci95) return v;
  if (p.name === 'ln k₀') return `${v} (95% CI ${Math.exp(p.ci95[0]).toPrecision(3)} ~ ${Math.exp(p.ci95[1]).toPrecision(3)})`;
  return `${v} (95% CI ${p.ci95[0].toFixed(digits)} ~ ${p.ci95[1].toFixed(digits)})`;
}
