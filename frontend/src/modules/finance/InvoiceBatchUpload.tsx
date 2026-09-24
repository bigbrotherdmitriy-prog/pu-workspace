import { useRef, useState } from "react";
import { Upload } from "lucide-react";
import { invoiceFileSupported, type InvoiceUploadResult } from "./invoiceUploadTransport";

export type InvoiceBatchUploadProps = {
  onUpload: (files: File[], onProgress: (message: string) => void) => Promise<InvoiceUploadResult[]>;
  onReview?: (documentId: number) => void | Promise<void>;
};

export function InvoiceBatchUpload({ onUpload, onReview }: InvoiceBatchUploadProps) {
  const filesInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const busyRef = useRef(false);
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [results, setResults] = useState<InvoiceUploadResult[]>([]);
  const [reviewing, setReviewing] = useState(false);

  function select(incoming: File[]) {
    if (busyRef.current) return;
    const supported = incoming.filter(invoiceFileSupported);
    const skipped = incoming.length - supported.length;
    setMessage(skipped ? `Пропущено файлов других форматов: ${skipped}. Поддерживаются счета PDF.` : "");
    const merged = new Map(files.map((file) => [`${file.webkitRelativePath || file.name}:${file.size}:${file.lastModified}`, file]));
    supported.forEach((file) => merged.set(`${file.webkitRelativePath || file.name}:${file.size}:${file.lastModified}`, file));
    const next = [...merged.values()];
    if (next.some((file) => file.size > 10 * 1024 * 1024)) { setError("Один из файлов больше 10 МБ. Выберите меньшие файлы."); return; }
    if (next.length > 100 || next.reduce((sum, file) => sum + file.size, 0) > 100 * 1024 * 1024) {
      setError("За один пакет можно выбрать до 100 счетов общим размером до 100 МБ. Загрузите папку частями."); return;
    }
    setError(""); setFiles(next);
  }

  async function upload() {
    if (busyRef.current || !files.length) return;
    busyRef.current = true; setBusy(true); setError("");
    try {
      const completed = await onUpload(files, setMessage);
      setResults((current) => [...current, ...completed]);
      setFiles([]);
      const failed = completed.filter((item) => item.error).length;
      setMessage(`Обработано: ${completed.length - failed}. С ошибками: ${failed}. Проверьте каждый счёт перед импортом.`);
    } catch (reason) { setError((reason as Error).message); }
    finally { busyRef.current = false; setBusy(false); }
  }

  return <section aria-label="Массовая загрузка счетов">
    <div className={`dds-invoice-drop ${dragging ? "active" : ""}`} onDragOver={(event) => { event.preventDefault(); if (!busy) setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={(event) => { event.preventDefault(); setDragging(false); select(Array.from(event.dataTransfer.files)); }}>
      <Upload /><div><strong>Перетащите сюда счета PDF — один или несколько</strong><small>Или выберите файлы и папку кнопками ниже. До 100 файлов, по 10 МБ, всего до 100 МБ. Каждый счёт проверяется человеком перед импортом.</small></div>
    </div>
    <input ref={filesInput} type="file" hidden multiple accept=".pdf,application/pdf" aria-label="Выбрать файлы счетов" onChange={(event) => { select(Array.from(event.target.files || [])); event.target.value = ""; }} />
    <input ref={folderInput} type="file" hidden multiple {...{ webkitdirectory: "" }} aria-label="Выбрать папку счетов" onChange={(event) => { select(Array.from(event.target.files || [])); event.target.value = ""; }} />
    <div className="dds-head-actions">
      <button type="button" className="secondary" disabled={busy} onClick={() => filesInput.current?.click()}>Добавить счета</button>
      <button type="button" className="secondary" disabled={busy} onClick={() => folderInput.current?.click()}>Добавить счета из папки</button>
      <button type="button" disabled={busy || !files.length} onClick={() => void upload()}>{busy ? "Обработка счетов…" : `Загрузить и разобрать (${files.length})`}</button>
      {!!files.length && <button type="button" className="secondary" disabled={busy} onClick={() => setFiles([])}>Очистить выбор</button>}
    </div>
    {!!files.length && <details><summary>Выбрано счетов: {files.length}</summary><ul>{files.map((file, index) => <li key={index}>{file.webkitRelativePath || file.name}</li>)}</ul></details>}
    {message && <p role="status">{message}</p>}
    {error && <p role="alert">{error}</p>}
    {!!results.length && <ul aria-label="Результаты загрузки счетов">{results.map((item, index) => <li key={index}><strong>{item.name}</strong> — {item.error || "Готов к проверке"}{item.documentIds.map((id) => <button key={id} type="button" disabled={!onReview || reviewing || busy} onClick={async () => {
      setReviewing(true); setError("");
      try { await onReview?.(id); } catch (reason) { setError((reason as Error).message); } finally { setReviewing(false); }
    }}>Проверить счёт</button>)}</li>)}</ul>}
  </section>;
}
