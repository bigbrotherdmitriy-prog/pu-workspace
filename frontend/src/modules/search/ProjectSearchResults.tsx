import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle, Bookmark, CalendarClock, FileText, FolderKanban, ListTodo, Mail,
  Search, SearchX, ShieldCheck, SlidersHorizontal, Trash2,
} from "lucide-react";
import {
  EMPTY_SEARCH_FILTERS, SEARCH_KINDS, type ProjectSearchFilters, type ProjectSearchHit,
  type ProjectSearchKind, useProjectSearch,
} from "./useProjectSearch";
import { draftFromSavedView, type SavedSearchView, useSavedSearchViews } from "./useSavedSearchViews";
import "./project-search.css";

export type { ProjectSearchHit } from "./useProjectSearch";

const icons = {
  project: FolderKanban, document: FileText, contract: FileText, task: ListTodo,
  obligation: CalendarClock, risk: AlertTriangle, decision: ShieldCheck, message: Mail,
};
const labels: Record<ProjectSearchKind, string> = {
  project: "Проект", document: "Документ", contract: "Договор", task: "Задача",
  obligation: "Обязательство", risk: "Риск", decision: "Решение", message: "Письмо",
};

type ResultsProps = {
  query: string; hits: ProjectSearchHit[]; loading?: boolean; error?: string;
  scanTruncated?: boolean; hasMore?: boolean; onLoadMore?: () => void;
  onOpen: (hit: ProjectSearchHit) => void;
};

export function ProjectSearchResults({
  query, hits, loading = false, error = "", scanTruncated = false, hasMore = false, onLoadMore, onOpen,
}: ResultsProps) {
  const uniqueHits = hits.reduce<ProjectSearchHit[]>((result, hit) => {
    if (!result.some((item) => item.kind === hit.kind && item.id === hit.id)) result.push(hit);
    return result;
  }, []);
  return <div className="project-search-result-list" role="listbox" aria-label="Результаты поиска по проекту">
    <div className="project-search-result-head"><span><small>Серверный поиск</small><strong>Найдено в проекте</strong></span><b>{uniqueHits.length}</b></div>
    {query.trim() && <div className="project-search-query">«{query.trim()}»</div>}
    {error && <div className="project-search-state error" role="alert">{error}</div>}
    {loading && !uniqueHits.length && <div className="project-search-state" role="status">Ищем по проекту…</div>}
    {uniqueHits.map((hit) => {
      const Icon = icons[hit.kind];
      return <button type="button" role="option" aria-selected="false" onClick={() => onOpen(hit)} key={`${hit.kind}-${hit.id}`}>
        <Icon /><span><small>{labels[hit.kind]}</small><strong>{hit.title}</strong>{hit.detail && <em>{hit.detail}</em>}</span>
      </button>;
    })}
    {!loading && !error && !uniqueHits.length && <div className="project-search-empty"><SearchX /><strong>Совпадений нет</strong><p>Измените запрос или фильтры.</p></div>}
    {scanTruncated && <div className="project-search-warning">Просмотрен безопасно ограниченный объём данных. Уточните запрос или фильтры.</div>}
    {hasMore && <button className="project-search-more" type="button" disabled={loading} onClick={onLoadMore}>{loading ? "Загрузка…" : "Показать ещё"}</button>}
  </div>;
}

type WorkspaceProps = { projectId: number; query: string; onQueryChange: (value: string) => void; onOpen: (hit: ProjectSearchHit) => void };

