import { useMemo, useRef, useState } from 'react';
import { flattenWbs, moveWbsRow, parseWbsGraph, wbsDraft, wbsPayload, wbsResponse, type WbsGraph, type WbsRow } from './wbsReadModel';
import './wbsTree.css';

export type WbsTreeEditorProps = {
  graph: unknown;
  baselineId: number;
  disabled?: boolean;
  save: (payload: ReturnType<typeof wbsPayload>) => Promise<unknown>;
  onSaved?: (graph: WbsGraph) => void;
};

export function WbsTreeEditor({ graph: raw, baselineId, disabled = false, save, onSaved }: WbsTreeEditorProps) {
  let graph: WbsGraph;
  try { graph = parseWbsGraph(raw, baselineId); } catch { return <p role="alert">Структура ГПР недоступна: получен неподтверждённый WBS-ответ.</p>; }
  return <WbsSession key={`${graph.baseline_id}:${graph.graph_revision}`} graph={graph} disabled={disabled} save={save} onSaved={onSaved} />;
}

function WbsSession({ graph, disabled, save, onSaved }: { graph: WbsGraph; disabled: boolean; save: WbsTreeEditorProps['save']; onSaved?: WbsTreeEditorProps['onSaved'] }) {
  const [rows, setRows] = useState(() => wbsDraft(graph));
  const [busy, setBusy] = useState(false); const [blocked, setBlocked] = useState(false); const [error, setError] = useState('');
  const counter = useRef(0); const lock = useRef(false);
  const flat = useMemo(() => { try { return flattenWbs(rows); } catch { return []; } }, [rows]);
  const dirty = JSON.stringify(rows) !== JSON.stringify(wbsDraft(graph));
  const editable = !disabled && !busy && !blocked && graph.status === 'draft';
  const summaries = rows.filter(row => row.summary);
  function add(summary: boolean) {
    const key = `new${++counter.current}`;
    const siblings = rows.filter(row => row.parentKey === null);
    setRows(current => [...current, { key, title: summary ? 'Новый раздел' : 'Новая работа', parentKey: null,
      order: siblings.length, summary, duration: summary ? '' : '1', milestone: false, dependencies: '' }]);
  }
  function change(key: string, patch: Partial<WbsRow>) { setRows(current => current.map(row => row.key === key ? { ...row, ...patch } : row)); setError(''); }
  function move(key: string, parentKey: string | null, delta = 0) {
    try {
      const row = rows.find(item => item.key === key)!;
      const siblings = rows.filter(item => item.parentKey === parentKey).sort((a, b) => a.order - b.order);
      const current = row.parentKey === parentKey ? siblings.findIndex(item => item.key === key) : siblings.length;
      setRows(moveWbsRow(rows, key, parentKey, current + delta)); setError('');
    } catch { setError('Перемещение отклонено: раздел нельзя вложить в себя или своего потомка.'); }
  }
  async function submit() {
    if (!editable || !dirty || lock.current) return;
    let payload: ReturnType<typeof wbsPayload>;
    try { payload = wbsPayload(graph, rows); } catch { setError('Проверьте иерархию, названия, длительности и ссылки работ.'); return; }
    lock.current = true; setBusy(true); setError('');
    try {
      const result = wbsResponse(await save(payload), graph, payload);
      onSaved?.(result);
    } catch { setBlocked(true); setError('Сохранение WBS не подтверждено. Автоматический повтор заблокирован; обновите серверную ревизию.'); }
    finally { lock.current = false; setBusy(false); }
  }
  return <section className="wbs-tree" aria-label="Структура работ ГПР" aria-busy={busy}>
    <header><div><h3>Структура работ</h3><p>Разделы сворачивают этапы в дерево. В календарный расчёт входят только работы и вехи.</p></div>
      <div><button type="button" disabled={!editable || rows.length >= 500} onClick={() => add(true)}>Добавить раздел</button>
        <button type="button" disabled={!editable || rows.length >= 500} onClick={() => add(false)}>Добавить работу</button></div></header>
    {error && <p role="alert">{error}</p>}
    {!flat.length && rows.length > 0 && <p role="alert">Дерево содержит недопустимую связь.</p>}
    <ol className="wbs-tree-list">{flat.map(({ row, level }) => {
      const siblings = rows.filter(item => item.parentKey === row.parentKey).sort((a, b) => a.order - b.order);
      const index = siblings.findIndex(item => item.key === row.key);
      return <li key={row.key} style={{ '--wbs-level': level } as React.CSSProperties} className={row.summary ? 'wbs-summary' : 'wbs-leaf'}>
        <div className="wbs-row"><span className="wbs-code">{row.summary ? 'Раздел' : row.milestone ? 'Веха' : 'Работа'} · {row.key}</span>
          <label>Название {row.key}<input value={row.title} maxLength={500} disabled={!editable} onChange={event => change(row.key, { title: event.target.value })} /></label>
          <label>Родитель {row.key}<select value={row.parentKey ?? ''} disabled={!editable} onChange={event => move(row.key, event.target.value || null)}>
            <option value="">Корень проекта</option>{summaries.filter(item => item.key !== row.key).map(item => <option key={item.key} value={item.key}>{item.title} ({item.key})</option>)}</select></label>
          {!row.summary && <><label>Дней {row.key}<input type="number" min={row.milestone ? 0 : 1} max={10000} disabled={!editable || row.milestone} value={row.duration} onChange={event => change(row.key, { duration: event.target.value })} /></label>
            <label>Веха {row.key}<input type="checkbox" disabled={!editable} checked={row.milestone} onChange={event => change(row.key, { milestone: event.target.checked, duration: event.target.checked ? '0' : '1' })} /></label></>}
          <div className="wbs-order"><button type="button" aria-label={`Поднять ${row.key}`} disabled={!editable || index === 0} onClick={() => move(row.key, row.parentKey, -1)}>↑</button>
            <button type="button" aria-label={`Опустить ${row.key}`} disabled={!editable || index === siblings.length - 1} onClick={() => move(row.key, row.parentKey, 1)}>↓</button></div>
        </div>
      </li>;
    })}</ol>
    <footer><button type="button" disabled={!editable || !dirty} onClick={() => void submit()}>Сохранить структуру и пересчитать</button>
      <button type="button" className="secondary" disabled={busy} onClick={() => { setRows(wbsDraft(graph)); setBlocked(false); setError(''); }}>Отбросить изменения</button></footer>
  </section>;
}
