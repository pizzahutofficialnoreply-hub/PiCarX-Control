// 실험 관리 하위 탭이 함께 쓰는 상태와 동작
import type { AutoRunState, ExperimentCondition, ExperimentSettings, RunsResponse } from '../services/experiment';

export type ImpactInfo = { available: boolean; calibrated: boolean; recording?: boolean; recorded_sample_count?: number } | null;

export type ExperimentContext = {
  request: (path: string, options?: RequestInit & { timeoutMs?: number }) => Promise<any>;
  data: RunsResponse | null;
  settings: ExperimentSettings;
  loadRuns: (select?: string | null) => Promise<RunsResponse>;
  condition: ExperimentCondition;
  setCondition: (update: Partial<ExperimentCondition>) => void;
  startAutoRun: (override?: Partial<ExperimentCondition>, extra?: Record<string, unknown>) => Promise<void>;
  auto: AutoRunState | null;
  busy: boolean;
  serverAvailable: boolean;
  simulated: boolean;
  vehicleConnected: boolean;
  canAutoRun: boolean;
  download: (path: string, name: string) => void;
  fail: (e: unknown) => void;
  setMessage: (m: string) => void;
  patchSettings: (patch: Partial<ExperimentSettings>) => Promise<void>;
  stopNow: () => Promise<void>;
  impact: ImpactInfo;
};
