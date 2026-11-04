import { useCallback, useEffect, useRef, useState } from "react";
import { csrfHeaders } from "../../api/client";
import { api, ApiError } from "../../api/client";
import { isOnline } from "../../offline/connectivity";
import { OfflineSessionChanged } from "../../offline/lifecycle";
import { enqueueWorkLogCreate, enqueuePreparedWorkLogCreate, prepareWorkLogCreate, submitPreparedWorkLogCreate, WORK_LOG_APPLIED_EVENT, type PreparedWorkLogCreate } from "../../offline/syncEngine";
import "./work-log.css";

type Entry = {
  id: number;
  work_date: string;
  object_name: string | null;
  work_name: string;
  unit: string | null;
  qty: string | null;
  location: string | null;
  weather: string | null;
  workers: number | null;
  hours: string | null;
  crew_id: number | null;
  note: string | null;
  labor?: LaborRow[];
};
type LaborRow = { person_name: string; hours: string; hourly_rate: string | null };
type Crew = { id: number; name: string; foreman: string | null; members: string; members_count: number; note: string | null };

type Summary = { entries: number; days: number; by_work: { work_name: string; unit: string | null; qty_total: string; entries: number }[] };
type EstimateLineHint = { id: number; position: string | null; code: string | null; name: string; unit: string | null; qty: string | null };
type Suggestions = { works: { work_name: string; unit: string | null }[]; objects: string[]; rd_items: { id: number; name: string; unit: string | null }[]; estimate_lines?: EstimateLineHint[] };
type ListResponse = { entries: Entry[]; summary: Summary };
type PlanItem = {
  id: number; title: string; is_group: boolean; is_milestone: boolean;
  planned_start: string | null; planned_finish: string | null;
  planned_progress: number; actual_progress: number; overdue: boolean;
  fact_entries: number; fact_qty: string | null; fact_unit: string | null;
  fact_first: string | null; fact_last: string | null;
};
type Schedule = { baseline: { id: number; name: string; version: number; status: string } | null; items: PlanItem[] };

const today = () => new Date().toISOString().slice(0, 10);

function formatDate(value: string): string {
  const [y, m, d] = value.split("-");
  return d && m && y ? `${d}.${m}.${y}` : value;
}

const emptyForm = () => ({ work_date: today(), object_name: "", work_name: "", unit: "", qty: "", location: "", weather: "", workers: "", hours: "", crew_id: "", note: "" });

