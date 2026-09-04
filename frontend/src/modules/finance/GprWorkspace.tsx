import { type PointerEvent as ReactPointerEvent, useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeftToLine, ArrowRightToLine, CalendarClock, ChevronDown, ChevronRight, Copy, Diamond, Download, LocateFixed, Plus, Save, Upload } from "lucide-react";
import { api } from "../../api/client";
import type { FinanceOverview, MppPreview } from "./types";

type Task = FinanceOverview["schedule"][number];
type Props = {
  projectId: number;
  finance: FinanceOverview | null;
  selectedContractId: number;
  onPrepare: (kind: string, baselineId?: number) => void;
  onUpdateTask: (id: number, patch: Record<string, unknown>) => Promise<void>;
  onBulkUpdate: (baselineId: number, ids: number[], patch: Record<string, unknown>) => Promise<void>;
  onCloneBaseline: (baselineId: number) => Promise<number | void>;
  onImported: () => Promise<void> | void;
};
type Zoom = "day" | "week" | "month" | "quarter";

const dateLabel = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit", year: "2-digit", timeZone: "UTC" });
const headerDay = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "short", timeZone: "UTC" });
const headerMonth = new Intl.DateTimeFormat("ru-RU", { month: "short", year: "numeric", timeZone: "UTC" });
const dayMs = 86_400_000;
const fileBase64 = async (file: File) => {
  const bytes = new Uint8Array(await file.arrayBuffer()); let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 32768) binary += String.fromCharCode(...bytes.subarray(offset, offset + 32768));
  return btoa(binary);
};

function asDate(value?: string) {
  return value ? new Date(`${value}T00:00:00Z`) : null;
}

function addDays(value: string, days: number) {
  const parsed = asDate(value);
  if (!parsed) return "";
  return new Date(parsed.getTime() + days * dayMs).toISOString().slice(0, 10);
}

function parsePredecessors(value?: string) {
  return (value || "").split(/[,;]+/).map((token) => Number(token.trim().match(/^\d+/)?.[0])).filter(Number.isFinite);
}

type LinkType = "FS" | "SS" | "FF" | "SF";
type Dependency = { predecessorId: number; type: LinkType; lag: number };

function parseDependencies(value?: string): Dependency[] {
  return (value || "").split(/[,;]+/).flatMap((token) => {
    const match = token.trim().match(/^(\d+)\s*(FS|SS|FF|SF)?\s*(?:([+-])\s*(\d+)\s*[dд])?$/i);
    if (!match) return [];
    return [{ predecessorId: Number(match[1]), type: (match[2]?.toUpperCase() || "FS") as LinkType, lag: Number(match[4] || 0) * (match[3] === "-" ? -1 : 1) }];
  });
}

function taskDepth(task: Task, byId: Map<number, Task>) {
  let depth = 0;
  let parent = task.parent_id ? byId.get(task.parent_id) : undefined;
  const seen = new Set<number>();
  while (parent && !seen.has(parent.id) && depth < 8) {
    seen.add(parent.id);
    depth += 1;
    parent = parent.parent_id ? byId.get(parent.parent_id) : undefined;
  }
  return depth;
}

function taskPath(task: Task, byId: Map<number, Task>) {
  const path = [task.title.trim().toLocaleLowerCase("ru")];
  let parent = task.parent_id ? byId.get(task.parent_id) : undefined;
  const seen = new Set<number>();
  while (parent && !seen.has(parent.id)) {
    seen.add(parent.id);
    path.unshift(parent.title.trim().toLocaleLowerCase("ru"));
    parent = parent.parent_id ? byId.get(parent.parent_id) : undefined;
  }
  return path.join(" / ");
}

function csvCell(value: unknown) {
  return `"${String(value ?? "").replaceAll('"', '""')}"`;
}

