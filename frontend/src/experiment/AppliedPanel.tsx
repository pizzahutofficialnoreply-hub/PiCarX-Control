// 지금 실제로 적용되는 주행·조향·학습 설정을 항상 보여 주고, 이 화면을 연 뒤(또는 최근 회차 이후) 바뀐 값을 표시합니다.
import { useEffect, useRef, useState } from 'react';
import { Settings2 } from 'lucide-react';
import type { ExperimentContext } from './context';

type Snap = {
  server_started: string; code_version: string | null; heading_learn: boolean; heading_ff_forward: number; heading_ff_backward: number;
  heading_sign: number; heading_gain: number; heading_ki: number; line_steer_ff_deg: number; line_learn_steer: boolean;
  steering_source_default: string; kick_ms_default: number;
  drive?: { mapping: string; trim_forward: number; trim_backward: number; trim_left: number; trim_right: number };
  steering_center?: number; adapter_mode?: string; mpu?: { read_mode: string; bad_reads: number; io_errors: number };
  last_command?: { direction?: string; requested?: number; applied?: number; expected_duty_percent?: number; steering?: number } | null;
};

const STEER_LABEL: Record<string, string> = { gyro: '자이로 직진 보정', line: '유도선 + 자이로', none: '조향 없음' };

function rows(s: Snap): [string, string][] {
  const d = s.drive;
  return [
    ['서버 코드 버전', s.code_version ?? '—'],
    ['서버 시작', new Date(s.server_started).toLocaleString()],
    ['출력 방식', d ? (d.mapping === 'library' ? '라이브러리 PWM' : '직접 듀티') : '—'],
    ['전진 배율 / 후진 배율', d ? `×${d.trim_forward} / ×${d.trim_backward}` : '—'],
    ['왼쪽 / 오른쪽 바퀴 배율', d ? `×${d.trim_left} / ×${d.trim_right}` : '—'],
    ['조향 중심값', s.steering_center != null ? `${s.steering_center}°` : '—'],
    ['측정 중 조향 (서버 기본값)', STEER_LABEL[s.steering_source_default] ?? s.steering_source_default],
    ['학습된 직진 조향 (전진 / 후진)', `${s.heading_ff_forward}° / ${s.heading_ff_backward}°`],
    ['직진 조향 학습', s.heading_learn ? '켜짐 (회차마다 바뀜)' : '꺼짐'],
    ['자이로 보정 방향', s.heading_sign > 0 ? '+1' : '−1 (자동으로 뒤집힘)'],
    ['유도선 직진 조향 / 학습', `${s.line_steer_ff_deg}° / ${s.line_learn_steer ? '켜짐' : '꺼짐'}`],
    ['출발 순간 최대 출력', s.kick_ms_default ? `${s.kick_ms_default} ms` : '끔'],
    ['MPU 읽기', s.mpu ? `${s.mpu.read_mode === 'fifo' ? '센서 버퍼' : '직접 읽기'} · 깨진 표본 ${s.mpu.bad_reads} · 통신 오류 ${s.mpu.io_errors}` : '—'],
  ];
}
const CHANGE_IGNORE = new Set(['MPU 읽기']);

