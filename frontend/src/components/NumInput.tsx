// 숫자 입력칸: 누르면 전체가 선택돼 바로 새 값을 칠 수 있고, 지우는 동안 기본값(예: 1)이 끼어들지 않습니다.
// 입력 중에는 친 글자를 그대로 보여 주고(빈칸·"1." 같은 중간 상태 허용), 칸을 벗어나면 실제 저장된 값으로 맞춥니다.
import { useRef, useState, type InputHTMLAttributes } from 'react';

type Props = Omit<InputHTMLAttributes<HTMLInputElement>, 'type'>;

export default function NumInput(props: Props) {
  const [text, setText] = useState<string | null>(null);
  const justFocused = useRef(false);
  const controlled = props.value !== undefined;
  const shown = controlled ? (text ?? (props.value == null ? '' : String(props.value))) : undefined;
  return <input {...props} type="number" inputMode="decimal"
    value={shown}
    onFocus={e => {
      if (controlled) setText(props.value == null ? '' : String(props.value));
      justFocused.current = true;
      try { e.currentTarget.select(); } catch { /* 일부 브라우저는 number 칸 선택 미지원 */ }
      props.onFocus?.(e);
    }}
    onMouseUp={e => { if (justFocused.current) { e.preventDefault(); justFocused.current = false; } props.onMouseUp?.(e); }}
    onChange={e => {
      const v = e.target.value;
      if (controlled) setText(v);
      // 빈칸이나 완성된 숫자일 때만 알립니다("-", "1." 같은 중간 상태는 기다림).
      if (v === '' || Number.isFinite(Number(v))) props.onChange?.(e);
    }}
    onBlur={e => { setText(null); justFocused.current = false; props.onBlur?.(e); }} />;
}
