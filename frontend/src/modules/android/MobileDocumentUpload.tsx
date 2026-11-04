import { useEffect, useRef, useState } from "react";
import { Camera, FileUp, FolderOpen, Upload, X } from "lucide-react";
import { ApiError } from "../../api/client";
import { isOnline } from "../../offline/connectivity";
import { OfflineSessionChanged } from "../../offline/lifecycle";
import { prepareDocumentUploads, enqueuePreparedDocumentUploads, submitPreparedDocumentUpload, type PreparedDocumentUpload } from "../../offline/syncEngine";
import {
  awaitLocalUploadJobs, isSupportedLocalUploadFile, localUploadMimeType, type QueuedLocalUploadJob,
} from "../documents/localUploadJobs";

type Props = {
  open: boolean;
  projectId: number;
  userId: number;
  onClose: () => void;
  onComplete: (message: string, documentIds: number[]) => void;
  title?: string;
  description?: string;
  folderOnly?: boolean;
};

const MAX_FILE_BYTES = 4 * 1024 * 1024;
const MAX_SELECTION_BYTES = 60 * 1024 * 1024;
export function MobileDocumentUpload({
  open, projectId, userId, onClose, onComplete,
  title = "Добавить документы",
  description = "Файлы отправятся в выбранный проект только после нажатия «Загрузить и проанализировать».",
  folderOnly = false,
}: Props) {
  const filesInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const cameraInput = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [warning, setWarning] = useState("");
  const [progress, setProgress] = useState("");
  const [dragging, setDragging] = useState(false);
  const prepared = useRef<PreparedDocumentUpload[] | null>(null);
  const scopeKey = `${userId}:${projectId}:${open}`;
  const currentScope = useRef({ key: scopeKey, active: false, generation: 0 });
  if (currentScope.current.key !== scopeKey) currentScope.current = { key: scopeKey, active: false, generation: 0 };
  const scope = currentScope.current;
  useEffect(() => {
    prepared.current = null;
    scope.active = open;
    scope.generation += 1;
    setFiles([]); setBusy(false); setError(""); setWarning(""); setProgress(""); setDragging(false);
    return () => { scope.active = false; scope.generation += 1; };
  }, [scope, open]);
  if (!open) return null;

  function select(incoming: FileList | null) {
    if (busy || prepared.current?.length) return;
    const selected = Array.from(incoming || []);
    const next = selected.filter(isSupportedLocalUploadFile);
    const unsupported = selected.filter((file) => !isSupportedLocalUploadFile(file));
    if (unsupported.length) {
      const names = unsupported.slice(0, 3).map((file) => file.name).join(", ");
      const remainder = unsupported.length > 3 ? ` и ещё ${unsupported.length - 3}` : "";
      setWarning(`Пропущено неподдерживаемых файлов: ${unsupported.length} (${names}${remainder}). Остальные документы можно загрузить.`);
    } else {
      setWarning("");
    }
    if (!next.length) {
      setError("");
      return;
    }
    const oversized = next.find((file) => file.size > MAX_FILE_BYTES);
    if (oversized) {
      setError(`${oversized.name}: файл больше 4 МБ`);
      return;
    }
    const merged = [...files, ...next];
    if (merged.length > 50) {
      setError("Выбрано больше 50 файлов. Выберите меньшую папку или загружайте частями.");
      return;
    }
    if (merged.reduce((sum, file) => sum + file.size, 0) > MAX_SELECTION_BYTES) {
      setError("Общий размер выбранных файлов больше 60 МБ");
      return;
    }
    setError("");
    setFiles(merged);
  }

  async function upload() {
    if (!files.length || !projectId || !userId || busy) return;
    const generation = scope.generation;
    const alive = () => currentScope.current === scope && scope.active && scope.generation === generation;
    if (!alive()) return;
    let uploadedCount = 0;
    let savingOffline = false;
    try {
      setBusy(true);
      setError("");
      const batches = prepared.current?.length ? prepared.current : prepareDocumentUploads({
        userId, projectId, files: files.map(file => ({ name: file.name, path: file.webkitRelativePath || file.name, mimeType: localUploadMimeType(file), blob: file })),
      });
      prepared.current = batches;
      if (!isOnline()) {
        savingOffline = true;
        await enqueuePreparedDocumentUploads(batches);
        if (!alive()) return;
        prepared.current = null;
        setFiles([]);
        onComplete(`Сохранено на устройстве: ${files.length}. Файлы отправятся после восстановления связи.`, []);
        onClose();
        return;
      }
      const total = { processed: 0, tasks: 0, risks: 0, skipped: 0, jobs: 0 };
      const jobs: QueuedLocalUploadJob[] = [];
      // Work on a snapshot: acknowledged batches leave the pending intent, never get new IDs.
      for (let index = 0; index < batches.length; index += 1) {
        if (!alive()) return;
        setProgress(`Пачка ${index + 1} из ${batches.length}`);
        const queuedJobs = await submitPreparedDocumentUpload(batches[index]);
        if (!alive()) return;
        total.jobs += queuedJobs.length;
        jobs.push(...queuedJobs);
        uploadedCount += batches[index].uploads.length;
        prepared.current = batches.slice(index + 1);
      }
      const completed = await awaitLocalUploadJobs(projectId, jobs, message => { if (alive()) setProgress(message); });
      if (!alive()) return;
      total.processed = completed.processed;
      total.tasks = completed.tasks;
      total.risks = completed.risks;
      total.skipped = completed.skipped;
      prepared.current = null;
      setFiles([]);
      onComplete(
        `Обработано: ${total.processed}. Задач: ${total.tasks}. Рисков: ${total.risks}. Пропущено: ${total.skipped}.`,
        completed.documents,
      );
      onClose();
    } catch (reason) {
      if (reason instanceof OfflineSessionChanged || !alive()) return;
      const remaining = files.slice(uploadedCount);
      if (!savingOffline && remaining.length && ((reason instanceof ApiError && reason.status === null) || !isOnline())) {
        try {
          await enqueuePreparedDocumentUploads(prepared.current || []);
          if (!alive()) return;
          prepared.current = null;
          setFiles([]);
          onComplete(
            `Загружено: ${uploadedCount}. Сохранено на устройстве: ${remaining.length}; отправятся после восстановления связи.`,
            [],
          );
          onClose();
          return;
        } catch (queueError) {
          if (alive()) {
            if (uploadedCount) setFiles(items => items.slice(uploadedCount));
            setError(`${(queueError as Error).message}. Повтор отправит ту же пачку.`);
          }
          return;
        }
      }
      if (uploadedCount) {
        setFiles((items) => items.slice(uploadedCount));
      }
      if (reason instanceof ApiError && [400, 401, 403, 404, 413, 422].includes(reason.status || 0)) {
        prepared.current = null; // Definite admission denial: allow correcting the selection.
      }
      const prefix = uploadedCount ? `Уже загружено файлов: ${uploadedCount}. ` : "";
      setError(`${prefix}${(reason as Error).message}`);
    } finally {
      if (alive()) {
        setBusy(false);
        setProgress("");
      }
    }
  }

  return <div className="mobile-upload-backdrop" role="dialog" aria-modal="true" aria-label="Загрузка документов">
    <section className="mobile-upload-sheet">
      <div className="mobile-upload-head">
        <div><span>ЛОКАЛЬНАЯ ЗАГРУЗКА</span><h2>{title}</h2><p>{description}</p></div>
        <button type="button" aria-label="Закрыть" disabled={busy} onClick={onClose}><X /></button>
      </div>
      <div className={`mobile-upload-actions${folderOnly ? " folder-only" : ""}`}>
        {!folderOnly && <button type="button" disabled={busy || !!prepared.current?.length} onClick={() => filesInput.current?.click()}><FileUp /><span><strong>Выбрать файлы</strong><small>PDF, DOCX, XLSX, TXT, CSV, фото</small></span></button>}
        <button type="button" disabled={busy || !!prepared.current?.length} onClick={() => folderInput.current?.click()}><FolderOpen /><span><strong>{folderOnly ? "Выбрать папку проекта" : "Выбрать папку"}</strong><small>С вложенными файлами, до 50 файлов</small></span></button>
        {!folderOnly && <button type="button" disabled={busy || !!prepared.current?.length} onClick={() => cameraInput.current?.click()}><Camera /><span><strong>Сфотографировать</strong><small>Счёт, акт или документ</small></span></button>}
      </div>
      <input ref={filesInput} hidden type="file" multiple accept=".pdf,.docx,.xlsx,.txt,.md,.csv,.bmp,.jpg,.jpeg,.png,.tif,.tiff,.webp" onChange={(event) => select(event.target.files)} />
      <input ref={folderInput} aria-label="Файлы из папки" hidden type="file" multiple {...{ webkitdirectory: "" }} onChange={(event) => select(event.target.files)} />
      <input ref={cameraInput} hidden type="file" accept="image/*" capture="environment" onChange={(event) => select(event.target.files)} />
      <div
        className={`mobile-upload-files${dragging ? " dragging" : ""}`}
        aria-label="Перетащите файлы сюда"
        onDragEnter={(event) => { event.preventDefault(); if (!busy && !prepared.current?.length) setDragging(true); }}
        onDragOver={(event) => event.preventDefault()}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => { event.preventDefault(); setDragging(false); if (!busy && !prepared.current?.length) select(event.dataTransfer.files); }}
      >
        {files.map((file, index) => <article key={`${file.name}-${index}`}><span><strong title={file.webkitRelativePath || file.name}>{file.webkitRelativePath || file.name}</strong><small>{Math.max(1, Math.round(file.size / 1024))} КБ</small></span><button type="button" disabled={busy || !!prepared.current?.length} aria-label={`Удалить ${file.name}`} onClick={() => setFiles((items) => items.filter((_, itemIndex) => itemIndex !== index))}><X /></button></article>)}
        {!files.length && <p>{folderOnly ? "Выберите папку целиком. До подтверждения ничего не будет загружено или изменено." : "Перетащите файлы сюда или выберите выше."} Максимум 4 МБ на файл и 60 МБ за один выбор.</p>}
      </div>
      {warning && <p className="mobile-upload-warning" role="status">{warning}</p>}
      {error && <p className="mobile-upload-error">{error}</p>}
      {progress && <p className="mobile-upload-progress" aria-live="polite">{progress}</p>}
      <button className="mobile-upload-submit" type="button" disabled={!files.length || busy || !projectId || !userId} onClick={() => void upload()}><Upload />{busy ? `Анализирую… ${progress}` : `Загрузить и проанализировать (${files.length})`}</button>
    </section>
  </div>;
}