export default function AppliedPanel({ ctx }: { ctx: ExperimentContext }) {
  const [snap, setSnap] = useState<Snap | null>(null);
  const first = useRef<Map<string, string> | null>(null);
  const [open, setOpen] = useState(false);
  async function load() {
    try {
      const s = await ctx.request('/api/experiment/applied', { timeoutMs: 6000 }) as Snap;
      setSnap(s);
      if (!first.current) first.current = new Map(rows(s));
    } catch { /* 연결 안 됨: 조용히 넘김 */ }
  }
  useEffect(() => {
    if (!ctx.serverAvailable) return;
    void load();
    const id = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ctx.serverAvailable]);
  if (!snap) return null;
  const now = rows(snap);
  const changed = now.filter(([k, v]) => !CHANGE_IGNORE.has(k) && first.current?.get(k) !== undefined && first.current.get(k) !== v);
  // 최근 실제 회차에 기록된 설정과 비교
  const last = (ctx.data?.runs ?? []).find(r => (r as unknown as { applied?: Snap }).applied) as unknown as { applied?: Snap; condition: { trial: number } } | undefined;
  const lastRows = last?.applied ? new Map(rows(last.applied)) : null;
  const diffLast = lastRows ? now.filter(([k, v]) => !CHANGE_IGNORE.has(k) && k !== '서버 시작' && lastRows.get(k) !== undefined && lastRows.get(k) !== v) : [];
  const learnWarn = snap.heading_learn || snap.line_learn_steer;
  const d = snap.drive;
  const summary = d ? `라이브러리 ${d.mapping === 'library' ? 'PWM' : '듀티'} · 전진 ×${d.trim_forward} · 좌우 ×${d.trim_left}/×${d.trim_right} · 조향 중심 ${snap.steering_center ?? '—'}° · 학습 조향 ${snap.heading_ff_forward}° · 코드 ${snap.code_version ?? '—'}` : '';
  return <section className={'card experiment-card' + (changed.length || diffLast.length ? ' warn' : '')} style={{ padding: '10px 14px' }}>
    <div className="button-row" style={{ justifyContent: 'space-between' }}>
      <span><Settings2 size={15} /> <b>지금 적용되는 설정</b> <small className="exp-hint">{summary}</small></span>
      <button className="secondary" onClick={() => setOpen(!open)}>{open ? '접기' : '자세히'}</button>
    </div>
    {changed.length > 0 && <p className="diagnostic-error">이 화면을 연 뒤 바뀐 값: {changed.map(([k, v]) => `${k} ${first.current?.get(k)} → ${v}`).join(' · ')}</p>}
    {diffLast.length > 0 && <p className="diagnostic-error">최근 회차({last?.condition.trial}회차) 때와 다른 값: {diffLast.map(([k, v]) => `${k} ${lastRows?.get(k)} → ${v}`).join(' · ')}</p>}
    {learnWarn && <p className="exp-hint"><b>직진 조향 학습이 켜져 있어</b> 자이로·유도선 조향을 쓰면 회차가 끝날 때마다 '학습된 직진 조향' 값이 바뀌고 다음 회차 주행이 달라집니다. 조건을 고정하려면 끄세요.
      <button className="secondary" style={{ marginLeft: 8 }} onClick={() => void ctx.patchSettings({ heading_learn: false, line: { ...(ctx.settings.line as object), learn_steer: false } } as never)}>학습 끄기</button></p>}
    {open && <div className="experiment-table-wrap"><table className="experiment-table"><tbody>
      {now.map(([k, v]) => <tr key={k} className={changed.some(c => c[0] === k) || diffLast.some(c => c[0] === k) ? 'over' : ''}><td>{k}</td><td><b>{v}</b></td><td><small>{lastRows?.get(k) !== undefined && lastRows.get(k) !== v && k !== '서버 시작' ? `최근 회차: ${lastRows.get(k)}` : ''}</small></td></tr>)}
      {snap.last_command && <tr><td>마지막 모터 명령</td><td colSpan={2}><b>{snap.last_command.direction}</b>{snap.last_command.requested != null ? ` · 요청 ${snap.last_command.requested} → 라이브러리에 ${snap.last_command.applied} → 실제 듀티 ${snap.last_command.expected_duty_percent}% · 조향 ${snap.last_command.steering}°` : ''}</td></tr>}
    </tbody></table>
      <p className="exp-hint">5초마다 서버에서 다시 읽습니다. 회차마다 이 설정이 함께 저장되어, 결과가 달라졌을 때 설정이 바뀐 탓인지 확인할 수 있습니다.</p></div>}
  </section>;
}
