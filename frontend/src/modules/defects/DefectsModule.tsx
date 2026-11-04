import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, csrfHeaders } from "../../api/client";
import { isOnline } from "../../offline/connectivity";
import { OfflineSessionChanged } from "../../offline/lifecycle";
import { prepareDefectCreate, enqueuePreparedDefectCreate, submitPreparedDefectCreate, DEFECT_APPLIED_EVENT, type PreparedDefectCreate } from "../../offline/syncEngine";
import "./defects.css";

type Photo = { id: number; kind: "before" | "after"; filename: string };
type Comment = { id: number; text: string; status_change: string | null; created_at: string | null };
type Defect = {
  id: number; number: number; title: string; description: string | null; object_name: string | null; location: string | null;
  priority: string; status: string; status_label: string; responsible: string | null; due_date: string | null; overdue: boolean;
  photos: Photo[]; comments?: Comment[];
};
type Listing = { defects: Defect[]; counts: Record<string, number>; overdue: number };

const STATUS_LABEL: Record<string, string> = { open: "Открыто", in_progress: "В работе", fixed: "Устранено", accepted: "Принято" };
const PRIORITY_LABEL: Record<string, string> = { low: "Низкий", normal: "Обычный", high: "Высокий" };
const NEXT: Record<string, string[]> = { open: ["in_progress", "fixed"], in_progress: ["open", "fixed"], fixed: ["accepted", "open"], accepted: ["open"] };
const NEXT_BUTTON: Record<string, string> = { in_progress: "Взять в работу", fixed: "Отметить устранённым", accepted: "Принять", open: "Вернуть в «Открыто»" };

const dateRu = (v: string | null) => (v ? v.slice(0, 10).split("-").reverse().join(".") : "");
const emptyForm = () => ({ title: "", description: "", object_name: "", location: "", priority: "normal", responsible: "", due_date: "" });

type DefectIntent = { command: PreparedDefectCreate; submitted: ReturnType<typeof emptyForm>; files: File[]; key: string };

