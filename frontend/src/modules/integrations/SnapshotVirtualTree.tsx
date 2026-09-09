import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../api/client";
import "./snapshotVirtualTree.css";

export type SnapshotNode = {
  id: number; external_id: string; parent_external_id: string | null; name: string;
  node_type: string; analysis_state: string; availability: string; source_path?: string | null;
};
type Page = { nodes: SnapshotNode[]; next_cursor: string | null; has_more: boolean };

export const TREE_ROW_HEIGHT = 44;
export function virtualWindow(count: number, scrollTop: number, viewport = 308, overscan = 5) {
  const start = Math.max(0, Math.floor(scrollTop / TREE_ROW_HEIGHT) - overscan);
  const end = Math.min(count, Math.ceil((scrollTop + viewport) / TREE_ROW_HEIGHT) + overscan);
  return { start, end };
}

export function SnapshotVirtualTree({ projectId, snapshotId }: { projectId: number; snapshotId: number }) {
  const [opened, setOpened] = useState(false), [rows, setRows] = useState<SnapshotNode[]>([]);
  const [cursor, setCursor] = useState<string | null>("0"), [busy, setBusy] = useState(false);
  const [error, setError] = useState(""), [filter, setFilter] = useState("changed");
  const [selected, setSelected] = useState<Set<number>>(new Set()), [scrollTop, setScrollTop] = useState(0);
  const scope = useRef(0);
  const window = useMemo(() => virtualWindow(rows.length, scrollTop), [rows.length, scrollTop]);

  useEffect(() => {
    scope.current += 1;
    setRows([]); setCursor("0"); setSelected(new Set()); setScrollTop(0);
    setError(""); setBusy(false);
    return () => { scope.current += 1; };
  }, [projectId, snapshotId]);

  async function load(reset = false) {
    if (busy || (!reset && cursor === null)) return;
    const requestScope = scope.current;
    setBusy(true); setError("");
    try {
      const after = reset ? "0" : cursor || "0";
      const page = await api<Page>(`/projects/${projectId}/snapshots/${snapshotId}/nodes?cursor=${after}&limit=200&analysis_state=${encodeURIComponent(filter)}`);
      if (requestScope !== scope.current) return;
      setRows(current => reset ? page.nodes : [...current, ...page.nodes]);
      setCursor(page.next_cursor);
    } catch (reason) {
      if (requestScope === scope.current) setError((reason as Error).message);
    } finally {
      if (requestScope === scope.current) setBusy(false);
    }
  }

  return <details className="snapshot-tree" onToggle={(event) => {
    const next = event.currentTarget.open; setOpened(next);
    if (next && !rows.length) void load(true);
  }}>
    <summary>Дерево снимка · выбрано {selected.size}</summary>
    {opened && <>
      <div className="snapshot-tree-tools">
        <select aria-label="Фильтр объектов" value={filter} onChange={event => {
          scope.current += 1; setBusy(false); setFilter(event.target.value);
          setRows([]); setCursor("0"); setSelected(new Set()); setScrollTop(0);
        }}>
          <option value="changed">Изменённые и новые</option><option value="pending">Ожидают анализа</option>
          <option value="conflict">Конфликты</option><option value="error">Ошибки</option>
          <option value="analyzed">Проанализированные</option>
        </select>
        <button onClick={() => void load(true)}>Применить</button>
        <button onClick={() => setSelected(new Set(rows.map(row => row.id)))}>Выбрать загруженные</button>
      </div>
      {error && <p role="alert">{error}</p>}
      <div className="snapshot-tree-viewport" onScroll={event => setScrollTop(event.currentTarget.scrollTop)}>
        <div style={{ height: rows.length * TREE_ROW_HEIGHT, position: "relative" }}>
          {rows.slice(window.start, window.end).map((row, index) => <label className="snapshot-tree-row"
            style={{ top: (window.start + index) * TREE_ROW_HEIGHT }} key={row.id}>
            <input type="checkbox" checked={selected.has(row.id)} onChange={event => setSelected(current => {
              const next = new Set(current); event.target.checked ? next.add(row.id) : next.delete(row.id); return next;
            })} />
            <span title={row.source_path || row.name}>{row.name}</span><small>{row.analysis_state} · {row.availability}</small>
          </label>)}
        </div>
      </div>
      {cursor !== null && <button disabled={busy} onClick={() => void load()}>{busy ? "Загрузка…" : "Ещё 200"}</button>}
    </>}
  </details>;
}
