// 백엔드 주소와 REST/WebSocket URL을 이 모듈에서 중앙 관리합니다.
const STORAGE_KEY = 'picarx-host';
const DEFAULT_BASE = `http://${window.location.hostname || '127.0.0.1'}:8000`;
let baseUrl = localStorage.getItem(STORAGE_KEY) || DEFAULT_BASE;
export function getApiBaseUrl() { return baseUrl; }
export function saveApiBaseUrl(value: string) {
  baseUrl = value.trim().replace(/\/$/, '');
  localStorage.setItem(STORAGE_KEY, baseUrl);
  return baseUrl;
}
export function getWebSocketUrl(path = '/api/ws') {
  return `${baseUrl.replace(/^http/, 'ws')}${path}`;
}
export async function apiRequest(path: string, options?: RequestInit) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 3500);
  let response: Response;
  try {
    response = await fetch(`${baseUrl}${path}`, {
      ...options,
      signal: controller.signal,
      headers: { 'Content-Type': 'application/json', ...options?.headers },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new Error('서버 응답 시간이 초과되었습니다. 차량 안전 상태와 Wi-Fi 연결을 확인하세요.');
    }
    if (error instanceof TypeError) throw new Error('서버에 연결할 수 없습니다. 주소와 Wi-Fi 연결을 확인하세요.');
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Server error (${response.status})`);
  }
  return response.json();
}
