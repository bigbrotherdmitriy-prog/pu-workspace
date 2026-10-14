import { useEffect, useState } from "react";
import { api, ApiError } from "../../api/client";
import "./backlog-triage.css";

/**
 * «Разобрать очередь»: bulk close stale tasks / dismiss stale drafts.
 * Every run is previewed first (dry run: count + up to 20 titles), the
 * confirm button states the exact number, and the result toast keeps an
 * «Отменить» button that restores the previous statuses of the batch.
 * Nothing is deleted: tasks become «Отменена», drafts «Отклонён».
 */
export type TriageKind = "tasks" | "drafts";
export type TriagePreset = "auto30" | "all60" | "manual";

type Preview = { count: number; sample: Array<{ id: number; title: string; status: string }> };
type Toast = { batchId?: number; text: string };

export const TRIAGE_PRESETS: Array<{ id: TriagePreset; days?: number; label: (days: number) => string }> = [
  { id: "auto30", days: 30, label: (days) => `Автоизвлечённые старше ${days} ${pluralRu(days, ["дня", "дней", "дней"])} без изменений` },
  { id: "all60", days: 60, label: (days) => `Все старше ${days} ${pluralRu(days, ["дня", "дней", "дней"])} без изменений` },
  { id: "manual", label: () => "Выбранные вручную галочками" },
];

export function pluralRu(count: number, forms: [string, string, string]): string {
  const mod10 = count % 10, mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return forms[0];
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return forms[1];
  return forms[2];
}

const WORDS = {
  tasks: { noun: ["задачу", "задачи", "задач"] as [string, string, string], verb: "Закрыть", done: "Закрыто",
    doneNoun: ["задача", "задачи", "задач"] as [string, string, string],
    endpoint: "tasks/bulk-close", idsKey: "task_ids", hint: "Задачи получат статус «Отменена». Ничего не удаляется." },
  drafts: { noun: ["черновик", "черновика", "черновиков"] as [string, string, string], verb: "Отклонить", done: "Отклонено",
    doneNoun: ["черновик", "черновика", "черновиков"] as [string, string, string],
    endpoint: "drafts/bulk-dismiss", idsKey: "draft_ids",
    hint: "Черновики получат статус «Отклонён». Подтверждённые, отправленные и стоящие в очереди письма не затрагиваются." },
};

function presetFilter(preset: TriagePreset, kind: TriageKind, selectedIds: number[], days: number) {
  if (preset === "auto30") return { older_than_days: days, untouched_only: true, auto_extracted_only: true };
  if (preset === "all60") return { older_than_days: days, untouched_only: true };
  return { [WORDS[kind].idsKey]: selectedIds };
}

function errorText(error: unknown): string {
  if (error instanceof ApiError && error.code === "bulk_preview_stale") {
    return "Очередь изменилась после предпросмотра. Обновите предпросмотр и подтвердите ещё раз.";
  }
  return (error as Error).message || "Не удалось выполнить действие";
}

type Props = {
  kind: TriageKind;
  projectId: number;
  /** Ids ticked in the list; used by the «Выбранные вручную» preset. */
  selectedIds?: number[];
  /** Tells the list to show (or hide) its selection checkboxes. */
  onSelectingChange?: (selecting: boolean) => void;
  onClearSelection?: () => void;
  /** Reload the list after a run or an undo. */
  onDone: () => void;
};