export function ProjectSearchWorkspace({ projectId, query, onQueryChange, onOpen }: WorkspaceProps) {
  const [filters, setFilters] = useState<ProjectSearchFilters>(EMPTY_SEARCH_FILTERS);
  const [open, setOpen] = useState(false);
  const [showFilters, setShowFilters] = useState(false);
  const [selectedViewId, setSelectedViewId] = useState(0);
  const [actionError, setActionError] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const search = useProjectSearch(projectId, query, filters);
  const saved = useSavedSearchViews(projectId, open);

  useEffect(() => {
    setFilters(EMPTY_SEARCH_FILTERS); setSelectedViewId(0); setOpen(false); setActionError("");
  }, [projectId]);
  useEffect(() => {
    const keyboard = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLocaleLowerCase() === "k") {
        event.preventDefault(); setOpen(true); inputRef.current?.focus();
      }
      if (event.key === "Escape" && rootRef.current?.contains(document.activeElement)) {
        setOpen(false); inputRef.current?.blur();
      }
    };
    const outside = (event: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", keyboard); document.addEventListener("pointerdown", outside);
    return () => { document.removeEventListener("keydown", keyboard); document.removeEventListener("pointerdown", outside); };
  }, []);

  function patchFilter<K extends keyof ProjectSearchFilters>(key: K, value: ProjectSearchFilters[K]) {
    setSelectedViewId(0); setFilters((current) => ({ ...current, [key]: value }));
  }
  function toggleType(kind: ProjectSearchKind) {
    patchFilter("types", filters.types.includes(kind) ? filters.types.filter((item) => item !== kind) : [...filters.types, kind]);
  }
  function applyView(view: SavedSearchView) {
    const draft = draftFromSavedView(view);
    onQueryChange(draft.query); setFilters(draft.filters); setSelectedViewId(view.id); setActionError(""); setOpen(true);
  }
  async function saveCurrent() {
    const name = window.prompt("Название сохранённого вида");
    if (!name?.trim()) return;
    try { const view = await saved.create(name, { query, filters }); setSelectedViewId(view.id); setActionError(""); }
    catch (caught) { setActionError(caught instanceof Error ? caught.message : "Не удалось сохранить вид"); }
  }
  async function renameCurrent() {
    const view = saved.views.find((item) => item.id === selectedViewId);
    if (!view) return;
    const name = window.prompt("Новое название вида", view.name);
    if (!name?.trim() || name.trim() === view.name) return;
    try { await saved.rename(view, name); setActionError(""); }
    catch (caught) { setActionError(caught instanceof Error ? caught.message : "Не удалось переименовать вид"); }
  }
  async function deleteCurrent() {
    const view = saved.views.find((item) => item.id === selectedViewId);
    if (!view || !window.confirm(`Удалить сохранённый вид «${view.name}»?`)) return;
    try { await saved.remove(view); setSelectedViewId(0); setActionError(""); }
    catch (caught) { setActionError(caught instanceof Error ? caught.message : "Не удалось удалить вид"); }
  }

  return <div className="search" ref={rootRef}>
    <Search />
    <input ref={inputRef} aria-label="Поиск по проекту" value={query} onFocus={() => setOpen(true)}
      onChange={(event) => { setSelectedViewId(0); onQueryChange(event.target.value); }} placeholder="Поиск по проекту" />
    <button className="project-search-filter-toggle" type="button" aria-label="Фильтры поиска" aria-pressed={showFilters}
      onClick={() => { setOpen(true); setShowFilters((value) => !value); }}><SlidersHorizontal /></button>
    <kbd>Ctrl K</kbd>
    {open && <div className="project-search-results">
      <div className="project-search-toolbar">
        <select aria-label="Сохранённый вид" value={selectedViewId} disabled={saved.loading} onChange={(event) => {
          const id = Number(event.target.value); setSelectedViewId(id);
          const view = saved.views.find((item) => item.id === id); if (view) applyView(view);
        }}>
          <option value={0}>{saved.loading ? "Загрузка видов…" : "Сохранённые виды"}</option>
          {saved.views.map((view) => <option key={view.id} value={view.id}>{view.name}</option>)}
        </select>
        <button type="button" onClick={() => void saveCurrent()} title="Сохранить текущий запрос"><Bookmark /> Сохранить</button>
        {selectedViewId > 0 && <><button type="button" onClick={() => void renameCurrent()}>Переименовать</button><button type="button" aria-label="Удалить сохранённый вид" onClick={() => void deleteCurrent()}><Trash2 /></button></>}
      </div>
      {(saved.error || actionError) && <div className="project-search-state error" role="alert">{actionError || saved.error}</div>}
      {showFilters && <div className="project-search-filters">
        <fieldset><legend>Типы</legend>{SEARCH_KINDS.map((kind) => <label key={kind}><input type="checkbox" checked={filters.types.includes(kind)} onChange={() => toggleType(kind)} />{labels[kind]}</label>)}</fieldset>
        <label>С даты<input type="date" value={filters.dateFrom} onChange={(event) => patchFilter("dateFrom", event.target.value)} /></label>
        <label>По дату<input type="date" value={filters.dateTo} onChange={(event) => patchFilter("dateTo", event.target.value)} /></label>
        <label>Договор<input inputMode="numeric" value={filters.contractId} onChange={(event) => patchFilter("contractId", event.target.value.replace(/\D/g, ""))} placeholder="ID договора" /></label>
        <label>Контрагент<input value={filters.counterparty} onChange={(event) => patchFilter("counterparty", event.target.value)} placeholder="Название" /></label>
        <button type="button" onClick={() => { setFilters(EMPTY_SEARCH_FILTERS); setSelectedViewId(0); }}>Сбросить</button>
      </div>}
      <ProjectSearchResults query={query} hits={search.hits} loading={search.loading} error={search.error}
        scanTruncated={search.scanTruncated} hasMore={Boolean(search.nextCursor)} onLoadMore={() => void search.loadMore()}
        onOpen={(hit) => { setOpen(false); onOpen(hit); }} />
    </div>}
  </div>;
}
