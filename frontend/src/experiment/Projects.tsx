// 프로젝트 저장(영구 보관): 지금까지의 시행 기록을 제목·실험 날짜·메모와 함께 따로 보관하고 메인은 비웁니다.
import { useEffect, useState } from 'react';
import { Archive, Download, FolderOpen, Pencil, Trash2 } from 'lucide-react';
import type { ExperimentContext } from './context';

type Project = { id: string; title: string; date: string; note: string; saved_at: string; runs: number; size_kb: number; first_run?: string | null; last_run?: string | null };

const today = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };

export default function Projects({ ctx }: { ctx: ExperimentContext }) {
  const [list, setList] = useState<Project[]>([]);
  const [mainRuns, setMainRuns] = useState(0);
  const [title, setTitle] = useState('');
  const [date, setDate] = useState(today());
  const [note, setNote] = useState('');
  const [resetSettings, setResetSettings] = useState(false);
  const [edit, setEdit] = useState<Project | null>(null);
  const [saving, setSaving] = useState(false);

  async function refresh() {
    try { const r = await ctx.request('/api/experiment/projects'); setList(r.projects); setMainRuns(r.main_runs); } catch (e) { ctx.fail(e); }
  }
  useEffect(() => { if (ctx.serverAvailable) void refresh(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [ctx.serverAvailable, ctx.data?.runs.length]);

  async function save() {
    if (!title.trim()) return;
    if (!window.confirm(`메인의 시행 ${mainRuns}개를 '${title.trim()}' 프로젝트로 옮기고 메인을 비웁니다. 계속할까요?`)) return;
    setSaving(true);
    try {
      const r = await ctx.request('/api/experiment/projects', { method: 'POST', body: JSON.stringify({ title, date, note, reset_settings: resetSettings }), timeoutMs: 60000 });
      ctx.setMessage(`'${r.project.title}' 프로젝트로 시행 ${r.moved}개를 보관했습니다. 메인은 비었습니다.`);
      setTitle(''); setNote(''); await ctx.loadRuns(null); await refresh();
    } catch (e) { ctx.fail(e); } finally { setSaving(false); }
  }
  async function open(p: Project, settingsToo: boolean) {
    try {
      const r = await ctx.request(`/api/experiment/projects/${p.id}/open?settings_too=${settingsToo}`, { method: 'POST', timeoutMs: 60000 });
      ctx.setMessage(`'${p.title}'의 시행 ${r.opened}개를 메인으로 불러왔습니다(보관본은 그대로 남아 있습니다).`);
      await ctx.loadRuns(null); await refresh();
    } catch (e) { ctx.fail(e); }
  }
  async function saveEdit() {
    if (!edit) return;
    try { await ctx.request(`/api/experiment/projects/${edit.id}`, { method: 'PATCH', body: JSON.stringify({ title: edit.title, date: edit.date, note: edit.note }) }); setEdit(null); await refresh(); } catch (e) { ctx.fail(e); }
  }
  async function remove(p: Project) {
    const c = window.prompt(`'${p.title}' 프로젝트를 완전히 지웁니다(되돌릴 수 없음). 계속하려면 '삭제'를 입력하세요.`);
    if (c?.trim() !== '삭제') return;
    try { await ctx.request(`/api/experiment/projects/${p.id}?confirm=${encodeURIComponent('삭제')}`, { method: 'DELETE' }); await refresh(); } catch (e) { ctx.fail(e); }
  }

  return <article className="card experiment-card">
    <div className="card-head"><div><h2><Archive size={17} /> 프로젝트 저장 (영구 보관)</h2>
      <p>지금 메인에 있는 시행 기록(원자료 포함)과 설정 사본을 제목·실험 날짜·메모와 함께 따로 보관하고, 메인은 빈 상태로 돌려 새 실험을 시작합니다. 초기화(휴지통)와 달리 지워지지 않고, 언제든 '불러오기'로 메인에 복사해 다시 볼 수 있습니다. 센서 보정·방지턱 목록 같은 설정은 메인에 그대로 남습니다.</p></div></div>
    <div className="experiment-fields">
      <label>제목<input value={title} maxLength={80} onChange={e => setTitle(e.target.value)} placeholder="예: 1차 보고서 · PWM 40~100 × 10회" /></label>
      <label>실험 날짜<input type="date" value={date} onChange={e => setDate(e.target.value)} /></label>
      <label style={{ gridColumn: '1 / -1' }}>메모<textarea rows={2} value={note} maxLength={2000} onChange={e => setNote(e.target.value)} placeholder="방지턱 38×12 mm, 600 g, 조향 없음 …" /></label>
      <label className="exp-check"><input type="checkbox" checked={resetSettings} onChange={e => setResetSettings(e.target.checked)} /> 분석 설정(허용 기준·관계식 선택·주행 옵션)도 기본값으로 (보정·방지턱·질량은 유지)</label>
    </div>
    <div className="button-row"><button className="primary" disabled={!ctx.serverAvailable || ctx.busy || saving || !title.trim() || mainRuns === 0} onClick={() => void save()}><Archive size={14} /> 지금 기록을 프로젝트로 저장 (시행 {mainRuns}개)</button>
      {mainRuns === 0 && <span className="exp-hint">메인이 비어 있습니다.</span>}</div>
    {list.length === 0 ? <div className="empty-chart">보관한 프로젝트가 없습니다.</div>
      : <div className="experiment-table-wrap"><table className="experiment-table"><thead><tr><th>실험 날짜</th><th>제목 · 메모</th><th>시행</th><th>보관한 때</th><th></th></tr></thead>
        <tbody>{list.map(p => edit?.id === p.id
          ? <tr key={p.id}><td><input type="date" value={edit.date} onChange={e => setEdit({ ...edit, date: e.target.value })} /></td>
              <td><input value={edit.title} onChange={e => setEdit({ ...edit, title: e.target.value })} /><textarea rows={2} value={edit.note} onChange={e => setEdit({ ...edit, note: e.target.value })} /></td>
              <td>{p.runs}</td><td></td><td><div className="button-row"><button className="secondary" onClick={() => void saveEdit()}>저장</button><button className="secondary" onClick={() => setEdit(null)}>취소</button></div></td></tr>
          : <tr key={p.id}><td>{p.date}</td><td><b>{p.title}</b>{p.note && <><br /><small style={{ whiteSpace: 'pre-wrap' }}>{p.note}</small></>}</td><td>{p.runs}개<br /><small>{p.size_kb} KB</small></td>
              <td><small>{new Date(p.saved_at).toLocaleString()}</small></td>
              <td><div className="button-row">
                <button className="secondary" disabled={ctx.busy || mainRuns > 0} title={mainRuns > 0 ? '메인에 기록이 있으면 먼저 프로젝트로 저장하세요' : '보관본을 메인으로 복사'} onClick={() => void open(p, false)}><FolderOpen size={14} /> 불러오기</button>
                <button className="secondary" disabled={ctx.busy || mainRuns > 0} title="그때의 설정(기준·관계식 선택 등)까지 함께" onClick={() => void open(p, true)}>설정까지 불러오기</button>
                <button className="secondary" onClick={() => ctx.download(`/api/experiment/projects/${p.id}/download.zip`, `picarx_project_${p.date}_${p.title}.zip`)}><Download size={14} /> ZIP</button>
                <button className="secondary" onClick={() => setEdit(p)}><Pencil size={14} /></button>
                <button className="secondary" onClick={() => void remove(p)}><Trash2 size={14} /></button></div></td></tr>)}</tbody></table></div>}
    {list.length > 0 && mainRuns > 0 && <p className="exp-hint">불러오기는 메인이 비어 있을 때만 됩니다(섞이지 않게). 지금 기록을 먼저 프로젝트로 저장하세요.</p>}
  </article>;
}