export function BacklogTriagePanel({ kind, projectId, selectedIds = [], onSelectingChange, onClearSelection, onDone }: Props) {
  const words = WORDS[kind];
  const [open, setOpen] = useState(false);
  const [preset, setPreset] = useState<TriagePreset>("auto30");
  // The age threshold is editable: a backlog only three weeks old would be
  // invisible to the 30-day preset.
  const [days, setDays] = useState(30);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState<"" | "preview" | "run" | "undo">("");
  const [error, setError] = useState("");
  const [toast, setToast] = useState<Toast | null>(null);
  const selectionKey = selectedIds.join(",");

  useEffect(() => { onSelectingChange?.(open && preset === "manual"); }, [open, preset, onSelectingChange]);
  // A stale preview must never be confirmed: any change of the input drops it.
  useEffect(() => { setPreview(null); }, [preset, projectId, days, preset === "manual" ? selectionKey : ""]);

  const choosePreset = (id: TriagePreset) => {
    setPreset(id);
    const found = TRIAGE_PRESETS.find((item) => item.id === id);
    if (found?.days) setDays(found.days);
  };
  const labelFor = (item: (typeof TRIAGE_PRESETS)[number]) => item.label(item.id === preset ? days : item.days || 0);
  const presetLabel = labelFor(TRIAGE_PRESETS.find((item) => item.id === preset)!);
  const base = `/projects/${projectId}/${words.endpoint}`;

  async function runPreview() {
    setBusy("preview"); setError("");
    try {
      const result = await api<Preview>(base, {
        method: "POST",
        body: JSON.stringify({ filter: presetFilter(preset, kind, selectedIds, days), dry_run: true, reason: "" }),
      });
      setPreview(result);
      setReason((current) => current || `Разбор очереди: ${presetLabel.toLocaleLowerCase("ru-RU")}`);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy("");
    }
  }

  async function confirm() {
    if (!preview) return;
    setBusy("run"); setError("");
    try {
      const result = await api<{ batch_id: number; count: number }>(base, {
        method: "POST",
        body: JSON.stringify({
          filter: presetFilter(preset, kind, selectedIds, days), dry_run: false,
          reason: reason.trim(), expected_count: preview.count,
        }),
      });
      setToast({ batchId: result.batch_id, text: `${words.done}: ${result.count} ${pluralRu(result.count, words.doneNoun)}.` });
      setPreview(null); setOpen(false);
      onClearSelection?.();
      onDone();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy("");
    }
  }

  async function undo(batchId: number) {
    setBusy("undo");
    try {
      const result = await api<{ restored: number; skipped: number[] }>(`${base}/${batchId}/undo`, { method: "POST" });
      setToast({ text: result.skipped.length
        ? `Восстановлено: ${result.restored}. Не тронуты ${result.skipped.length} — их уже изменили вручную.`
        : `Восстановлено: ${result.restored}. Всё вернулось как было.` });
      onDone();
    } catch (e) {
      setToast({ batchId, text: errorText(e) });
    } finally {
      setBusy("");
    }
  }

  const canPreview = preset === "manual" ? selectedIds.length > 0 : days > 0;
  const confirmLabel = preview
    ? `${words.verb} ${preview.count} ${pluralRu(preview.count, words.noun)} (можно отменить)` : "";

  return <div className="backlog-triage">
    <button type="button" className="secondary backlog-triage-toggle" aria-expanded={open}
      onClick={() => { setOpen((value) => !value); setError(""); }}>Разобрать очередь</button>
    {open && <section className="backlog-triage-panel" aria-label="Разобрать очередь">
      <p className="backlog-triage-hint">{words.hint}</p>
      <fieldset className="backlog-triage-presets">
        <legend>Что разобрать</legend>
        {TRIAGE_PRESETS.map((item) => <label key={item.id}>
          <input type="radio" name={`triage-preset-${kind}`} value={item.id} checked={preset === item.id}
            onChange={() => choosePreset(item.id)} />
          <span>{item.id === "manual" ? `${labelFor(item)} (${selectedIds.length})` : labelFor(item)}</span>
        </label>)}
      </fieldset>
      {preset !== "manual" && <label className="backlog-triage-days"><span>Старше, дней</span>
        <input type="number" min={0} max={3650} inputMode="numeric" value={days}
          onChange={(event) => setDays(Math.max(0, Math.min(3650, Math.trunc(Number(event.target.value) || 0))))} /></label>}
      {preset === "manual" && !selectedIds.length && <p className="backlog-triage-hint">Отметьте галочками нужные записи в списке ниже.</p>}
      <button type="button" disabled={!canPreview || Boolean(busy)} onClick={() => void runPreview()}>
        {busy === "preview" ? "Считаю…" : "Показать, что попадёт"}
      </button>
      {error && <p className="backlog-triage-error" role="alert">{error}</p>}
      {preview && <div className="backlog-triage-preview" aria-live="polite">
        <strong>Найдено: {preview.count}</strong>
        {preview.count > 0 && <ul aria-label="Примеры">
          {preview.sample.map((row) => <li key={row.id}>{row.title}</li>)}
          {preview.count > preview.sample.length && <li className="backlog-triage-more">и ещё {preview.count - preview.sample.length}</li>}
        </ul>}
        {preview.count > 0 && <>
          <label className="backlog-triage-reason"><span>Причина (сохранится в истории)</span>
            <textarea value={reason} maxLength={2000} onChange={(event) => setReason(event.target.value)} /></label>
          <button type="button" className="complete" disabled={Boolean(busy) || !reason.trim()} onClick={() => void confirm()}>
            {busy === "run" ? "Выполняю…" : confirmLabel}
          </button>
        </>}
      </div>}
    </section>}
    {toast && <div className="backlog-triage-toast" role="status">
      <span>{toast.text}</span>
      {toast.batchId !== undefined && <button type="button" disabled={busy === "undo"} onClick={() => void undo(toast.batchId!)}>
        {busy === "undo" ? "Отменяю…" : "Отменить"}</button>}
      <button type="button" className="secondary" aria-label="Скрыть уведомление" onClick={() => setToast(null)}>×</button>
    </div>}
  </div>;
}
