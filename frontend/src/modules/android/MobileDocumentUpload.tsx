import { useRef, useState } from "react";
import { Camera, FileUp, Upload, X } from "lucide-react";
import { api } from "../../api/client";

type Props = {
  open: boolean;
  projectId: number;
  onClose: () => void;
  onComplete: (message: string) => void;
};

const MAX_FILE_BYTES = 4 * 1024 * 1024;
const MAX_SELECTION_BYTES = 60 * 1024 * 1024;
const MAX_REQUEST_BYTES = 12 * 1024 * 1024;
const MAX_FILES_PER_REQUEST = 5;

function partitionFiles(files: File[]): File[][] {
  const batches: File[][] = [];
  let current: File[] = [];
  let currentBytes = 0;
  for (const file of files) {
    if (current.length && (current.length >= MAX_FILES_PER_REQUEST || currentBytes + file.size > MAX_REQUEST_BYTES)) {
      batches.push(current);
      current = [];
      currentBytes = 0;
    }
    current.push(file);
    currentBytes += file.size;
  }
  if (current.length) batches.push(current);
  return batches;
}

function readBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error(`Не удалось прочитать ${file.name}`));
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] || "");
    reader.readAsDataURL(file);
  });
}

export function MobileDocumentUpload({ open, projectId, onClose, onComplete }: Props) {
  const filesInput = useRef<HTMLInputElement>(null);
  const cameraInput = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [progress, setProgress] = useState("");
  if (!open) return null;

  function select(incoming: FileList | null) {
    const next = Array.from(incoming || []);
    const oversized = next.find((file) => file.size > MAX_FILE_BYTES);
    if (oversized) {
      setError(`${oversized.name}: файл больше 4 МБ`);
      return;
    }
    const merged = [...files, ...next].slice(0, 50);
    if (merged.reduce((sum, file) => sum + file.size, 0) > MAX_SELECTION_BYTES) {
      setError("Общий размер выбранных файлов больше 60 МБ");
      return;
    }
    setError("");
    setFiles(merged);
  }

  async function upload() {
    if (!files.length) return;
    let uploadedCount = 0;
    try {
      setBusy(true);
      setError("");
      const batches = partitionFiles(files);
      const total = { processed: 0, tasks: 0, risks: 0, skipped: 0 };
      for (let index = 0; index < batches.length; index += 1) {
        setProgress(`Пачка ${index + 1} из ${batches.length}`);
        const payload = await Promise.all(batches[index].map(async (file) => ({
          path: file.name,
          mime_type: file.type || "application/octet-stream",
          content_base64: await readBase64(file),
        })));
        const result = await api("/local-upload/analyze", {
          method: "POST",
          body: JSON.stringify({ project_id: projectId, files: payload }),
        });
        total.processed += result.processed;
        total.tasks += result.tasks;
        total.risks += result.risks;
        total.skipped += result.skipped.length;
        uploadedCount += batches[index].length;
      }
      setFiles([]);
      onComplete(`Обработано: ${total.processed}. Задач: ${total.tasks}. Рисков: ${total.risks}. Пропущено: ${total.skipped}.`);
      onClose();
    } catch (reason) {
      if (uploadedCount) {
        setFiles((items) => items.slice(uploadedCount));
      }
      const prefix = uploadedCount ? `Уже загружено файлов: ${uploadedCount}. ` : "";
      setError(`${prefix}${(reason as Error).message}`);
    } finally {
      setBusy(false);
      setProgress("");
    }
  }

  return <div className="mobile-upload-backdrop" role="dialog" aria-modal="true" aria-label="Загрузка документов с Android">
    <section className="mobile-upload-sheet">
      <div className="mobile-upload-head">
        <div><span>ANDROID</span><h2>Добавить документы</h2><p>Файлы отправятся на ваш сервер только после подтверждения.</p></div>
        <button type="button" aria-label="Закрыть" onClick={onClose}><X /></button>
      </div>
      <div className="mobile-upload-actions">
        <button type="button" onClick={() => filesInput.current?.click()}><FileUp /><span><strong>Выбрать файлы</strong><small>PDF, DOCX, XLSX, TXT, CSV, фото</small></span></button>
        <button type="button" onClick={() => cameraInput.current?.click()}><Camera /><span><strong>Сфотографировать</strong><small>Счёт, акт или документ</small></span></button>
      </div>
      <input ref={filesInput} hidden type="file" multiple accept=".pdf,.doc,.docx,.xls,.xlsx,.txt,.csv,image/*" onChange={(event) => select(event.target.files)} />
      <input ref={cameraInput} hidden type="file" accept="image/*" capture="environment" onChange={(event) => select(event.target.files)} />
      <div className="mobile-upload-files">
        {files.map((file, index) => <article key={`${file.name}-${index}`}><span><strong>{file.name}</strong><small>{Math.max(1, Math.round(file.size / 1024))} КБ</small></span><button type="button" aria-label={`Удалить ${file.name}`} onClick={() => setFiles((items) => items.filter((_, itemIndex) => itemIndex !== index))}><X /></button></article>)}
        {!files.length && <p>Выбранные файлы появятся здесь. Максимум 4 МБ на файл и 60 МБ за один выбор.</p>}
      </div>
      {error && <p className="mobile-upload-error">{error}</p>}
      {progress && <p className="mobile-upload-progress" aria-live="polite">{progress}</p>}
      <button className="mobile-upload-submit" type="button" disabled={!files.length || busy} onClick={() => void upload()}><Upload />{busy ? `Анализирую… ${progress}` : `Загрузить и проанализировать (${files.length})`}</button>
    </section>
  </div>;
}
