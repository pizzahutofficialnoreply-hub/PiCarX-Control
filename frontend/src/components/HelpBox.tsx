// 접었다 펴는 도움말 상자. 마지막으로 펼쳤는지 기억합니다(처음에는 펼친 채로).
import { useState, type ReactNode } from 'react';
import { HelpCircle } from 'lucide-react';

export default function HelpBox({ id, title, children, defaultOpen = true }: { id: string; title: string; children: ReactNode; defaultOpen?: boolean }) {
  const key = 'picarx-help:' + id;
  const [open, setOpen] = useState<boolean>(() => { try { const v = localStorage.getItem(key); return v == null ? defaultOpen : v === '1'; } catch { return defaultOpen; } });
  return <details className="exp-help" open={open} onToggle={e => { const o = (e.target as HTMLDetailsElement).open; setOpen(o); try { localStorage.setItem(key, o ? '1' : '0'); } catch { /* 무시 */ } }}>
    <summary><HelpCircle size={15} /> {title}</summary>
    <div className="exp-help-body">{children}</div>
  </details>;
}
