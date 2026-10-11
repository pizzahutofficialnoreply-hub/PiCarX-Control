export type ImpactSample = {
  timestamp: string;
  elapsed_s: number;
  ax_g: number;
  ay_g: number;
  az_g: number;
  gx_dps: number;
  gy_dps: number;
  gz_dps: number;
  total_g: number;
  delta_x_g: number | null;
  delta_y_g: number | null;
  delta_z_g: number | null;
  dynamic_g: number | null;
  dynamic_m_s2: number | null;
  saturated_possible: boolean;
};

export type ImpactClassification = 'pending' | 'bump' | 'other' | 'uncertain';

export type ImpactCandidate = {
  id: string;
  timestamp: string;
  elapsed_s: number;
  peak_g: number;
  peak_m_s2: number | null;
  classification: ImpactClassification;
};

export type ExperimentCondition = {
  baseMassG: number;
  addedWeightCount: number;
  eachWeightG: number;
  speedLevel: number;
  measuredSpeedMps: number | null;
  bumpHeightMm: number;
  bumpWidthMm: number | null;
  trialNumber: number;
  note: string;
};

export type ExperimentRun = {
  id: string;
  createdAt: string;
  condition: ExperimentCondition;
  samples: ImpactSample[];
  candidates: ImpactCandidate[];
  sampleCount: number;
};

export const EXPERIMENT_RUNS_KEY = 'picarx-impact-experiment-runs-v1';
export const EXPERIMENT_CONDITION_KEY = 'picarx-impact-experiment-condition-v1';
export const IMPACT_THRESHOLD_KEY = 'picarx-impact-experiment-threshold-v1';

export function totalMassG(condition: Pick<ExperimentCondition, 'baseMassG' | 'addedWeightCount' | 'eachWeightG'>): number {
  return Math.max(0, condition.baseMassG) + Math.max(0, condition.addedWeightCount) * Math.max(0, condition.eachWeightG);
}

export function readExperimentRuns(): ExperimentRun[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(EXPERIMENT_RUNS_KEY) || '[]');
    return Array.isArray(value) ? value.filter((item): item is ExperimentRun =>
      item && typeof item.id === 'string' && Array.isArray(item.samples) && Array.isArray(item.candidates)
    ) : [];
  } catch {
    return [];
  }
}

export function writeExperimentRuns(runs: ExperimentRun[]): void {
  try {
    localStorage.setItem(EXPERIMENT_RUNS_KEY, JSON.stringify(runs.slice(0, 100)));
  } catch {
    // Storage quota errors must not interfere with vehicle control.
  }
}

export function detectImpactCandidates(samples: ImpactSample[], thresholdG: number): ImpactCandidate[] {
  const threshold = Math.max(0.01, Math.min(8, thresholdG));
  const candidates: ImpactCandidate[] = [];
  let group: ImpactSample[] = [];

  const flush = () => {
    if (!group.length) return;
    const peak = group.reduce((best, sample) => {
      const value = typeof sample.dynamic_g === 'number' && Number.isFinite(sample.dynamic_g) ? sample.dynamic_g : -1;
      const bestValue = typeof best.dynamic_g === 'number' && Number.isFinite(best.dynamic_g) ? best.dynamic_g : -1;
      return value > bestValue ? sample : best;
    }, group[0]);
    const peakG = typeof peak.dynamic_g === 'number' && Number.isFinite(peak.dynamic_g) ? peak.dynamic_g : 0;
    candidates.push({
      id: `${peak.timestamp}-${peak.elapsed_s.toFixed(4)}`,
      timestamp: peak.timestamp,
      elapsed_s: peak.elapsed_s,
      peak_g: peakG,
      peak_m_s2: typeof peak.dynamic_m_s2 === 'number' && Number.isFinite(peak.dynamic_m_s2) ? peak.dynamic_m_s2 : null,
      classification: 'pending',
    });
    group = [];
  };

  for (const sample of samples) {
    const value = typeof sample.dynamic_g === 'number' && Number.isFinite(sample.dynamic_g) ? sample.dynamic_g : null;
    if (value !== null && value >= threshold) {
      if (group.length && sample.elapsed_s - group[group.length - 1].elapsed_s > 0.08) flush();
      group.push(sample);
    } else {
      flush();
    }
  }
  flush();
  return candidates;
}

export function buildExperimentRun(
  samples: ImpactSample[],
  condition: ExperimentCondition,
  thresholdG: number,
): ExperimentRun | null {
  if (!samples.length) return null;
  const copied = samples.map(sample => ({ ...sample }));
  return {
    id: `run-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    createdAt: new Date().toISOString(),
    condition: { ...condition, trialNumber: Math.max(1, Math.round(condition.trialNumber)) },
    samples: copied,
    candidates: detectImpactCandidates(copied, thresholdG),
    sampleCount: copied.length,
  };
}

export function summarizeSamples(samples: ImpactSample[]) {
  const values = samples
    .map(sample => sample.dynamic_g)
    .filter((value): value is number => typeof value === 'number' && Number.isFinite(value));
  if (!values.length) return { peakG: null as number | null, meanG: null as number | null, standardDeviationG: null as number | null };
  const mean = values.reduce((sum, value) => sum + value, 0) / values.length;
  const variance = values.reduce((sum, value) => sum + (value - mean) ** 2, 0) / values.length;
  return {
    peakG: Math.max(...values),
    meanG: mean,
    standardDeviationG: Math.sqrt(variance),
  };
}
