import { useEffect, useRef, useState } from "react";
import { constraints, parseGraph, putPayload, sameItems, toDraft, type Constraint, type Draft, type DraftItem, type Graph } from "./graphReadModel";
import "./scheduleGraph.css";
import { SchedulePlanSummary } from "./SchedulePlanSummary";

export type GraphApi = (path: string, options?: RequestInit) => Promise<unknown>;
export type ScheduleGraphEditorProps = {
  projectId: number; baselineId: number; api: GraphApi; canEdit: boolean; canApprove?: boolean;
  onSaved?: (baselineId: number, graphRevision: number) => void;
};
const constraintNames: Record<Constraint, string> = {
  asap: "Как можно раньше", snet: "Начать не раньше", fnet: "Закончить не раньше",
  snlt: "Начать не позже", fnlt: "Закончить не позже", mso: "Начать точно", mfo: "Закончить точно",
};
const statusOf = (error: unknown) => typeof error === "object" && error !== null && "status" in error ? error.status : null;

/** Remount the request/draft state on project/baseline change; late responses are discarded. */
export function ScheduleGraphEditor(props: ScheduleGraphEditorProps) {
  if (!Number.isSafeInteger(props.projectId) || props.projectId < 1 || !Number.isSafeInteger(props.baselineId) || props.baselineId < 1)
    return <p role="status">Выберите проект и версию ГПР.</p>;
  return <GraphSession key={`${props.projectId}:${props.baselineId}`} {...props} />;
}
function GraphSession({ baselineId, api, canEdit, canApprove = false, onSaved }: ScheduleGraphEditorProps) {
  const [graph, setGraph] = useState<Graph | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [latest, setLatest] = useState<Graph | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [conflict, setConflict] = useState(false);
  const [denied, setDenied] = useState(false);
  const [approvalReview, setApprovalReview] = useState(false);
  const alive = useRef(true);
  const request = useRef(0);
  const lock = useRef(false);
  const endpoint = `/execution/baselines/${baselineId}/graph`;
  // api identity may be an inline adapter. Avoid resetting a local draft on parent renders.
  const apiRef = useRef(api); apiRef.current = api;
  useEffect(() => {
    alive.current = true;
    const controller = new AbortController(); const ticket = ++request.current;
    apiRef.current(endpoint, { signal: controller.signal }).then(value => {
      if (!alive.current || ticket !== request.current) return;
      const result = parseGraph(value, baselineId); setGraph(result); setDraft(toDraft(result));
    }).catch(() => { if (alive.current && ticket === request.current) setError("Не удалось загрузить ГПР. Повторите загрузку."); })
      .finally(() => { if (alive.current && ticket === request.current) setBusy(false); });
    return () => { alive.current = false; controller.abort(); ++request.current; };
  }, [baselineId, endpoint]);

  async function reload() {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError(""); setApprovalReview(false); const ticket = ++request.current;
    try {
      const result = parseGraph(await apiRef.current(endpoint), baselineId);
      if (!alive.current || ticket !== request.current) return;
      if (graph && result.graph_revision < graph.graph_revision) throw new Error("stale_graph_response");
      if (draft) { setLatest(result); setConflict(true); setNotice("Серверная версия загружена отдельно. Локальные правки сохранены."); }
      else { setGraph(result); setDraft(toDraft(result)); }
      setDenied(false);
    } catch (e) {
      if (alive.current && ticket === request.current) {
        setError("Не удалось обновить ГПР. Локальные правки не отправлены.");
        setConflict(true); setLatest(null);
        if ([401, 403, 404].includes(Number(statusOf(e)))) setDenied(true);
      }
    } finally { lock.current = false; if (alive.current && ticket === request.current) setBusy(false); }
  }
  async function save() {
    if (!graph || !draft || lock.current || !canEdit || conflict || denied || graph.status !== "draft") return;
    let payload;
    try { payload = putPayload(draft, graph); } catch { setError("Проверьте начало проекта, длительности, вехи и даты ограничений."); return; }
    lock.current = true; setBusy(true); setError(""); setNotice(""); setApprovalReview(false); const ticket = ++request.current;
    try {
      const result = parseGraph(await apiRef.current(endpoint, { method: "PUT", body: JSON.stringify(payload) }), baselineId);
      if (!alive.current || ticket !== request.current) return;
      if (result.graph_revision <= graph.graph_revision) throw new Error("stale_graph_response");
      setGraph(result); setDraft(toDraft(result)); setLatest(null); setNotice("Граф сохранён. Даты рассчитаны сервером.");
      onSaved?.(baselineId, result.graph_revision);
    } catch (e) {
      if (!alive.current || ticket !== request.current) return;
      if (statusOf(e) === 422) setError("Граф отклонён: проверьте зависимости, циклы и совместимость ограничений. Правки сохранены локально.");
      else {
        setConflict(true); setLatest(null);
        setError(statusOf(e) === 409 ? "Версия ГПР изменилась. Загрузите серверную версию и сравните правки." : "Результат сохранения не подтверждён. Обновите серверную версию перед повтором.");
        if ([401, 403, 404].includes(Number(statusOf(e)))) setDenied(true);
      }
    } finally { lock.current = false; if (alive.current && ticket === request.current) setBusy(false); }
  }
  function change(id: number, patch: Partial<DraftItem>) {
    setDraft(current => current && ({ ...current, items: current.items.map(item => item.id === id ? { ...item, ...patch } : item) }));
    setNotice(""); setApprovalReview(false);
  }
  const editable = canEdit && graph?.status === "draft" && !busy && !denied;
  const dirty = !!graph && !!draft && JSON.stringify(draft) !== JSON.stringify(toDraft(graph));
  const approvable = canApprove && graph?.status === "draft" && graph.planning_mode === "calendar_graph" && !dirty && !busy && !denied && !conflict;
  async function approve() {
    if (!approvable || !approvalReview || !graph || lock.current) return;
    lock.current = true; setBusy(true); setError(""); setApprovalReview(false); const ticket = ++request.current;
    try {
      await apiRef.current(`/execution/baselines/${baselineId}/status`, { method: "PATCH", body: JSON.stringify({
        status: "approved", expected_status: "draft", expected_graph_revision: graph.graph_revision,
      }) });
      if (!alive.current || ticket !== request.current) return;
      const result = parseGraph(await apiRef.current(endpoint), baselineId);
      if (!alive.current || ticket !== request.current) return;
      if (result.graph_revision <= graph.graph_revision) throw new Error("stale_graph_response");
      setGraph(result); setDraft(toDraft(result)); setNotice("Статус версии повторно получен с сервера.");
      onSaved?.(baselineId, result.graph_revision);
    } catch (e) {
      if (alive.current && ticket === request.current) {
        setConflict(true); setLatest(null); setError("Утверждение не подтверждено. Обновите серверную версию; автоматический повтор отключён.");
        if ([401, 403, 404].includes(Number(statusOf(e)))) setDenied(true);
      }
    } finally { lock.current = false; if (alive.current && ticket === request.current) setBusy(false); }
  }
  return <section className="card schedule-graph" aria-label="Редактор графа ГПР" aria-busy={busy}>
    <header><h2>Граф ГПР</h2><button type="button" className="secondary" disabled={busy} onClick={() => void reload()}>Обновить серверную версию</button></header>
    {busy && <p role="status">Загрузка / сохранение ГПР…</p>}
    {error && <p role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {denied && <p role="alert">Доступ к версии недоступен. Редактирование скрыто до повторной проверки доступа.</p>}
    {graph && draft && !denied && <>
      <p>Версия {graph.version} · ревизия графа {graph.graph_revision} · {graph.status === "draft" ? "Черновик" : "Только чтение — создайте черновик в реестре ГПР"}</p>
      {!canEdit && <p>Доступ только для просмотра.</p>}
      <p>Календарные дни, включая день начала. Веха — 0 дней. Рабочие календари не поддерживаются. Даты ниже — последний сохранённый расчёт, не прогноз локальных правок.</p>
      <SchedulePlanSummary graph={graph} stale={conflict || busy} hasLocalEdits={dirty} />
      {conflict && <aside aria-label="Конфликт версий">
        <p>Автоматическая перезапись отключена. Локальные изменения остаются в форме.</p>
        {latest && <>
          <p>Сервер: версия {latest.version}, ревизия {latest.graph_revision}, статус {latest.status}, начало {latest.project_start || "не задано"}.</p>
          <div className="schedule-graph-scroll"><table><caption>Новая серверная версия для сравнения</caption><thead><tr><th>Этап</th><th>Длительность / веха</th><th>Зависимости</th><th>Ограничения</th><th>Расчётные даты</th></tr></thead><tbody>{latest.items.map(v => <tr key={v.id}><th>{v.title} (#{v.id})</th><td>{v.duration_days ?? "—"} / {v.is_milestone ? "да" : "нет"}</td><td>{v.predecessor_ids || "—"}</td><td>{v.constraint_type || "—"} {v.constraint_date} / не раньше {v.not_before_date || "—"}</td><td>{v.planned_start || "—"} → {v.planned_finish || "—"}</td></tr>)}</tbody></table></div>
          <button type="button" disabled={busy || !canEdit || latest.status !== "draft" || !sameItems(draft, latest)} onClick={() => {
            setGraph(latest); setLatest(null); setConflict(false); setError(""); setNotice("Локальные правки оставлены. Проверьте их и отдельно нажмите «Сохранить и рассчитать».");
          }}>Оставить мои правки поверх новой ревизии</button>
          <button type="button" className="secondary" disabled={busy} onClick={() => {
            setGraph(latest); setDraft(toDraft(latest)); setLatest(null); setConflict(false); setError(""); setNotice("Локальные правки заменены выбранной серверной версией.");
          }}>Отбросить мои правки и принять серверную версию</button>
          {!sameItems(draft, latest) && <p>Состав этапов изменился. Перенос всех локальных правок заблокирован; сохраните нужные значения перед принятием серверной версии.</p>}
        </>}
      </aside>}
      <form onSubmit={event => { event.preventDefault(); void save(); }}>
        <fieldset disabled={!editable}><legend>Параметры расчёта</legend>
          <label>Начало проекта<input type="date" required value={draft.anchor} onChange={e => { setDraft({ ...draft, anchor: e.target.value }); setNotice(""); setApprovalReview(false); }} /></label>
          {!draft.items.length && <p>Этапов пока нет. Добавьте этапы в существующем реестре ГПР, затем обновите эту версию.</p>}
          <div className="schedule-graph-scroll"><table><caption>Полный граф существующих этапов</caption><thead><tr><th>Этап</th><th>Длительность</th><th>Веха</th><th>Зависимости</th><th>Ограничение</th><th>Не раньше</th><th>Сохранённый расчёт</th></tr></thead><tbody>{draft.items.map(v => {
            const row = graph.items.find(item => item.id === v.id)!;
            return <tr key={v.id}><th scope="row">{row.title}<small>#{v.id}</small></th>
              <td><input aria-label={`Длительность #${v.id}`} type="number" required min={v.milestone ? 0 : 1} max={10000} step={1} disabled={v.milestone} value={v.duration} onChange={e => change(v.id, { duration: e.target.value })} /></td>
              <td><input aria-label={`Веха #${v.id}`} type="checkbox" checked={v.milestone} onChange={e => change(v.id, { milestone: e.target.checked, duration: e.target.checked ? "0" : "1" })} /></td>
              <td><input aria-label={`Зависимости #${v.id}`} maxLength={2000} placeholder="12FS+2d; 7SS-1d" value={v.dependencies} onChange={e => change(v.id, { dependencies: e.target.value })} /></td>
              <td><select aria-label={`Ограничение #${v.id}`} value={v.constraint} onChange={e => change(v.id, { constraint: e.target.value as Constraint, constraintDate: e.target.value === "asap" ? "" : v.constraintDate })}>{constraints.map(c => <option key={c} value={c}>{constraintNames[c]}</option>)}</select>
                {v.constraint !== "asap" && <input aria-label={`Дата ограничения #${v.id}`} type="date" required value={v.constraintDate} onChange={e => change(v.id, { constraintDate: e.target.value })} />}</td>
              <td><input aria-label={`Не раньше #${v.id}`} type="date" value={v.notBefore} onChange={e => change(v.id, { notBefore: e.target.value })} /></td>
              <td>{row.planned_start || "—"} → {row.planned_finish || "—"}</td></tr>;
          })}</tbody></table></div>
          <p>Связи: FS — окончание → начало (следующий день); SS — начало → начало; FF — окончание → окончание; SF — начало → окончание. Лаг: +2d или −1d. Указывайте ID этапа, а не номер строки.</p>
        </fieldset>
        <button type="submit" disabled={!editable || conflict}>Сохранить и рассчитать</button>
      </form>
      {canApprove && graph.status === "draft" && <div>
        <button type="button" disabled={!approvable} onClick={() => setApprovalReview(true)}>Проверить перед утверждением</button>
        {dirty && <p>Сначала сохраните и проверьте рассчитанные сервером даты.</p>}
        {approvalReview && <div role="group" aria-label="Подтверждение утверждения ГПР"><p>Утвердить версию {graph.version}, ревизию {graph.graph_revision}? План станет неизменяемым; прежняя утверждённая версия перейдёт в историю.</p>
          <button type="button" disabled={!approvable} onClick={() => void approve()}>Подтверждаю утверждение этой ревизии</button>
          <button type="button" className="secondary" onClick={() => setApprovalReview(false)}>Отмена утверждения</button>
        </div>}
      </div>}
    </>}
  </section>;
}