function dependencyOffset(predecessor: Task, successor: Task, link: Dependency) {
  const predecessorDuration = predecessor.is_milestone ? 0 : Math.max(1, predecessor.duration_days || 1);
  const successorDuration = successor.is_milestone ? 0 : Math.max(1, successor.duration_days || 1);
  if (link.type === "FS") return predecessorDuration + link.lag;
  if (link.type === "SS") return link.lag;
  if (link.type === "FF") return predecessorDuration - successorDuration + link.lag;
  return 1 - successorDuration + link.lag;
}

function networkAnalysis(tasks: Task[]) {
  const byId = new Map(tasks.map((task) => [task.id, task]));
  const remaining = new Set(tasks.map((task) => task.id));
  const ordered: Task[] = [];
  while (remaining.size) {
    const ready = tasks.filter((task) => remaining.has(task.id) && parsePredecessors(task.predecessor_ids).every((id) => !remaining.has(id)));
    if (!ready.length) break;
    ready.forEach((task) => { remaining.delete(task.id); ordered.push(task); });
  }
  const earliest = new Map<number, number>();
  for (const task of ordered) {
    let value = 0;
    for (const link of parseDependencies(task.predecessor_ids)) {
      const predecessor = byId.get(link.predecessorId);
      if (predecessor) value = Math.max(value, (earliest.get(predecessor.id) || 0) + dependencyOffset(predecessor, task, link));
    }
    earliest.set(task.id, value);
  }
  const projectFinish = Math.max(0, ...tasks.map((task) => (earliest.get(task.id) || 0) + (task.is_milestone ? 0 : Math.max(1, task.duration_days || 1))));
  const latest = new Map(tasks.map((task) => [task.id, projectFinish - (task.is_milestone ? 0 : Math.max(1, task.duration_days || 1))]));
  for (const task of [...ordered].reverse()) {
    for (const successor of tasks) {
      for (const link of parseDependencies(successor.predecessor_ids).filter((item) => item.predecessorId === task.id)) {
        latest.set(task.id, Math.min(latest.get(task.id)!, (latest.get(successor.id) || 0) - dependencyOffset(task, successor, link)));
      }
    }
  }
  const slack = new Map(tasks.map((task) => [task.id, Math.max(0, (latest.get(task.id) || 0) - (earliest.get(task.id) || 0))]));
  return { critical: new Set(tasks.filter((task) => slack.get(task.id) === 0).map((task) => task.id)), slack };
}

