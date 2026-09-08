import { useEffect, useRef, useState } from "react";
import { ApiError } from "../../api/client";

export type ReviewedDraft = { id: number; subject: string; body: string; recipient_to?: string | null;
  status: "draft" | "approved" | "rejected" | "sent"; review_token: string };
type Props = { projectId: number; draftId: number; title: string; canEdit: boolean; canApprove: boolean;
  api: (path: string, options?: RequestInit) => Promise<unknown>; onUpdated: (row: ReviewedDraft) => void };
function parse(value: unknown, id: number): ReviewedDraft {
  if (!value || typeof value !== "object") throw new Error("invalid");
  const row = value as Record<string, unknown>;
  if (row.id !== id || typeof row.subject !== "string" || !row.subject.trim() || typeof row.body !== "string"
    || !row.body.trim() || typeof row.review_token !== "string" || !/^[a-f0-9]{64}$/.test(row.review_token)
    || !["draft", "approved", "rejected", "sent"].includes(String(row.status))
    || (row.recipient_to != null && typeof row.recipient_to !== "string")) throw new Error("invalid");
  return row as ReviewedDraft;
}

export function DraftReviewCard(props: Props) {
  return <ScopedDraftReview key={`${props.projectId}:${props.draftId}`} {...props} />;
}
function ScopedDraftReview({ projectId, draftId, title, canEdit, canApprove, api, onUpdated }: Props) {
  const [saved, setSaved] = useState<ReviewedDraft | null>(null);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [recipient, setRecipient] = useState("");
  const [busy, setBusy] = useState(false);
  const [blocked, setBlocked] = useState(false);
  const [notice, setNotice] = useState("");
  const pending = useRef(false);
  const generation = useRef(0);
  const live = useRef(true);
  useEffect(() => { live.current = true; return () => { live.current = false; generation.current++; }; }, []);
  const dirty = !!saved && (subject !== saved.subject || body !== saved.body || recipient !== (saved.recipient_to || ""));
  function accept(row: ReviewedDraft) {
    setSaved(row); setSubject(row.subject); setBody(row.body); setRecipient(row.recipient_to || ""); setBlocked(false);
  }
  async function reload() {
    if (pending.current || !projectId || (dirty && !window.confirm("Заменить несохранённые правки актуальным серверным черновиком?"))) return;
    pending.current = true; setBusy(true); const ticket = ++generation.current;
    try {
      const value = await api(`/response-drafts?project_id=${projectId}`);
      if (!live.current || ticket !== generation.current) return;
      if (!value || typeof value !== "object" || !Array.isArray((value as { drafts?: unknown }).drafts)) throw new Error("invalid");
      const rows = (value as { drafts: unknown[] }).drafts.filter(row => !!row && typeof row === "object" && (row as { id?: unknown }).id === draftId);
      if (rows.length !== 1) throw new Error("invalid");
      const row = parse(rows[0], draftId); accept(row); onUpdated(row); setNotice("Проверьте актуальные тему, текст и получателя. Отправка не выполняется.");
    } catch {
      if (live.current && ticket === generation.current) { setBlocked(true); setNotice("Актуальный черновик недоступен. Подтверждение запрещено."); }
    } finally { pending.current = false; if (live.current && ticket === generation.current) setBusy(false); }
  }
  async function mutate(status: "draft" | "approved" | "rejected") {
    if (pending.current || !saved || saved.status !== "draft" || blocked || !canEdit
      || (status !== "draft" && dirty) || (status === "approved" && !canApprove)) return;
    pending.current = true; setBusy(true); const ticket = ++generation.current;
    try {
      const update = status === "draft" ? { status, expected_review_token: saved.review_token, subject, body,
        ...(recipient ? { recipient_to: recipient } : {}) } : { status, expected_review_token: saved.review_token };
      const result = await api(`/response-drafts/${draftId}`, { method: "PATCH", body: JSON.stringify(update) });
      if (!live.current || ticket !== generation.current) return;
      const row = parse(result, draftId);
      if (row.status !== status) throw new Error("invalid");
      if (status !== "draft" && (row.subject !== saved.subject || row.body !== saved.body
        || (row.recipient_to || "") !== (saved.recipient_to || ""))) throw new Error("invalid");
      if (status === "draft" && (row.subject !== subject.trim() || row.body !== body.trim()
        || (row.recipient_to || "") !== (recipient.trim().toLowerCase() || saved.recipient_to || ""))) throw new Error("invalid");
      accept(row); onUpdated(row);
      setNotice(status === "draft" ? "Правки сохранены. Проверьте текст ещё раз перед отдельным подтверждением."
        : status === "approved" ? "Точная версия подтверждена. Письмо не отправлено." : "Черновик отклонён.");
    } catch (error) {
      if (live.current && ticket === generation.current) {
        setBlocked(!(error instanceof ApiError && error.status === 422));
        setNotice(error instanceof ApiError && error.status === 422 ? "Проверьте введённые поля. Правки сохранены в форме."
          : "Версия изменилась или результат неизвестен. Правки сохранены в форме; перечитайте черновик. Повторной записи не было.");
      }
    } finally { pending.current = false; if (live.current && ticket === generation.current) setBusy(false); }
  }
  return <section className="inbox-draft" aria-label="Проверка точной версии черновика">
    <h3>{title}</h3>
    <button disabled={busy} onClick={() => void reload()}>{saved ? "Перечитать черновик" : "Загрузить черновик для проверки"}</button>
    {notice && <p role="status">{notice}</p>}
    {saved && <>
      <input aria-label="Тема ответа" value={subject} disabled={busy || !canEdit || saved.status !== "draft"} onChange={e => setSubject(e.target.value)} />
      <input aria-label="Получатель ответа" type="email" value={recipient} disabled={busy || !canEdit || saved.status !== "draft"} onChange={e => setRecipient(e.target.value)} />
      <textarea aria-label="Текст ответа" value={body} disabled={busy || !canEdit || saved.status !== "draft"} onChange={e => setBody(e.target.value)} />
      {dirty && <p>Есть несохранённые правки — сначала сохраните, затем отдельно подтвердите.</p>}
      <div className="draft-actions">
        <button disabled={busy || blocked || !canEdit || !dirty || saved.status !== "draft" || !subject.trim() || !body.trim() || (!recipient && !!saved.recipient_to)} onClick={() => void mutate("draft")}>Сохранить правки черновика</button>
        <button disabled={busy || blocked || dirty || !canEdit || saved.status !== "draft"} onClick={() => void mutate("rejected")}>Отклонить черновик</button>
        <button disabled={busy || blocked || dirty || !canEdit || !canApprove || saved.status !== "draft"} onClick={() => void mutate("approved")}>Подтвердить черновик</button>
      </div>
    </>}
  </section>;
}
