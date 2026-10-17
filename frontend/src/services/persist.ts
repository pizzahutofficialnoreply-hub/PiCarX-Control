// 화면에서 마지막으로 고른 값(보기 방식, 입력칸 등)을 브라우저에 기억합니다.
// 기억이 안 되는 환경(사생활 보호 창 등)에서도 화면은 정상 동작합니다.
import { useEffect, useState } from 'react';

export function readStored<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem('picarx-p:' + key);
    if (raw == null) return fallback;
    const v = JSON.parse(raw) as T;
    // 숫자 칸에 글자가 저장된 경우 등 모양이 다르면 기본값으로(빈칸 '' ↔ 숫자 같은 선택형은 허용)
    if (v === undefined || (typeof fallback === 'boolean' && typeof v !== 'boolean') || (Array.isArray(fallback) && !Array.isArray(v))) return fallback;
    return v;
  } catch { return fallback; }
}

/** useState와 같지만 값이 바뀔 때마다 기억하고, 다음에 열면 마지막 값으로 시작합니다. */
export function usePersistentState<T>(key: string, fallback: T): [T, (v: T | ((prev: T) => T)) => void] {
  const [value, setValue] = useState<T>(() => readStored(key, fallback));
  useEffect(() => { try { localStorage.setItem('picarx-p:' + key, JSON.stringify(value)); } catch { /* 무시 */ } }, [key, value]);
  return [value, setValue];
}
