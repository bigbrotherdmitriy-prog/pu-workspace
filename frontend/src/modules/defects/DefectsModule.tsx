import { useCallback, useEffect, useState } from "react";
import { api, csrfHeaders } from "../../api/client";
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

export function DefectsModule({ projectId }: { projectId: number }) {
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

  const reload = useCallback(() => setTick((v) => v + 1), []);
  const fail = (e: unknown, fallback: string) => setError(e instanceof Error ? e.message : fallback);

  useEffect(() => {
    if (projectId <= 0) return;
    const params = new URLSearchParams();
    if (status) params.set("status", status);
    if (query.trim()) params.set("q", query.trim());
    api<Listing>(`/projects/${projectId}/defects${params.toString() ? `?${params.toString()}` : ""}`)
      .then((res) => setData(res ?? { defects: [], counts: {}, overdue: 0 }))
      .catch((e) => fail(e, "Не удалось загрузить замечания"));
  }, [projectId, status, query, tick]);

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

  async function create() {
    if (!form.title.trim()) { setError("Опишите замечание коротко: что не так"); return; }
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const body: Record<string, string> = {};
      for (const [k, v] of Object.entries(form)) if (v.trim()) body[k] = v.trim();
      const created = await api<Defect>(`/projects/${projectId}/defects`, { method: "POST", body: JSON.stringify(body) });
      if (newPhotos.length) await uploadPhotos(created.id, newPhotos, "before");
      setForm((f) => ({ ...emptyForm(), object_name: f.object_name, responsible: f.responsible }));
      setNewPhotos([]);
      setMessage(`Замечание № ${created.number} добавлено`);
      reload();
    } catch (e) {
      fail(e, "Не удалось сохранить замечание");
      reload();
    } finally {
      setBusy(false);
    }
  }

  async function openDefect(id: number) {
    setError("");
    try { setOpen(await api<Defect>(`/projects/${projectId}/defects/${id}`)); } catch (e) { fail(e, "Не удалось открыть замечание"); }
  }

  async function addPhotos(kind: "before" | "after", files: File[]) {
    if (!open || !files.length) return;
    setError("");
    try {
      await uploadPhotos(open.id, files, kind);
      await openDefect(open.id);
      reload();
    } catch (e) { fail(e, "Не удалось загрузить фото"); }
  }

  async function setDefectStatus(next: string) {
    if (!open) return;
    setError("");
    try {
      const res = await api<Defect>(`/projects/${projectId}/defects/${open.id}/status`, { method: "POST", body: JSON.stringify({ status: next, comment }) });
      setOpen(res);
      setComment("");
      reload();
    } catch (e) { fail(e, "Не удалось изменить статус"); }
  }

  async function addComment() {
    if (!open || !comment.trim()) return;
    try {
      setOpen(await api<Defect>(`/projects/${projectId}/defects/${open.id}/comments`, { method: "POST", body: JSON.stringify({ text: comment }) }));
      setComment("");
    } catch (e) { fail(e, "Не удалось сохранить комментарий"); }
  }

  async function removePhoto(photo: Photo) {
    if (!open) return;
    try { setOpen(await api<Defect>(`/projects/${projectId}/defects/${open.id}/photos/${photo.id}`, { method: "DELETE" })); reload(); } catch (e) { fail(e, "Не удалось удалить фото"); }
  }

  async function removeDefect() {
    if (!open) return;
    try { await api(`/projects/${projectId}/defects/${open.id}`, { method: "DELETE" }); setOpen(null); reload(); } catch (e) { fail(e, "Не удалось удалить замечание"); }
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
          <input type="file" accept="image/*" capture="environment" multiple aria-label="Фото замечания" onChange={(e) => setNewPhotos(Array.from(e.target.files ?? []))} />
        </label>
        {newPhotos.length > 0 && <span className="df-note df-wide">Выбрано фото: {newPhotos.length}</span>}
        <button type="submit" className="df-save" disabled={busy}>{busy ? "Сохраняем…" : "Добавить замечание"}</button>
      </form>
      {message && <p className="df-note" role="status">{message}</p>}
      {error && <p className="df-error" role="alert">{error}</p>}

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
            <button type="button" aria-label="Закрыть замечание" onClick={() => setOpen(null)}>×</button>
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
