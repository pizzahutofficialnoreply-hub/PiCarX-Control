// PiCar-X 백엔드 주소와 REST/WebSocket URL을 중앙 관리합니다.

const STORAGE_KEY = 'picarx-host';
const RECENT_KEY = 'picarx-hosts';

// 브라우저에서 실행 중인 호스트를 기본값으로 사용합니다.
// 예:
// Windows에서 http://localhost:5173으로 실행 → http://localhost:8000
// Raspberry Pi에서 직접 실행 → http://<현재 호스트>:8000
const DEFAULT_BASE = `http://${window.location.hostname || '127.0.0.1'}:8000`;

let baseUrl = normalizeBaseUrl(
  localStorage.getItem(STORAGE_KEY) || DEFAULT_BASE,
);

function normalizeBaseUrl(value: string): string {
  let url = value.trim();

  if (!url) {
    return DEFAULT_BASE;
  }

  // 사용자가 172.30.1.95:8000처럼 입력해도 동작하도록 처리
  if (!/^https?:\/\//i.test(url)) {
    url = `http://${url}`;
  }

  // 마지막 / 제거
  url = url.replace(/\/+$/, '');

  return url;
}

export function getApiBaseUrl(): string {
  return baseUrl;
}

export function saveApiBaseUrl(value: string): string {
  baseUrl = normalizeBaseUrl(value);
  localStorage.setItem(STORAGE_KEY, baseUrl);
  const recent = getRecentApiBaseUrls().filter((url) => url !== baseUrl);
  localStorage.setItem(RECENT_KEY, JSON.stringify([baseUrl, ...recent].slice(0, 5)));
  return baseUrl;
}

export function getRecentApiBaseUrls(): string[] {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(RECENT_KEY) || '[]');
    return Array.isArray(parsed) ? parsed.filter((value): value is string => typeof value === 'string') : [];
  } catch {
    return [];
  }
}

export function getWebSocketUrl(path = '/api/ws'): string {
  const wsBase = baseUrl.replace(/^http/i, 'ws');
  return `${wsBase}${path}`;
}

export async function apiRequest(
  path: string,
  options: RequestInit = {},
): Promise<any> {
  const controller = new AbortController();

  const timeout = window.setTimeout(() => {
    controller.abort();
  }, 3500);

  let response: Response;

  try {
    response = await fetch(`${baseUrl}${path}`, {
      ...options,
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        ...options.headers,
      },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new Error(
        '서버 응답 시간이 초과되었습니다. 차량 안전 상태와 Wi-Fi 연결을 확인하세요.',
      );
    }

    if (error instanceof TypeError) {
      throw new Error(
        '서버에 연결할 수 없습니다. Raspberry Pi 주소와 Wi-Fi 연결을 확인하세요.',
      );
    }

    throw error;
  } finally {
    window.clearTimeout(timeout);
  }

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));

    throw new Error(
      body?.detail ||
        body?.message ||
        `서버 오류 (${response.status})`,
    );
  }

  // 204 No Content 대응
  if (response.status === 204) {
    return null;
  }

  return response.json();
}

/* -------------------------------------------------------------------------- */
/* 연결 확인                                                                  */
/* -------------------------------------------------------------------------- */

export async function checkHealth() {
  return apiRequest('/api/health');
}

export async function getVehicleStatus() {
  return apiRequest('/api/status');
}

export async function getDiagnostics() {
  return apiRequest('/api/diagnostics');
}

export async function getSensors() {
  return apiRequest('/api/sensors');
}

/* -------------------------------------------------------------------------- */
/* 주행                                                                         */
/* -------------------------------------------------------------------------- */

export type DriveDirection =
  | 'forward'
  | 'backward'
  | 'left'
  | 'right';

export interface DriveCommand {
  direction: DriveDirection;

  // 백엔드의 speed는 실제 km/h가 아니라 Motor PWM입니다.
  // 현재 백엔드 제한: 0 ~ 60
  speed: number;

  // -30 = 최대 좌회전
  //   0 = 중앙
  // +30 = 최대 우회전
  steering: number;
}

export async function drive(command: DriveCommand) {
  return apiRequest('/api/drive', {
    method: 'POST',
    body: JSON.stringify({
      direction: command.direction,
      speed: Math.max(0, Math.min(60, Math.round(command.speed))),
      steering: Math.max(-30, Math.min(30, Math.round(command.steering))),
    }),
  });
}

export async function stopVehicle() {
  return apiRequest('/api/stop', {
    method: 'POST',
  });
}

/* -------------------------------------------------------------------------- */
/* 설정 / 조향 테스트                                                          */
/* -------------------------------------------------------------------------- */

export type SteeringTestDirection =
  | 'left'
  | 'right'
  | 'center';

export async function steeringTest(
  direction: SteeringTestDirection,
  angle = 20,
) {
  return apiRequest('/api/settings/steering-test', {
    method: 'POST',
    body: JSON.stringify({
      direction,
      angle: Math.max(0, Math.min(30, Math.round(angle))),
    }),
  });
}

export async function saveCalibration(
  calibration: Record<string, unknown>,
) {
  return apiRequest('/api/settings/calibration', {
    method: 'PUT',
    body: JSON.stringify(calibration),
  });
}