export function WorkLogModule({ projectId, userId = 0 }: { projectId: number; userId?: number }) {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [suggest, setSuggest] = useState<Suggestions>({ works: [], objects: [], rd_items: [] });
  const [form, setForm] = useState(emptyForm);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [tick, setTick] = useState(0);
  const [plan, setPlan] = useState<Schedule>({ baseline: null, items: [] });
  const [selected, setSelected] = useState<PlanItem | null>(null);
  const [formHeader, setFormHeader] = useState<Record<string, string>>({});
  const [view, setView] = useState<"all" | "plan" | "crews">("all");
  const [crews, setCrews] = useState<Crew[]>([]);
  const [estimateLineId, setEstimateLineId] = useState("");
  const [labor, setLabor] = useState<LaborRow[]>([]);
  const [crewForm, setCrewForm] = useState({ id: 0, name: "", foreman: "", members: "" });
  const [pendingCommands, setPendingCommands] = useState<Record<string, PreparedWorkLogCreate>>({});
  const scope = `${userId}:${projectId}`;
  const liveScope = useRef(scope);
  liveScope.current = scope;
  const pending = pendingCommands[scope];
  const latestBody = useRef<() => Record<string, unknown>>(() => ({}));
  const lastQueued = useRef<{ scope: string; id: string } | null>(null);

  useEffect(() => {
    const applied = (event: Event) => {
      const detail = (event as CustomEvent<{ userId: number; projectId: number; clientMutationId?: string }>).detail;
      if (detail?.userId === userId && detail.projectId === projectId) {
        setTick((value) => value + 1);
        if (lastQueued.current?.scope === scope && lastQueued.current.id === detail.clientMutationId) {
          setMessage("Запись добавлена");
          lastQueued.current = null;
        }
      }
    };
    window.addEventListener(WORK_LOG_APPLIED_EVENT, applied);
    return () => window.removeEventListener(WORK_LOG_APPLIED_EVENT, applied);
  }, [userId, projectId]);

  useEffect(() => {
    setEntries([]);
    setSummary(null);
    setCrews([]);
    setPlan({ baseline: null, items: [] });
    setSuggest({ works: [], objects: [], rd_items: [] });
  }, [userId, projectId]);

  useEffect(() => {
    if (projectId <= 0) return;
    let alive = true;
    api<{ crews: Crew[] }>(`/projects/${projectId}/work-log/crews`)
      .then((res) => { if (alive && res && Array.isArray(res.crews)) setCrews(res.crews); })
      .catch(() => undefined);
    return () => { alive = false; };
  }, [userId, projectId, tick]);

  useEffect(() => {
    if (projectId <= 0) return;
    let alive = true;
    api<Schedule>(`/projects/${projectId}/work-log/schedule`)
      .then((res) => { if (alive && res && Array.isArray(res.items)) setPlan(res); })
      .catch(() => undefined);
    return () => { alive = false; };
  }, [userId, projectId, tick]);

  useEffect(() => {
    if (projectId <= 0) return;
    let alive = true;
    setLoading(true);
    const suffix = query.trim() ? `?q=${encodeURIComponent(query.trim())}` : "";
    api<ListResponse>(`/projects/${projectId}/work-log${suffix}`)
      .then((res) => { if (alive) { setEntries(res?.entries ?? []); setSummary(res?.summary ?? null); setError(""); } })
      .catch((err: unknown) => { if (alive) setError(err instanceof Error ? err.message : "Не удалось загрузить журнал"); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [userId, projectId, query, tick]);

  useEffect(() => {
    if (projectId <= 0) return;
    let alive = true;
    api<Suggestions>(`/projects/${projectId}/work-log/suggestions`)
      .then((res) => { if (alive && res) setSuggest(res); })
      .catch(() => undefined);
    return () => { alive = false; };
  }, [userId, projectId, tick]);

  const set = (field: keyof ReturnType<typeof emptyForm>, value: string) => setForm((f) => ({ ...f, [field]: value }));

  function pickWork(name: string) {
    const known = suggest.works.find((w) => w.work_name === name) ?? suggest.rd_items.find((i) => i.name === name);
    setForm((f) => ({ ...f, work_name: name, unit: f.unit || (known?.unit ?? "") }));
  }

  const invalidReferences = Boolean(
    (selected && !plan.items.some((item) => item.id === selected.id)) ||
    (form.crew_id && !crews.some((crew) => String(crew.id) === form.crew_id)) ||
    (estimateLineId && !suggest.estimate_lines?.some((line) => String(line.id) === estimateLineId))
  );

  function clearReferences() {
    setSelected(null);
    setEstimateLineId("");
    setForm((current) => ({ ...current, crew_id: "" }));
  }

  function workBody(): Record<string, unknown> {
    if (invalidReferences) throw new Error("Выберите привязки заново для текущего проекта или снимите их.");
    const workName = selected ? selected.title : form.work_name.trim();
    if (!workName) throw new Error("Укажите, какая работа выполнена");
    const body: Record<string, unknown> = { work_date: form.work_date, work_name: workName };
    if (selected) body.schedule_item_id = selected.id;
    if (estimateLineId) body.estimate_line_id = Number(estimateLineId);
    for (const key of ["object_name", "unit", "qty", "location", "weather", "note"] as const) {
      if (form[key].trim()) body[key] = form[key].trim();
    }
    if (form.workers.trim()) body.workers = Number(form.workers);
    if (form.hours.trim()) body.hours = form.hours.trim();
    if (form.crew_id) body.crew_id = Number(form.crew_id);
    const people = labor.filter((r) => r.person_name.trim() && r.hours.trim());
    if (people.length) body.labor = people.map((r) => ({ person_name: r.person_name.trim(), hours: r.hours.trim(), ...(r.hourly_rate ? { hourly_rate: r.hourly_rate } : {}) }));
    return body;
  }
  latestBody.current = workBody;

  function clearSubmitted(data: Record<string, unknown>, commandScope: string) {
    if (liveScope.current !== commandScope) return;
    try { if (JSON.stringify(latestBody.current()) !== JSON.stringify(data)) return; }
    catch { return; }
    setEstimateLineId("");
    setLabor([]);
    setForm((f) => ({ ...emptyForm(), work_date: f.work_date, object_name: f.object_name, location: f.location, unit: selected ? f.unit : "" }));
  }

  function removePending(commandScope: string) {
    setPendingCommands((current) => { const next = { ...current }; delete next[commandScope]; return next; });
  }

  function acceptedOnline(command: PreparedWorkLogCreate, commandScope: string, label: string) {
    removePending(commandScope);
    if (liveScope.current !== commandScope) return;
    setError("");
    setMessage(label);
    clearSubmitted(command.patch, commandScope);
    setTick((value) => value + 1);
  }

  async function preserveUncertain(command: PreparedWorkLogCreate, reason: unknown, commandScope: string) {
    if (reason instanceof OfflineSessionChanged) return;
    const attempts = command.attempts + 1;
    const status = reason instanceof ApiError && [401, 403].includes(reason.status || 0)
      ? "auth_required" : reason instanceof ApiError && [400, 404, 409, 422].includes(reason.status || 0)
        ? "attention" : attempts >= 3 ? "attention" : "queued";
    const retry: PreparedWorkLogCreate = { ...command, attempts, status, updatedAt: new Date().toISOString() };
    try {
      await enqueuePreparedWorkLogCreate(retry);
      removePending(commandScope);
      if (liveScope.current === commandScope) {
        lastQueued.current = { scope: commandScope, id: command.id };
        setMessage("Запись сохранена на устройстве. Ожидает подтверждения сервера.");
        clearSubmitted(command.patch, commandScope);
      }
    } catch {
      setPendingCommands((current) => ({ ...current, [commandScope]: retry }));
      if (liveScope.current === commandScope) setError("Ответ сервера не подтверждён, сохранить повтор на устройстве не удалось. Запись хранится только на этой странице. Повторите неподтверждённую запись.");
    }
  }

  async function save() {
    if (pending || saving) return;
    let body: Record<string, unknown>;
    try { body = workBody(); } catch (reason) { setError((reason as Error).message); return; }
    const commandScope = scope;
    setSaving(true); setError(""); setMessage("");
    try {
      if (!userId) throw new Error(!isOnline() ? "Войдите заново, чтобы сохранить запись на устройстве." : "Войдите заново, чтобы отправить запись журнала.");
      if (!isOnline()) {
        const id = await enqueueWorkLogCreate({ userId, projectId, data: body });
        if (liveScope.current === commandScope) {
          lastQueued.current = { scope: commandScope, id };
          setMessage("Запись сохранена на устройстве. Ожидает отправки.");
          clearSubmitted(body, commandScope);
        }
        return;
      }
      const command = prepareWorkLogCreate({ userId, projectId, data: body });
      try { await submitPreparedWorkLogCreate(command); }
      catch (reason) {
        if (reason instanceof ApiError && [400, 401, 403, 404, 409, 422].includes(reason.status || 0)) throw reason;
        await preserveUncertain(command, reason, commandScope);
        return;
      }
      acceptedOnline(command, commandScope, "Запись добавлена");
    } catch (err) {
      if (liveScope.current === commandScope) setError(err instanceof Error ? err.message : "Не удалось сохранить запись");
    } finally { setSaving(false); }
  }

  async function retryPending() {
    if (!pending || saving) return;
    const command = pending, commandScope = scope;
    setSaving(true); setError("");
    try {
      if (!isOnline()) {
        try {
          await enqueuePreparedWorkLogCreate(command);
          removePending(commandScope);
          if (liveScope.current === commandScope) {
            lastQueued.current = { scope: commandScope, id: command.id };
            setMessage("Запись сохранена на устройстве. Ожидает отправки.");
            clearSubmitted(command.patch, commandScope);
          }
        } catch { if (liveScope.current === commandScope) setError("Повтор пока не сохранён на устройстве. Не закрывайте страницу с неподтверждённой записью."); }
        return;
      }
      try { await submitPreparedWorkLogCreate(command); }
      catch (reason) { await preserveUncertain(command, reason, commandScope); return; }
      acceptedOnline(command, commandScope, "Неподтверждённая запись добавлена");
    } finally { setSaving(false); }
  }

  async function remove(id: number) {
    setError("");
    try {
      await api(`/projects/${projectId}/work-log/${id}`, { method: "DELETE" });
      setTick((v) => v + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось удалить запись");
    }
  }

  function fillLaborFromCrew(crewId: string) {
    const crew = crews.find((c) => String(c.id) === crewId);
    if (!crew) return;
    const names = [crew.foreman ?? "", ...crew.members.split("\n").map((l) => l.split("—")[0].trim())].map((n) => n.trim()).filter(Boolean);
    setLabor(names.map((n) => ({ person_name: n, hours: form.hours.trim() || "8", hourly_rate: null })));
  }

  function setLaborRow(index: number, patch: Partial<LaborRow>) {
    setLabor((rows) => rows.map((r, i) => (i === index ? { ...r, ...patch } : r)));
  }

  async function saveCrew() {
    if (!crewForm.name.trim()) { setError("Укажите название бригады"); return; }
    setError("");
    const body = { name: crewForm.name.trim(), foreman: crewForm.foreman.trim(), members: crewForm.members };
    try {
      if (crewForm.id) await api(`/projects/${projectId}/work-log/crews/${crewForm.id}`, { method: "PATCH", body: JSON.stringify(body) });
      else await api(`/projects/${projectId}/work-log/crews`, { method: "POST", body: JSON.stringify(body) });
      setCrewForm({ id: 0, name: "", foreman: "", members: "" });
      setMessage("Бригада сохранена");
      setTick((v) => v + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить бригаду");
    }
  }

  async function removeCrew(id: number) {
    setError("");
    try {
      await api(`/projects/${projectId}/work-log/crews/${id}`, { method: "DELETE" });
      setTick((v) => v + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось удалить бригаду");
    }
  }

  async function downloadForm() {
    setError("");
    try {
      const response = await fetch(`/projects/${projectId}/work-log/form.docx`, {
        method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", ...csrfHeaders() },
        body: JSON.stringify({ header: formHeader }),
      });
      if (!response.ok) throw new Error(`Не удалось сформировать журнал (${response.status})`);
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = "Общий журнал работ.docx";
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сформировать журнал");
    }
  }

  async function downloadExcel() {
    try {
      const response = await fetch(`/projects/${projectId}/work-log/export.xlsx`, { credentials: "same-origin" });
      if (!response.ok) throw new Error(`Не удалось скачать файл (${response.status})`);
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = "Журнал работ.xlsx";
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось скачать файл");
    }
  }

  return (
    <div className="wl">
      <div className="wl-head">
        <h3 className="wl-title">Журнал работ</h3>
        <button type="button" onClick={() => void downloadExcel()}>Скачать в Excel</button>
      </div>
      <details className="wl-print">
        <summary>Печатная форма журнала (Word, по РД-11-05-2007)</summary>
        <div className="wl-form">
          {([["journal_no", "№ журнала"], ["object_name", "Объект"], ["address", "Адрес"], ["customer", "Застройщик / заказчик"], ["contractor", "Лицо, осуществляющее строительство"], ["designer", "Проектная организация"]] as [string, string][]).map(([k, label]) => (
            <label key={k}>{label}<input value={formHeader[k] ?? ""} onChange={(e) => setFormHeader((h) => ({ ...h, [k]: e.target.value }))} /></label>
          ))}
          <button type="button" className="wl-save" onClick={() => void downloadForm()}>Скачать журнал по бланку (Word)</button>
        </div>
      </details>

      <div className="wl-tabs" role="tablist">
        <button type="button" role="tab" aria-selected={view === "all"} className={view === "all" ? "wl-tab wl-tab-on" : "wl-tab"} onClick={() => setView("all")}>Записи за день</button>
        <button type="button" role="tab" aria-selected={view === "crews"} className={view === "crews" ? "wl-tab wl-tab-on" : "wl-tab"} onClick={() => setView("crews")}>Бригады</button>
        <button type="button" role="tab" aria-selected={view === "plan"} className={view === "plan" ? "wl-tab wl-tab-on" : "wl-tab"} onClick={() => setView("plan")}>Подсказка из графика (ГПР)</button>
      </div>

      {view === "crews" && (
        <div className="wl-crews">
          <p className="wl-note">Состав бригад вносится здесь, на компьютере. На объекте прораб выбирает бригаду одной кнопкой, число рабочих подставляется само.</p>
          <form className="wl-form" aria-label="Бригада" onSubmit={(e) => { e.preventDefault(); void saveCrew(); }}>
            <label>Название бригады<input value={crewForm.name} onChange={(e) => setCrewForm((f) => ({ ...f, name: e.target.value }))} placeholder="Бригада электромонтажников" /></label>
            <label>Бригадир / ответственный<input value={crewForm.foreman} onChange={(e) => setCrewForm((f) => ({ ...f, foreman: e.target.value }))} placeholder="Фамилия И.О." /></label>
            <label className="wl-wide">Состав (по одному человеку в строке: ФИО — должность)
              <textarea rows={4} value={crewForm.members} onChange={(e) => setCrewForm((f) => ({ ...f, members: e.target.value }))} />
            </label>
            <button type="submit" className="wl-save">{crewForm.id ? "Сохранить бригаду" : "Добавить бригаду"}</button>
          </form>
          {crews.length === 0 && <p className="wl-empty">Бригад пока нет.</p>}
          <ul className="wl-plan-list">
            {crews.map((c) => (
              <li key={c.id} className="wl-crew-row">
                <span><b>{c.name}</b>{c.foreman ? `, ${c.foreman}` : ""} · человек: {c.members_count}</span>
                <span className="wl-actions">
                  <button type="button" onClick={() => setCrewForm({ id: c.id, name: c.name, foreman: c.foreman ?? "", members: c.members })}>Изменить</button>
                  <button type="button" aria-label={`Удалить бригаду ${c.name}`} onClick={() => void removeCrew(c.id)}>Удалить</button>
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {view === "plan" && (
        <div className="wl-plan">
          {!plan.baseline && <p className="wl-empty">В проекте нет графика работ. Загрузите ГПР в разделе «ГПР и ДДС»: журнал строится по нему.</p>}
          {plan.baseline && <p className="wl-note">График: {plan.baseline.name} (версия {plan.baseline.version}). Необязательно: выберите работу из графика, и название подставится само.</p>}
          <ul className="wl-plan-list">
            {plan.items.filter((i) => !i.is_group && !i.is_milestone).map((i) => (
              <li key={i.id}>
                <button
                  type="button"
                  className={`wl-plan-item${selected?.id === i.id ? " wl-plan-on" : ""}${i.overdue ? " wl-plan-late" : ""}`}
                  aria-pressed={selected?.id === i.id}
                  onClick={() => setSelected(selected?.id === i.id ? null : i)}
                >
                  <span className="wl-plan-title">{i.title}</span>
                  <span className="wl-sub">
                    План: {i.planned_start ? formatDate(i.planned_start) : "—"} – {i.planned_finish ? formatDate(i.planned_finish) : "—"}
                    {" · "}выполнено {Math.round(i.actual_progress)}% из {Math.round(i.planned_progress)}% по плану
                    {i.fact_entries > 0 ? ` · записей: ${i.fact_entries}${i.fact_qty ? `, объём ${i.fact_qty} ${i.fact_unit ?? ""}` : ""}` : ""}
                    {i.overdue ? " · просрочено" : ""}
                  </span>
                </button>
              </li>
            ))}
          </ul>
          {selected && (
            <div className="wl-selected" role="status">
              Вносим факт по работе: <b>{selected.title}</b>. Название, график и единицы подставлены автоматически.
              <button type="button" onClick={() => setSelected(null)}>Другая работа (вне графика)</button>
            </div>
          )}
        </div>
      )}
      {view !== "crews" && <>
      {invalidReferences && <button type="button" onClick={clearReferences}>Снять неподтверждённые привязки</button>}
      <form className="wl-form" onSubmit={(e) => { e.preventDefault(); void save(); }} aria-label="Новая запись журнала">
        <label>Дата<input type="date" value={form.work_date} onChange={(e) => set("work_date", e.target.value)} /></label>
        <label>Объект
          <input list="wl-objects" value={form.object_name} onChange={(e) => set("object_name", e.target.value)} placeholder="Например, ДУБНА" />
          <datalist id="wl-objects">{suggest.objects.map((o) => <option key={o} value={o} />)}</datalist>
        </label>
        {(suggest.estimate_lines?.length ?? 0) > 0 && (
          <label className="wl-wide">Позиция сметы (для КС-2)
            <select value={estimateLineId} onChange={(e) => {
              const id = e.target.value;
              setEstimateLineId(id);
              const line = suggest.estimate_lines?.find((l) => String(l.id) === id);
              if (line) setForm((f) => ({ ...f, work_name: line.name, unit: line.unit ?? f.unit }));
            }}>
              <option value="">Не из сметы</option>
              {suggest.estimate_lines?.map((l) => <option key={l.id} value={String(l.id)}>{`${l.position ? `${l.position}. ` : ""}${l.name}${l.unit ? `, ${l.unit}` : ""}`}</option>)}
            </select>
          </label>
        )}
        {!selected && <label className="wl-wide">Какая работа выполнена
          <input list="wl-works" value={form.work_name} onChange={(e) => pickWork(e.target.value)} placeholder="Начните вводить или выберите из спецификации" />
          <datalist id="wl-works">
            {suggest.works.map((w) => <option key={`w${w.work_name}`} value={w.work_name} />)}
            {suggest.rd_items.map((i) => <option key={`r${i.id}`} value={i.name} />)}
          </datalist>
        </label>}
        <label>Объём<input inputMode="decimal" value={form.qty} onChange={(e) => set("qty", e.target.value)} placeholder="12,5" /></label>
        <label>Ед. изм.<input value={form.unit} onChange={(e) => set("unit", e.target.value)} placeholder="м, шт" /></label>
        <label>Место<input value={form.location} onChange={(e) => set("location", e.target.value)} placeholder="Ось, этаж, помещение" /></label>
        <label>Бригада
          <select value={form.crew_id} onChange={(e) => { set("crew_id", e.target.value); fillLaborFromCrew(e.target.value); }}>
            <option value="">Без бригады</option>
            {crews.map((c) => <option key={c.id} value={String(c.id)}>{c.name}</option>)}
          </select>
        </label>
        <label>Часов работы<input inputMode="decimal" value={form.hours} onChange={(e) => set("hours", e.target.value)} placeholder="8" /></label>
        <label>Рабочих<input inputMode="numeric" value={form.workers} onChange={(e) => set("workers", e.target.value)} placeholder="по бригаде" /></label>
        <label>Погода<input value={form.weather} onChange={(e) => set("weather", e.target.value)} placeholder="+5, дождь" /></label>
        <label className="wl-wide">Примечание<input value={form.note} onChange={(e) => set("note", e.target.value)} /></label>
        <fieldset className="wl-wide wl-labor">
          <legend>Кто сколько отработал</legend>
          {labor.length === 0 && <p className="wl-note">Необязательно. Выберите бригаду, чтобы подставить людей, или добавьте вручную.</p>}
          {labor.map((r, i) => (
            <div className="wl-labor-row" key={i}>
              <input aria-label={`Работник ${i + 1}`} placeholder="ФИО" value={r.person_name} onChange={(e) => setLaborRow(i, { person_name: e.target.value })} />
              <input aria-label={`Часов у работника ${i + 1}`} inputMode="decimal" placeholder="Часов" value={r.hours} onChange={(e) => setLaborRow(i, { hours: e.target.value })} />
              <input aria-label={`Ставка работника ${i + 1}`} inputMode="decimal" placeholder="Ставка ₽/ч (необязательно)" value={r.hourly_rate ?? ""} onChange={(e) => setLaborRow(i, { hourly_rate: e.target.value })} />
              <button type="button" aria-label={`Убрать работника ${i + 1}`} onClick={() => setLabor((rows) => rows.filter((_, j) => j !== i))}>×</button>
            </div>
          ))}
          <button type="button" onClick={() => setLabor((rows) => [...rows, { person_name: "", hours: "8", hourly_rate: null }])}>Добавить работника</button>
        </fieldset>
        <button type="submit" className="wl-save" disabled={saving || Boolean(pending)}>{saving ? "Сохраняем…" : "Добавить запись"}</button>
      </form>
      {pending && <aside className="wl-note" aria-label="Неподтверждённая запись">
        <p>Ожидает подтверждения: {String(pending.patch.work_name)} · {String(pending.patch.work_date)}.</p>
        <p>Повтор не сохранён на устройстве: он доступен только на этой странице. Не закрывайте её до подтверждения или сохранения записи.</p>
        <button type="button" disabled={saving} onClick={() => void retryPending()}>Повторить неподтверждённую запись</button>
      </aside>}
      {message && <p className="wl-note" role="status">{message}</p>}
      {error && <p className="wl-error" role="alert">{error}</p>}

      {summary && summary.entries > 0 && (
        <p className="wl-note">Записей: {summary.entries}, рабочих дней: {summary.days}</p>
      )}
      <input className="wl-search" aria-label="Поиск по журналу" placeholder="Поиск по работам, месту, примечанию" value={query} onChange={(e) => setQuery(e.target.value)} />
      {loading && <p className="wl-note">Загружаем…</p>}
      {!loading && entries.length === 0 && <p className="wl-empty">Записей пока нет. Заполните форму выше: достаточно даты и названия работы.</p>}
      {entries.length > 0 && (
        <div className="wl-wrap">
          <table className="wl-table">
            <thead><tr><th>Дата</th><th>Объект</th><th>Работа</th><th>Объём</th><th>Место</th><th>Бригада</th><th>Рабочих</th><th>Часов</th><th></th></tr></thead>
            <tbody>
              {entries.map((e) => (
                <tr key={e.id}>
                  <td>{formatDate(e.work_date)}</td>
                  <td>{e.object_name || "—"}</td>
                  <td>{e.work_name}{e.note ? <div className="wl-sub">{e.note}</div> : null}</td>
                  <td>{e.qty ? `${e.qty} ${e.unit ?? ""}`.trim() : "—"}</td>
                  <td>{e.location || "—"}</td>
                  <td>{crews.find((c) => c.id === e.crew_id)?.name ?? "—"}</td>
                  <td>{e.workers ?? "—"}</td>
                  <td>{e.hours ?? "—"}</td>
                  <td><button type="button" aria-label={`Удалить запись ${e.work_name}`} onClick={() => void remove(e.id)}>Удалить</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      </>}
    </div>
  );
}