export function GprWorkspace({ projectId, finance, selectedContractId, onPrepare, onUpdateTask, onBulkUpdate, onCloneBaseline, onImported }: Props) {
  const baselines = (finance?.baselines || []).filter((baseline) => !selectedContractId || baseline.contract_id === selectedContractId);
  const [baselineId, setBaselineId] = useState(0);
  const [selectedId, setSelectedId] = useState(0);
  const [zoom, setZoom] = useState<Zoom>("week");
  const [showCritical, setShowCritical] = useState(true);
  const [collapsedIds, setCollapsedIds] = useState<Set<number>>(() => new Set());
  const [referenceId, setReferenceId] = useState(0);
  const [checkedIds, setCheckedIds] = useState<Set<number>>(() => new Set());
  const [dragPreview, setDragPreview] = useState<{ id: number; start: string; finish: string } | null>(null);
  const [mppFile, setMppFile] = useState<File | null>(null);
  const [mppPreview, setMppPreview] = useState<MppPreview | null>(null);
  const [mppBusy, setMppBusy] = useState(false);
  const [mppError, setMppError] = useState("");
  const mppInput = useRef<HTMLInputElement>(null);
  const chartRef = useRef<HTMLDivElement>(null);
  const currentBaselineId = baselines.some((item) => item.id === baselineId) ? baselineId : baselines[0]?.id || 0;
  const tasks = useMemo(() => (finance?.schedule || []).filter((task) => task.baseline_id === currentBaselineId).sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0) || a.id - b.id), [finance, currentBaselineId]);
  const selected = tasks.find((task) => task.id === selectedId) || tasks[0];
  const [draft, setDraft] = useState<Record<string, string | number | boolean>>({});
  useEffect(() => {
    if (!selected) { setDraft({}); return; }
    setSelectedId(selected.id);
    setDraft({ title: selected.title, planned_start: selected.planned_start || "", planned_finish: selected.planned_finish || "", duration_days: selected.duration_days ?? 1, predecessor_ids: selected.predecessor_ids || "", planned_progress: selected.planned_progress || 0, actual_progress: selected.actual_progress || 0, is_milestone: Boolean(selected.is_milestone), constraint_type: selected.constraint_type || "asap", constraint_date: selected.constraint_date || "" });
  }, [selected?.id, selected?.title, selected?.planned_start, selected?.planned_finish, selected?.duration_days, selected?.predecessor_ids, selected?.planned_progress, selected?.actual_progress, selected?.is_milestone, selected?.constraint_type, selected?.constraint_date]);
  const byId = useMemo(() => new Map(tasks.map((task) => [task.id, task])), [tasks]);
  const network = useMemo(() => networkAnalysis(tasks), [tasks]);
  const visibleTasks = useMemo(() => tasks.filter((task) => {
    let parentId = task.parent_id;
    const seen = new Set<number>();
    while (parentId && !seen.has(parentId)) {
      if (collapsedIds.has(parentId)) return false;
      seen.add(parentId);
      parentId = byId.get(parentId)?.parent_id;
    }
    return true;
  }), [tasks, byId, collapsedIds]);
  const childParents = useMemo(() => new Set(tasks.flatMap((task) => task.parent_id ? [task.parent_id] : [])), [tasks]);
  const referenceCandidates = baselines.filter((baseline) => baseline.id !== currentBaselineId && baseline.status === "approved");
  const referenceBaselineId = referenceCandidates.some((baseline) => baseline.id === referenceId) ? referenceId : referenceCandidates[0]?.id || 0;
  const referenceTasks = (finance?.schedule || []).filter((task) => task.baseline_id === referenceBaselineId);
  const referenceById = useMemo(() => new Map(referenceTasks.map((task) => [task.id, task])), [referenceTasks]);
  const referenceByPath = useMemo(() => new Map(referenceTasks.map((task) => [taskPath(task, referenceById), task])), [referenceTasks, referenceById]);
  const finishVariance = useMemo(() => new Map(tasks.map((task) => {
    const reference = referenceByPath.get(taskPath(task, byId));
    const currentFinish = asDate(task.planned_finish);
    const referenceFinish = asDate(reference?.planned_finish);
    return [task.id, currentFinish && referenceFinish ? Math.round((currentFinish.getTime() - referenceFinish.getTime()) / dayMs) : null];
  })), [tasks, byId, referenceByPath]);
  const dated = tasks.flatMap((task) => [asDate(task.planned_start), asDate(task.planned_finish)]).filter((value): value is Date => Boolean(value));
  const timelineStart = dated.length ? new Date(Math.min(...dated.map(Number))) : new Date();
  const timelineFinish = dated.length ? new Date(Math.max(...dated.map(Number))) : new Date(timelineStart.getTime() + 30 * dayMs);
  timelineStart.setUTCDate(timelineStart.getUTCDate() - 2);
  timelineFinish.setUTCDate(timelineFinish.getUTCDate() + 3);
  const pixelsPerDay = zoom === "day" ? 34 : zoom === "week" ? 12 : zoom === "month" ? 4 : 1.5;
  const totalDays = Math.max(1, Math.ceil((timelineFinish.getTime() - timelineStart.getTime()) / dayMs));
  const timelineWidth = Math.max(760, totalDays * pixelsPerDay);
  const ticks: Date[] = [];
  const step = zoom === "day" ? 1 : zoom === "week" ? 7 : zoom === "month" ? 30 : 90;
  for (let day = 0; day <= totalDays; day += step) ticks.push(new Date(timelineStart.getTime() + day * dayMs));
  const editable = baselines.find((item) => item.id === currentBaselineId)?.status !== "approved";

  async function save() {
    if (!selected) return;
    try {
      await onUpdateTask(selected.id, {
        ...draft,
        planned_start: draft.planned_start || null,
        planned_finish: draft.planned_finish || null,
        constraint_date: draft.constraint_date || null,
      });
    } catch { /* the controller presents the API message */ }
  }

  function beginBarDrag(task: Task, mode: "move" | "resize", event: ReactPointerEvent) {
    if (!editable || !task.planned_start || !task.planned_finish) return;
    event.preventDefault();
    event.stopPropagation();
    const originX = event.clientX;
    const originalStart = asDate(task.planned_start)!;
    const originalFinish = asDate(task.planned_finish)!;
    let preview = { id: task.id, start: task.planned_start, finish: task.planned_finish };
    const move = (pointerEvent: PointerEvent) => {
      const delta = Math.round((pointerEvent.clientX - originX) / pixelsPerDay);
      const start = mode === "move" ? new Date(originalStart.getTime() + delta * dayMs) : originalStart;
      const finish = new Date(originalFinish.getTime() + delta * dayMs);
      if (finish < start) return;
      preview = { id: task.id, start: start.toISOString().slice(0, 10), finish: finish.toISOString().slice(0, 10) };
      setDragPreview(preview);
    };
    const up = () => {
      document.removeEventListener("pointermove", move);
      document.removeEventListener("pointerup", up);
      setDragPreview(null);
      const durationDays = Math.max(0, Math.round((asDate(preview.finish)!.getTime() - asDate(preview.start)!.getTime()) / dayMs) + 1);
      void onUpdateTask(task.id, { planned_start: preview.start, planned_finish: preview.finish, duration_days: task.is_milestone ? 0 : durationDays });
    };
    document.addEventListener("pointermove", move);
    document.addEventListener("pointerup", up, { once: true });
  }

  async function indent(outdent = false) {
    if (!selected) return;
    const index = tasks.findIndex((task) => task.id === selected.id);
    const previous = tasks[index - 1];
    const parentId = outdent ? (selected.parent_id ? byId.get(selected.parent_id)?.parent_id || null : null) : previous?.id || null;
    await onUpdateTask(selected.id, { parent_id: parentId });
  }

  function scrollToToday() {
    const today = new Date();
    const utcToday = Date.UTC(today.getFullYear(), today.getMonth(), today.getDate());
    const left = (utcToday - timelineStart.getTime()) / dayMs * pixelsPerDay;
    chartRef.current?.scrollTo({ left: Math.max(0, left - chartRef.current.clientWidth / 2), behavior: "smooth" });
  }

  async function chooseMpp(file?: File) {
    if (!file) return;
    setMppFile(file); setMppPreview(null); setMppError(""); setMppBusy(true);
    try {
      const content_base64 = await fileBase64(file);
      setMppPreview(await api<MppPreview>("/execution/mpp/preview", {
        method: "POST",
        body: JSON.stringify({ project_id: projectId, contract_id: selectedContractId || null, filename: file.name, content_base64 }),
      }));
    } catch (error) {
      setMppError(error instanceof Error ? error.message : "Не удалось прочитать MPP-файл");
    } finally { setMppBusy(false); }
  }

  async function importMpp() {
    if (!mppFile) return;
    setMppError(""); setMppBusy(true);
    try {
      const content_base64 = await fileBase64(mppFile);
      const result = await api<{ baseline_id: number; created: number; duplicate: boolean }>("/execution/mpp/import", {
        method: "POST",
        body: JSON.stringify({ project_id: projectId, contract_id: selectedContractId || null, filename: mppFile.name, content_base64 }),
      });
      await onImported();
      setBaselineId(result.baseline_id); setSelectedId(0); setMppFile(null); setMppPreview(null);
      if (result.duplicate) setMppError("Этот файл уже импортирован — открыта существующая версия ГПР.");
      if (mppInput.current) mppInput.current.value = "";
    } catch (error) {
      setMppError(error instanceof Error ? error.message : "Не удалось импортировать MPP-файл");
    } finally { setMppBusy(false); }
  }

  function exportSchedule() {
    const headings = ["ID", "WBS", "Название", "Длительность", "Начало", "Окончание", "Предшественники", "План %", "Факт %", "Резерв, дн.", "Отклонение от baseline, дн."];
    const rows = tasks.map((task, index) => [task.id, index + 1, taskPath(task, byId), task.duration_days || 0, task.planned_start || "", task.planned_finish || "", task.predecessor_ids || "", task.planned_progress || 0, task.actual_progress || 0, network.slack.get(task.id) || 0, finishVariance.get(task.id) ?? ""]);
    const csv = `\uFEFF${[headings, ...rows].map((row) => row.map(csvCell).join(";")).join("\r\n")}`;
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `ГПР_${baselines.find((item) => item.id === currentBaselineId)?.name || currentBaselineId}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  }

  async function applyBulk(kind: "shift" | "plan" | "fact") {
    if (!checkedIds.size) return;
    const label = kind === "shift" ? "Сдвиг в календарных днях" : kind === "plan" ? "План выполнения, %" : "Факт выполнения, %";
    const raw = window.prompt(label, kind === "shift" ? "1" : "100");
    if (raw === null) return;
    const value = Number(raw);
    if (!Number.isFinite(value) || (kind !== "shift" && (value < 0 || value > 100))) return;
    await onBulkUpdate(currentBaselineId, Array.from(checkedIds), kind === "shift" ? { delta_days: Math.round(value) } : kind === "plan" ? { planned_progress: value } : { actual_progress: value });
    setCheckedIds(new Set());
  }

  async function cloneBaseline() {
    if (!currentBaselineId) return;
    const id = await onCloneBaseline(currentBaselineId);
    if (id) { setBaselineId(id); setSelectedId(0); setCheckedIds(new Set()); }
  }

  const displayDates = new Map(tasks.map((task) => [task.id, dragPreview?.id === task.id ? dragPreview : { start: task.planned_start || "", finish: task.planned_finish || "" }]));
  const dateX = (value: string, finish = false) => {
    const parsed = asDate(value);
    return parsed ? (parsed.getTime() - timelineStart.getTime()) / dayMs * pixelsPerDay + (finish ? pixelsPerDay : 0) : 0;
  };
  const arrows = visibleTasks.flatMap((task, rowIndex) => parseDependencies(task.predecessor_ids).flatMap((link) => {
    const predecessorIndex = visibleTasks.findIndex((candidate) => candidate.id === link.predecessorId);
    const predecessor = byId.get(link.predecessorId);
    if (!predecessor || predecessorIndex < 0) return [];
    const predDates = displayDates.get(predecessor.id)!;
    const taskDates = displayDates.get(task.id)!;
    const fromFinish = link.type === "FS" || link.type === "FF";
    const toFinish = link.type === "FF" || link.type === "SF";
    const x1 = dateX(fromFinish ? predDates.finish : predDates.start, fromFinish);
    const x2 = dateX(toFinish ? taskDates.finish : taskDates.start, toFinish);
    if (!x1 || !x2) return [];
    const y1 = 36 + predecessorIndex * 36 + 18;
    const y2 = 36 + rowIndex * 36 + 18;
    const bend = x1 + Math.max(8, Math.min(24, (x2 - x1) / 2));
    return [{ key: `${predecessor.id}-${task.id}-${link.type}`, path: `M ${x1} ${y1} H ${bend} V ${y2} H ${x2}`, critical: network.critical.has(predecessor.id) && network.critical.has(task.id), label: `${predecessor.id}${link.type}${link.lag ? `${link.lag > 0 ? "+" : ""}${link.lag}д` : ""}` }];
  }));

  return <section className="card gpr-workspace">
    <div className="gpr-head"><div><span className="eyebrow">КАЛЕНДАРНО-СЕТЕВОЕ ПЛАНИРОВАНИЕ</span><h2>График работ</h2><p>Иерархия задач, зависимости, план/факт и диаграмма Ганта в одном рабочем поле.</p></div><div className="gpr-controls"><select aria-label="Версия ГПР" value={currentBaselineId} onChange={(event) => { setBaselineId(Number(event.target.value)); setSelectedId(0); }}>{baselines.map((item) => <option value={item.id} key={item.id}>v{item.version} · {item.name} · {item.status}</option>)}</select><input ref={mppInput} hidden type="file" accept=".mpp" onChange={(event) => void chooseMpp(event.target.files?.[0])} /><button type="button" className="secondary" disabled={mppBusy} onClick={() => mppInput.current?.click()}><Upload />{mppBusy ? "Читаю…" : "Импорт .mpp"}</button><button type="button" onClick={() => onPrepare(currentBaselineId ? "schedule" : "baseline", currentBaselineId)}><Plus />{currentBaselineId ? "Задача" : "Версия ГПР"}</button></div></div>
    {mppError && <div className={`gpr-import-message ${mppPreview ? "" : "error"}`}>{mppError}</div>}
    {mppPreview && <div className="gpr-import-preview"><div><strong>{mppPreview.filename}</strong><span>{mppPreview.task_count} задач · {mppPreview.relation_count} связей · {mppPreview.summary_count} сводных · {mppPreview.milestone_count} вех · {mppPreview.critical_count} критических</span><small>{mppPreview.planned_start || "без даты"} — {mppPreview.planned_finish || "без даты"}. Исходный файл не изменяется.</small></div><div><button className="secondary" type="button" onClick={() => { setMppFile(null); setMppPreview(null); setMppError(""); if (mppInput.current) mppInput.current.value = ""; }}>Отмена</button><button type="button" disabled={mppBusy} onClick={() => void importMpp()}>{mppBusy ? "Импорт…" : "Создать ГПР"}</button></div></div>}
    <div className="gpr-toolbar"><button disabled={!editable || !selected} onClick={() => void indent()} title="Сделать подзадачей"><ArrowRightToLine /> Отступ</button><button disabled={!editable || !selected?.parent_id} onClick={() => void indent(true)} title="Поднять уровень"><ArrowLeftToLine /> Выступ</button><button disabled={!editable || !selected} onClick={() => selected && void onUpdateTask(selected.id, { is_milestone: !selected.is_milestone })}><Diamond /> Веха</button><button type="button" onClick={() => void cloneBaseline()} title="Создать редактируемую копию версии"><Copy /> Новая версия</button><label><input type="checkbox" checked={showCritical} onChange={(event) => setShowCritical(event.target.checked)} /> Критический путь</label><label>Сравнить <select aria-label="Baseline для сравнения" value={referenceBaselineId} onChange={(event) => setReferenceId(Number(event.target.value))}><option value={0}>без baseline</option>{referenceCandidates.map((item) => <option value={item.id} key={item.id}>v{item.version} · {item.name}</option>)}</select></label><span></span><button type="button" onClick={scrollToToday}><LocateFixed /> Сегодня</button><button type="button" onClick={exportSchedule}><Download /> CSV</button><label>Масштаб <select value={zoom} onChange={(event) => setZoom(event.target.value as Zoom)}><option value="day">День</option><option value="week">Неделя</option><option value="month">Месяц</option><option value="quarter">Квартал</option></select></label></div>
    {checkedIds.size > 0 && <div className="gpr-bulk"><strong>Выбрано: {checkedIds.size}</strong><button disabled={!editable} onClick={() => void applyBulk("shift")}>Сдвинуть даты</button><button disabled={!editable} onClick={() => void applyBulk("plan")}>План, %</button><button onClick={() => void applyBulk("fact")}>Факт, %</button><button className="secondary" onClick={() => setCheckedIds(new Set())}>Снять выбор</button></div>}
    <div className="gpr-split">
      <div className="gpr-grid"><table><thead><tr><th><input aria-label="Выбрать все видимые задачи" type="checkbox" checked={visibleTasks.length > 0 && visibleTasks.every((task) => checkedIds.has(task.id))} onChange={(event) => setCheckedIds(event.target.checked ? new Set(visibleTasks.map((task) => task.id)) : new Set())} /></th><th>№</th><th>Название задачи</th><th>Длительность</th><th>Начало</th><th>Окончание</th><th>Предш.</th><th>Резерв</th>{referenceBaselineId > 0 && <th>Δ срок</th>}<th>%</th></tr></thead><tbody>{visibleTasks.map((task) => <tr className={`${selected?.id === task.id ? "selected" : ""} ${showCritical && network.critical.has(task.id) ? "critical" : ""}`} onClick={() => setSelectedId(task.id)} key={task.id}><td><input aria-label={`Выбрать задачу ${task.title}`} type="checkbox" checked={checkedIds.has(task.id)} onClick={(event) => event.stopPropagation()} onChange={(event) => setCheckedIds((current) => { const next = new Set(current); if (event.target.checked) next.add(task.id); else next.delete(task.id); return next; })} /></td><td>{tasks.indexOf(task) + 1}</td><td style={{ paddingLeft: 10 + taskDepth(task, byId) * 18 }}>{childParents.has(task.id) ? <button className="gpr-collapse" aria-label={collapsedIds.has(task.id) ? "Развернуть ветвь" : "Свернуть ветвь"} onClick={(event) => { event.stopPropagation(); setCollapsedIds((current) => { const next = new Set(current); if (next.has(task.id)) next.delete(task.id); else next.add(task.id); return next; }); }}>{collapsedIds.has(task.id) ? <ChevronRight /> : <ChevronDown />}</button> : task.parent_id ? <span className="gpr-tree">└</span> : <span className="gpr-tree-spacer"></span>}{task.is_milestone && <Diamond className="gpr-milestone-icon" />}{task.title}</td><td>{task.is_milestone ? "0 дн." : `${task.duration_days ?? 1} дн.`}</td><td>{task.planned_start ? dateLabel.format(asDate(task.planned_start)!) : "—"}</td><td>{task.planned_finish ? dateLabel.format(asDate(task.planned_finish)!) : "—"}</td><td>{task.predecessor_ids || "—"}</td><td>{network.slack.get(task.id) || 0} дн.</td>{referenceBaselineId > 0 && <td className={(finishVariance.get(task.id) || 0) > 0 ? "gpr-delay" : ""}>{finishVariance.get(task.id) == null ? "—" : `${finishVariance.get(task.id)! > 0 ? "+" : ""}${finishVariance.get(task.id)} дн.`}</td>}<td>{task.actual_progress || task.planned_progress || 0}%</td></tr>)}</tbody></table>{!tasks.length && <p className="gpr-empty">Создайте версию ГПР и добавьте первую задачу.</p>}</div>
      <div className="gpr-chart" ref={chartRef}><div className="gpr-timeline" style={{ width: timelineWidth }}><div className="gpr-timescale">{ticks.map((tick) => <span style={{ left: Math.round((tick.getTime() - timelineStart.getTime()) / dayMs * pixelsPerDay), width: step * pixelsPerDay }} key={tick.toISOString()}>{zoom === "quarter" ? `${Math.floor(tick.getUTCMonth() / 3) + 1} кв. ${tick.getUTCFullYear()}` : zoom === "month" ? headerMonth.format(tick) : headerDay.format(tick)}</span>)}</div>{visibleTasks.map((task) => {
        const dates = displayDates.get(task.id)!;
        const start = asDate(dates.start || dates.finish);
        const finish = asDate(dates.finish || dates.start);
        const left = start ? Math.max(0, (start.getTime() - timelineStart.getTime()) / dayMs * pixelsPerDay) : 0;
        const width = start && finish ? Math.max(task.is_milestone ? 12 : 4, ((finish.getTime() - start.getTime()) / dayMs + 1) * pixelsPerDay) : 0;
        return <div className={`gpr-chart-row ${selected?.id === task.id ? "selected" : ""}`} onClick={() => setSelectedId(task.id)} key={task.id}>{start && (task.is_milestone ? <span className="gpr-diamond" style={{ left }} title={task.title} onPointerDown={(event) => beginBarDrag(task, "move", event)}></span> : <span className={`gpr-bar ${showCritical && network.critical.has(task.id) ? "critical" : ""} ${dragPreview?.id === task.id ? "dragging" : ""}`} style={{ left, width }} title={`${task.title}: ${dates.start} — ${dates.finish}`} onPointerDown={(event) => beginBarDrag(task, "move", event)}><i style={{ width: `${Math.min(100, task.actual_progress || 0)}%` }}></i>{editable && <b className="gpr-resize" title="Изменить длительность" onPointerDown={(event) => beginBarDrag(task, "resize", event)}></b>}</span>)}</div>;
      })}<svg className="gpr-links" width={timelineWidth} height={36 + visibleTasks.length * 36} aria-label="Связи задач"><defs><marker id="gpr-arrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7 Z" /></marker></defs>{arrows.map((arrow) => <path key={arrow.key} d={arrow.path} className={showCritical && arrow.critical ? "critical" : ""}><title>{arrow.label}</title></path>)}</svg></div></div>
    </div>
    {selected && <div className="gpr-editor"><div><strong>Задача #{selected.id}</strong><small>{editable ? "Черновая версия · резерв " + (network.slack.get(selected.id) || 0) + " дн." : "Утверждённый baseline доступен только для просмотра"}</small></div><input aria-label="Название задачи" disabled={!editable} value={String(draft.title || "")} onChange={(event) => setDraft((value) => ({ ...value, title: event.target.value }))} /><input aria-label="Начало" disabled={!editable} type="date" value={String(draft.planned_start || "")} onChange={(event) => setDraft((value) => ({ ...value, planned_start: event.target.value, planned_finish: event.target.value && Number(value.duration_days) > 0 ? addDays(event.target.value, Number(value.duration_days) - 1) : value.planned_finish }))} /><input aria-label="Окончание" disabled={!editable} type="date" value={String(draft.planned_finish || "")} onChange={(event) => setDraft((value) => ({ ...value, planned_finish: event.target.value, duration_days: event.target.value && value.planned_start ? Math.max(1, Math.round((asDate(event.target.value)!.getTime() - asDate(String(value.planned_start))!.getTime()) / dayMs) + 1) : value.duration_days }))} /><input aria-label="Длительность" disabled={!editable} type="number" min="0" value={Number(draft.duration_days || 0)} onChange={(event) => setDraft((value) => ({ ...value, duration_days: Number(event.target.value), planned_finish: value.planned_start ? addDays(String(value.planned_start), Math.max(0, Number(event.target.value) - 1)) : value.planned_finish }))} /><input aria-label="Предшественники" disabled={!editable} value={String(draft.predecessor_ids || "")} onChange={(event) => setDraft((value) => ({ ...value, predecessor_ids: event.target.value }))} placeholder="12FS+2д, 15SS" /><select aria-label="Ограничение" disabled={!editable} value={String(draft.constraint_type || "asap")} onChange={(event) => setDraft((value) => ({ ...value, constraint_type: event.target.value }))}><option value="asap">Как можно раньше</option><option value="mso">Фикс. начало</option><option value="mfo">Фикс. окончание</option><option value="snet">Начать не ранее</option><option value="fnet">Закончить не ранее</option><option value="snlt">Начать не позднее</option><option value="fnlt">Закончить не позднее</option></select><input aria-label="Дата ограничения" disabled={!editable || draft.constraint_type === "asap"} type="date" value={String(draft.constraint_date || "")} onChange={(event) => setDraft((value) => ({ ...value, constraint_date: event.target.value }))} /><input aria-label="План выполнения" disabled={!editable} type="number" min="0" max="100" value={Number(draft.planned_progress || 0)} onChange={(event) => setDraft((value) => ({ ...value, planned_progress: Number(event.target.value) }))} /><input aria-label="Факт выполнения" type="number" min="0" max="100" value={Number(draft.actual_progress || 0)} onChange={(event) => setDraft((value) => ({ ...value, actual_progress: Number(event.target.value) }))} /><button type="button" onClick={() => void save()}><Save /> Сохранить</button></div>}
    <footer className="gpr-note"><CalendarClock /> Красным показан критический путь (нулевой полный резерв). Полосы можно переносить и растягивать; связанные задачи пересчитываются автоматически.</footer>
  </section>;
}