export function DefectsModule({ projectId, userId = 0 }: { projectId: number; userId?: number }) {
  const [data, setData] = useState<Listing>({ defects: [], counts: {}, overdue: 0 });
  const [status, setStatus] = useState("");
  const [query, setQuery] = useState("");
  const [form, setForm] = useState(emptyForm);
  const [newPhotos, setNewPhotos] = useState<File[]>([]);
  const [open, setOpen] = useState<Defect | null>(null);
  const [comment, setComment] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [tick, setTick] = useState(0);
  const createBusy = useRef(false);
  const latestDraft = useRef({ form, newPhotos });
  latestDraft.current = { form, newPhotos };
  const photoInput = useRef<HTMLInputElement>(null);
  const [photoRecovery, setPhotoRecovery] = useState<Record<string, { id: number; number: number }>>({});

  const scopeKey = `${userId}:${projectId}`;
  const [pendingCommands, setPendingCommands] = useState<Record<string, DefectIntent>>({});
  const pending = pendingCommands[scopeKey];
  const queued = useRef(new Set<string>());
  const detailRequest = useRef(0);
  const currentScope = useRef({ key: scopeKey, active: false, generation: 0 });
  if (currentScope.current.key !== scopeKey) {
    currentScope.current = { key: scopeKey, active: false, generation: 0 };
    detailRequest.current += 1;
  }
  const scope = currentScope.current;
  const priorActor = useRef(userId);
  const liveProject = useRef(projectId);
  liveProject.current = projectId;


  const reload = useCallback(() => setTick((v) => v + 1), []);
  const fail = (e: unknown, fallback: string) => setError(e instanceof Error ? e.message : fallback);

  useEffect(() => {
    scope.active = true; scope.generation += 1;
    createBusy.current = false; setBusy(false); setError(""); setMessage("");
    if (priorActor.current !== userId) { setForm(emptyForm()); setNewPhotos([]); priorActor.current = userId; }
    setData({ defects: [], counts: {}, overdue: 0 });
    setOpen(null);
    detailRequest.current += 1;
    return () => { detailRequest.current += 1; scope.active = false; scope.generation += 1; };
  }, [projectId, userId, scope]);

  useEffect(() => {
    const applied = (event: Event) => {
      const detail = (event as CustomEvent<{ userId: number; projectId: number; clientMutationId: string; defectNumber: number }>).detail;
      if (!detail || detail.userId !== userId || detail.projectId !== projectId || !Number.isSafeInteger(detail.defectNumber) || detail.defectNumber <= 0) return;
      const key = `${scopeKey}:${detail.clientMutationId}`;
      if (!queued.current.delete(key)) return;
      setMessage(`Замечание № ${detail.defectNumber} синхронизировано`); reload();
    };
    window.addEventListener(DEFECT_APPLIED_EVENT, applied);
    return () => window.removeEventListener(DEFECT_APPLIED_EVENT, applied);
  }, [userId, projectId, scopeKey, reload]);

  useEffect(() => {
    if (projectId <= 0) return;
    let active = true;
    const params = new URLSearchParams();
    if (status) params.set("status", status);
    if (query.trim()) params.set("q", query.trim());
    api<Listing>(`/projects/${projectId}/defects${params.toString() ? `?${params.toString()}` : ""}`)
      .then((res) => { if (active) setData(res ?? { defects: [], counts: {}, overdue: 0 }); })
      .catch((e) => { if (active) fail(e, "Не удалось загрузить замечания"); });
    return () => { active = false; };
  }, [projectId, userId, status, query, tick]);

  async function uploadPhotos(defectId: number, files: File[], kind: "before" | "after") {
    for (const file of files) {
      const response = await fetch(`/projects/${projectId}/defects/${defectId}/photos?name=${encodeURIComponent(file.name)}&kind=${kind}`, {
        method: "POST", body: file, credentials: "same-origin", headers: { "Content-Type": file.type || "image/jpeg", ...csrfHeaders() },
      });
      if (!response.ok) {
        let detail = `Не удалось загрузить ${file.name} (${response.status})`;
        try { const body = await response.json(); if (typeof body?.detail === "string") detail = body.detail; } catch { /* default */ }
        throw new Error(detail);
      }
    }
  }

  function clearSubmitted(submitted: ReturnType<typeof emptyForm>, files: File[], alive: () => boolean) {
    if (!alive()) return;
    if (JSON.stringify(latestDraft.current.form) === JSON.stringify(submitted)) {
      setForm((current) => JSON.stringify(current) === JSON.stringify(submitted)
        ? { ...emptyForm(), object_name: current.object_name, responsible: current.responsible } : current);
      const currentFiles = latestDraft.current.newPhotos;
      if (currentFiles.length === files.length && currentFiles.every((file, index) => file === files[index])) {
        setNewPhotos([]);
        if (photoInput.current) photoInput.current.value = "";
      }
    }
  }

  function acknowledgeCreated(created: { id: number; number: number }, intent: DefectIntent, alive: () => boolean) {
    if (!alive()) return;
    clearSubmitted(intent.submitted, intent.files, alive);
    setMessage(`Замечание № ${created.number} добавлено`); reload();
  }

  function removePending(key: string) {
    setPendingCommands(current => { const next = { ...current }; delete next[key]; return next; });
  }

  async function preserveUncertain(intent: DefectIntent, reason: unknown, alive: () => boolean) {
    if (reason instanceof OfflineSessionChanged) return;
    const attempts = intent.command.attempts + 1;
    const status = reason instanceof ApiError && [401, 403].includes(reason.status || 0) ? "auth_required" : attempts >= 3 ? "attention" : "queued";
    const retry: DefectIntent = { ...intent, command: { ...intent.command, attempts, status, updatedAt: new Date().toISOString() } };
    if (!intent.files.length) {
      try {
        await enqueuePreparedDefectCreate(retry.command);
        removePending(intent.key); queued.current.add(`${intent.key}:${intent.command.id}`);
        if (alive()) {
          clearSubmitted(intent.submitted, intent.files, alive);
          setMessage(status === "auth_required" ? "Замечание сохранено на устройстве. Войдите исходной учётной записью для отправки." : "Замечание сохранено на устройстве. Ожидает подтверждения сервера.");
        }
        return;
      } catch { /* Keep exact intent in memory rather than inventing a second create. */ }
    }
    setPendingCommands(current => ({ ...current, [intent.key]: retry }));
    if (alive()) setError(intent.files.length
      ? "Создание замечания не подтверждено. Фото не сохранены в offline-очереди; не закрывайте страницу. Повторите неподтверждённое замечание."
      : "Не удалось подтвердить создание и сохранить повтор на устройстве. Не закрывайте страницу; повторите неподтверждённое замечание.");
  }

  async function runIntent(intent: DefectIntent) {
    if (createBusy.current) return;
    const generation = scope.generation;
    const alive = () => currentScope.current === scope && scope.active && scope.generation === generation;
    if (!alive() || intent.key !== scopeKey) return;
    createBusy.current = true; setBusy(true); setError(""); setMessage("");
    try {
      if (!isOnline()) {
        if (intent.files.length) throw new Error("Фото без связи пока не сохраняются. Оставьте форму открытой и повторите после восстановления связи.");
        await enqueuePreparedDefectCreate(intent.command);
        removePending(intent.key); queued.current.add(`${intent.key}:${intent.command.id}`);
        if (alive()) { clearSubmitted(intent.submitted, intent.files, alive); setMessage("Замечание сохранено на устройстве. Ожидает отправки."); }
        return;
      }
      let result: Awaited<ReturnType<typeof submitPreparedDefectCreate>>;
      try { result = await submitPreparedDefectCreate(intent.command); }
      catch (reason) {
        if (reason instanceof ApiError && [400, 404, 409, 422].includes(reason.status || 0)) throw reason;
        await preserveUncertain(intent, reason, alive); return;
      }
      removePending(intent.key);
      const created = { id: result.entityId, number: result.entityNumber };
      if (!alive()) {
        if (intent.files.length) setPhotoRecovery(current => ({ ...current, [intent.key]: created }));
        return;
      }
      try { if (intent.files.length) await uploadPhotos(created.id, intent.files, "before"); }
      catch (reason) {
        setPhotoRecovery(current => ({ ...current, [intent.key]: created }));
        acknowledgeCreated(created, intent, alive);
        if (alive()) fail(reason, "Не удалось загрузить все фото");
        return;
      }
      acknowledgeCreated(created, intent, alive);
    } catch (reason) { if (alive()) { fail(reason, "Не удалось сохранить замечание"); reload(); } }
    finally { if (alive()) { createBusy.current = false; setBusy(false); } }
  }

  async function create() {
    if (createBusy.current || pending) return;
    if (!form.title.trim()) { setError("Опишите замечание коротко: что не так"); return; }
    if (!Number.isSafeInteger(userId) || userId <= 0 || !Number.isSafeInteger(projectId) || projectId <= 0) {
      setError("Войдите заново и выберите проект для создания замечания."); return;
    }
    const submitted = { ...form }, files = [...newPhotos];
    try {
      const body: Record<string, string> = {};
      for (const [key, value] of Object.entries(submitted)) if (value.trim()) body[key] = value.trim();
      const command = prepareDefectCreate({ userId, projectId, data: body });
      await runIntent({ command, submitted, files, key: scopeKey });
    } catch (reason) { fail(reason, "Не удалось подготовить замечание"); }
  }

  async function openDefect(id: number) {
    const request = ++detailRequest.current;
    const lookupProject = projectId;
    setError("");
    try {
      const result = await api<Defect>(`/projects/${lookupProject}/defects/${id}`);
      if (request === detailRequest.current && liveProject.current === lookupProject) {
        setOpen(result);
        setPhotoRecovery((current) => {
          if (current[`${userId}:${lookupProject}`]?.id !== result.id) return current;
          const next = { ...current };
          delete next[`${userId}:${lookupProject}`];
          return next;
        });
      }
    } catch (e) {
      if (request === detailRequest.current && liveProject.current === lookupProject) fail(e, "Не удалось открыть замечание");
    }
  }

  function closeDefect() {
    detailRequest.current += 1;
    setOpen(null);
  }

  // A reply belongs to the selected detail instance, not just its numeric id.
  function mutationScope() {
    const request = ++detailRequest.current;
    const requestProject = projectId;
    return () => request === detailRequest.current && liveProject.current === requestProject;
  }

  async function addPhotos(kind: "before" | "after", files: File[]) {
    if (!open || !files.length) return;
    const current = mutationScope();
    const defectId = open.id;
    setError("");
    try {
      await uploadPhotos(defectId, files, kind);
      if (!current()) return;
      await openDefect(defectId);
      if (liveProject.current === projectId) reload();
    } catch (e) { if (current()) fail(e, "Не удалось загрузить фото"); }
  }

  async function setDefectStatus(next: string) {
    if (!open) return;
    const current = mutationScope();
    const submittedComment = comment;
    setError("");
    try {
      const res = await api<Defect>(`/projects/${projectId}/defects/${open.id}/status`, { method: "POST", body: JSON.stringify({ status: next, comment: submittedComment }) });
      if (!current()) return;
      setOpen(res);
      setComment((value) => value === submittedComment ? "" : value);
      reload();
    } catch (e) { if (current()) fail(e, "Не удалось изменить статус"); }
  }

  async function addComment() {
    if (!open || !comment.trim()) return;
    const current = mutationScope();
    const submittedComment = comment;
    try {
      const result = await api<Defect>(`/projects/${projectId}/defects/${open.id}/comments`, { method: "POST", body: JSON.stringify({ text: submittedComment }) });
      if (!current()) return;
      setOpen(result);
      setComment((value) => value === submittedComment ? "" : value);
    } catch (e) { if (current()) fail(e, "Не удалось сохранить комментарий"); }
  }

  async function removePhoto(photo: Photo) {
    if (!open) return;
    const current = mutationScope();
    try {
      const result = await api<Defect>(`/projects/${projectId}/defects/${open.id}/photos/${photo.id}`, { method: "DELETE" });
      if (!current()) return;
      setOpen(result); reload();
    } catch (e) { if (current()) fail(e, "Не удалось удалить фото"); }
  }

  async function removeDefect() {
    if (!open) return;
    const current = mutationScope();
    try {
      await api(`/projects/${projectId}/defects/${open.id}`, { method: "DELETE" });
      if (!current()) return;
      closeDefect(); reload();
    } catch (e) { if (current()) fail(e, "Не удалось удалить замечание"); }
  }

  async function exportExcel() {
    try {
      const response = await fetch(`/projects/${projectId}/defects/export.xlsx${status ? `?status=${status}` : ""}`, { credentials: "same-origin" });
      if (!response.ok) throw new Error(`Не удалось скачать (${response.status})`);
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = "Замечания.xlsx";
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    } catch (e) { fail(e, "Не удалось скачать"); }
  }

  const photoUrl = (d: Defect, p: Photo) => `/projects/${projectId}/defects/${d.id}/photos/${p.id}`;

  return (
    <div className="df">
      <div className="df-head">
        <h3 className="df-title">Замечания</h3>
        <button type="button" onClick={() => void exportExcel()}>Скачать в Excel</button>
      </div>

      <form className="df-form" aria-label="Новое замечание" onSubmit={(e) => { e.preventDefault(); void create(); }}>
        <label className="df-wide">Что не так
          <input value={form.title} onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))} placeholder="Например: не закреплён кабельный лоток" />
        </label>
        <label>Объект<input value={form.object_name} onChange={(e) => setForm((f) => ({ ...f, object_name: e.target.value }))} /></label>
        <label>Место<input value={form.location} onChange={(e) => setForm((f) => ({ ...f, location: e.target.value }))} placeholder="Ось, этаж, помещение" /></label>
        <label>Приоритет
          <select value={form.priority} onChange={(e) => setForm((f) => ({ ...f, priority: e.target.value }))}>
            {Object.entries(PRIORITY_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </label>
        <label>Ответственный<input value={form.responsible} onChange={(e) => setForm((f) => ({ ...f, responsible: e.target.value }))} /></label>
        <label>Срок устранения<input type="date" value={form.due_date} onChange={(e) => setForm((f) => ({ ...f, due_date: e.target.value }))} /></label>
        <label className="df-wide">Подробности<input value={form.description} onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))} /></label>
        <label className="df-wide">Фото с телефона или из файлов
          <input ref={photoInput} type="file" accept="image/*" capture="environment" multiple aria-label="Фото замечания" onChange={(e) => setNewPhotos(Array.from(e.target.files ?? []))} />
        </label>
        {newPhotos.length > 0 && <span className="df-note df-wide">Выбрано фото: {newPhotos.length}</span>}
        <button type="submit" className="df-save" disabled={busy || !!pending}>{busy ? "Сохраняем…" : "Добавить замечание"}</button>
      </form>
      {message && <p className="df-note" role="status">{message}</p>}
      {error && <p className="df-error" role="alert">{error}</p>}
      {pending && <p className="df-note">Есть неподтверждённое замечание. Повтор использует исходные данные, новый черновик сохранится.
        <button type="button" disabled={busy} onClick={() => void runIntent(pending)}>Повторить неподтверждённое замечание</button>
      </p>}
      {photoRecovery[scopeKey] && <p className="df-note" role="status">
        Не все фото удалось загрузить в замечание № {photoRecovery[scopeKey].number}. Проверьте вложения перед повторной загрузкой.
        <button type="button" onClick={() => void openDefect(photoRecovery[scopeKey].id)}>Открыть созданное замечание</button>
      </p>}


      <div className="df-chips" role="group" aria-label="Фильтр по статусу">
        <button type="button" className={!status ? "df-chip df-chip-on" : "df-chip"} onClick={() => setStatus("")}>Все</button>
        {Object.entries(STATUS_LABEL).map(([k, v]) => (
          <button key={k} type="button" className={status === k ? "df-chip df-chip-on" : "df-chip"} onClick={() => setStatus(status === k ? "" : k)}>{v}: {data.counts[k] ?? 0}</button>
        ))}
        {data.overdue > 0 && <span className="df-overdue">Просрочено: {data.overdue}</span>}
      </div>
      <input className="df-search" aria-label="Поиск по замечаниям" placeholder="Поиск по замечаниям, месту, ответственному" value={query} onChange={(e) => setQuery(e.target.value)} />

      {data.defects.length === 0 ? <p className="df-empty">Замечаний нет. Добавьте первое: достаточно описания и фото.</p> : (
        <ul className="df-list">
          {data.defects.map((d) => (
            <li key={d.id}>
              <button type="button" className={`df-item${d.overdue ? " df-item-late" : ""}`} onClick={() => void openDefect(d.id)}>
                {d.photos[0] ? <img className="df-thumb" src={photoUrl(d, d.photos[0])} alt="" loading="lazy" /> : <span className="df-thumb df-thumb-empty" aria-hidden="true" />}
                <span className="df-item-body">
                  <b>№ {d.number}. {d.title}</b>
                  <span className="df-note">{[d.object_name, d.location].filter(Boolean).join(", ") || "место не указано"}</span>
                  <span className="df-meta">
                    <span className={`df-status df-s-${d.status}`}>{d.status_label}</span>
                    {d.priority === "high" && <span className="df-high">Высокий приоритет</span>}
                    {d.due_date && <span className={d.overdue ? "df-overdue" : "df-note"}>срок {dateRu(d.due_date)}{d.overdue ? " (просрочено)" : ""}</span>}
                    {d.responsible && <span className="df-note">{d.responsible}</span>}
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {open && (
        <div className="df-detail" role="dialog" aria-label={`Замечание ${open.number}`}>
          <div className="df-detail-head">
            <h4>№ {open.number}. {open.title}</h4>
            <button type="button" aria-label="Закрыть замечание" onClick={closeDefect}>×</button>
          </div>
          <p className="df-note">{[open.object_name, open.location].filter(Boolean).join(", ")} · {STATUS_LABEL[open.status]}{open.responsible ? ` · ${open.responsible}` : ""}{open.due_date ? ` · срок ${dateRu(open.due_date)}` : ""}</p>
          {open.description && <p>{open.description}</p>}
          {(["before", "after"] as const).map((kind) => (
            <div key={kind}>
              <h5>{kind === "before" ? "Фото замечания" : "Фото после устранения"}</h5>
              <div className="df-photos">
                {open.photos.filter((p) => p.kind === kind).map((p) => (
                  <span className="df-photo" key={p.id}>
                    <a href={photoUrl(open, p)} target="_blank" rel="noreferrer"><img src={photoUrl(open, p)} alt={p.filename} loading="lazy" /></a>
                    <button type="button" aria-label={`Удалить фото ${p.filename}`} onClick={() => void removePhoto(p)}>×</button>
                  </span>
                ))}
              </div>
              <label className="df-add">Добавить фото
                <input type="file" accept="image/*" capture="environment" multiple aria-label={kind === "before" ? "Добавить фото замечания" : "Добавить фото результата"}
                  onChange={(e) => { void addPhotos(kind, Array.from(e.target.files ?? [])); e.currentTarget.value = ""; }} />
              </label>
            </div>
          ))}
          <input aria-label="Комментарий" placeholder="Комментарий или результат (обязателен, если нет фото после устранения)" value={comment} onChange={(e) => setComment(e.target.value)} />
          <div className="df-actions">
            {(NEXT[open.status] ?? []).map((s) => <button type="button" key={s} className={s === "fixed" || s === "accepted" ? "df-main" : ""} onClick={() => void setDefectStatus(s)}>{NEXT_BUTTON[s]}</button>)}
            <button type="button" onClick={() => void addComment()}>Только комментарий</button>
            <button type="button" onClick={() => void removeDefect()}>Удалить замечание</button>
          </div>
          {(open.comments?.length ?? 0) > 0 && (
            <ul className="df-history">{open.comments!.map((c) => <li key={c.id}>{dateRu(c.created_at)} {c.status_change ? `[${STATUS_LABEL[c.status_change]}] ` : ""}{c.text}</li>)}</ul>
          )}
        </div>
      )}
    </div>
  );
}
