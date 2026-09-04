import { useEffect, useState } from "react";
import { FileScan, FileText, RefreshCw } from "lucide-react";
import { api } from "../../api/client";

export type DocumentListItem = {
  id: number; name: string; source: string; status: string; current_version: number;
  extraction_method?: string; extraction_quality?: string; ocr_pages?: number; ocr_updated_at?: string;
  ocr_reprocess_available?: boolean; ocr_reprocess_unavailable_reason?: string;
};
export type DocumentCard = DocumentListItem & {
  mime_type?: string; source_url?: string; summary?: string;
  versions: { version: number; created_at: string }[];
  links: { tasks: number; risks: number; decisions: number; drafts: number };
};

type Props = {
  collapsed: boolean;
  knowledgeMode: boolean;
  documents: DocumentListItem[];
  selected: DocumentCard | null;
  onSelect: (document: DocumentListItem) => void;
  projectId: number;
  onOcrComplete: () => void;
};

type OcrBatch = { job_id: number; status: string; result?: { total: number; processed: unknown[]; skipped: unknown[]; tasks: number; risks: number; decisions: number; drafts: number }; error?: string };

export function DocumentsModule({ collapsed, knowledgeMode, documents, selected, onSelect, projectId, onOcrComplete }: Props) {
  const [previousVersion, setPreviousVersion] = useState(0);
  const [currentVersion, setCurrentVersion] = useState(0);
  const [comparison, setComparison] = useState<null | {
    added_lines: number; removed_lines: number; changed_lines: number;
    unchanged: boolean; preview: string[]; preview_truncated: boolean;
  }>(null);
  const [comparisonError, setComparisonError] = useState("");
  const [ocrBatch, setOcrBatch] = useState<OcrBatch | null>(null);
  const [ocrError, setOcrError] = useState("");

  useEffect(() => {
    const versions = selected?.versions.map((item) => item.version) || [];
    setCurrentVersion(versions[0] || 0);
    setPreviousVersion(versions[1] || versions[0] || 0);
    setComparison(null);
    setComparisonError("");
  }, [selected?.id, selected?.versions.length]);

  useEffect(() => {
    if (!ocrBatch || !["queued", "running"].includes(ocrBatch.status)) return;
    const timer = window.setInterval(async () => {
      try {
        const next: OcrBatch = await api(`/projects/${projectId}/documents/ocr-batches/${ocrBatch.job_id}`);
        setOcrBatch(next);
        if (next.status === "succeeded") onOcrComplete();
        if (next.status === "dead_letter") setOcrError(next.error || "OCR завершился с ошибкой");
      } catch (error) {
        setOcrError((error as Error).message);
      }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [ocrBatch?.job_id, ocrBatch?.status, onOcrComplete, projectId]);

  async function startOcr(documentIds?: number[]) {
    try {
      setOcrError("");
      setOcrBatch(await api(`/projects/${projectId}/documents/ocr-batches`, {
        method: "POST", body: JSON.stringify({ document_ids: documentIds || null }),
      }));
    } catch (error) {
      setOcrError((error as Error).message);
    }
  }

  const ocrBusy = ocrBatch && ["queued", "running"].includes(ocrBatch.status);
  const ocrResult = ocrBatch?.status === "succeeded" ? ocrBatch.result : null;
  const eligibleDocuments = documents.filter((item) => item.ocr_reprocess_available !== false);
  const unavailableOcrMessage = selected?.ocr_reprocess_unavailable_reason === "format_not_supported"
    ? "Повторное OCR доступно только для PDF и изображений."
    : "Повторное OCR недоступно: исходный файл не сохранён. Загрузите файл ещё раз, чтобы распознать его заново.";

  async function compareVersions() {
    if (!selected || !previousVersion || !currentVersion) return;
    try {
      setComparisonError("");
      setComparison(await api(`/history/documents/${selected.id}/compare?previous=${previousVersion}&current=${currentVersion}`));
    } catch (error) {
      setComparisonError((error as Error).message);
    }
  }

  return <section className={`documents-overlay ${collapsed ? "collapsed" : ""}`}>
    <div className="documents-layout">
      <div className="card">
        <div className="card-head"><div><h2>{knowledgeMode ? "Центр знаний" : "Реестр документов"}</h2><p>{knowledgeMode ? "Поиск по названиям, сводкам и извлечённому тексту" : `Найдено: ${documents.length}`}</p></div>
          <button disabled={Boolean(ocrBusy) || !eligibleDocuments.length} onClick={() => void startOcr(eligibleDocuments.map((item) => item.id))} title={eligibleDocuments.length ? "Повторно распознать доступные PDF и изображения, не изменяя оригиналы" : "Нет документов с доступным оригиналом для повторного OCR"}><RefreshCw className={ocrBusy ? "spin" : ""} />{ocrBusy ? (ocrBatch?.status === "queued" ? "В очереди" : "Распознаю…") : "Повторно распознать доступные сканы"}</button>
        </div>
        {ocrResult && <div className="ocr-batch-result" role="status"><strong>OCR завершён</strong><span>Распознано: {ocrResult.processed.length} из {ocrResult.total}</span><span>Пропущено: {ocrResult.skipped.length}</span><span>Новых предложений: задач {ocrResult.tasks}, рисков {ocrResult.risks}, решений {ocrResult.decisions}</span></div>}
        {ocrError && <p className="version-error">{ocrError}</p>}
        <div className="document-register">
          {documents.map((item) => <button className={selected?.id === item.id ? "selected" : ""} onClick={() => onSelect(item)} key={item.id}>
            <FileText /><span><strong>{item.name}</strong><small>{item.source} · версия {item.current_version || 1} · {item.status}</small>{item.extraction_method && <small className={`ocr-quality ${item.extraction_quality || ""}`}>OCR: {item.extraction_method} · качество {item.extraction_quality || "не определено"}{item.ocr_pages ? ` · страниц ${item.ocr_pages}` : ""}</small>}</span>
          </button>)}
          {!documents.length && <div className="empty"><FileText /><p>Документы не найдены</p></div>}
        </div>
      </div>
      <div className="card document-detail">
        {selected ? <>
          <div className="card-head"><div><h2>{selected.name}</h2><p>{selected.mime_type || "Документ"}</p></div>
            {selected.source_url && <a className="source-link" href={selected.source_url} target="_blank" rel="noreferrer">Открыть оригинал</a>}
          </div>
          <div className="document-links"><span>Задачи <strong>{selected.links.tasks}</strong></span><span>Риски <strong>{selected.links.risks}</strong></span><span>Решения <strong>{selected.links.decisions}</strong></span><span>Черновики <strong>{selected.links.drafts}</strong></span></div>
          <h3>Краткая сводка</h3><p className="document-summary">{selected.summary || "Сводка появится после анализа содержимого."}</p>
          <div className="document-ocr-actions">
            {selected.ocr_reprocess_available === false
              ? <span role="note">{unavailableOcrMessage}</span>
              : <button disabled={Boolean(ocrBusy)} onClick={() => void startOcr([selected.id])}><FileScan />Повторно распознать этот документ</button>}
            {selected.extraction_method && <span>Метод: {selected.extraction_method} · качество: {selected.extraction_quality || "не определено"} · OCR-страниц: {selected.ocr_pages || 0}</span>}
          </div>
          <p className="versions">Версий: {selected.versions.length || 1}</p>
          {selected.versions.length > 1 && <section className="version-comparison">
            <h3>Что изменилось между версиями</h3>
            <div className="version-comparison-controls">
              <select value={previousVersion} onChange={(event) => setPreviousVersion(Number(event.target.value))}>
                {selected.versions.map((item) => <option value={item.version} key={item.version}>Версия {item.version}</option>)}
              </select>
              <span>→</span>
              <select value={currentVersion} onChange={(event) => setCurrentVersion(Number(event.target.value))}>
                {selected.versions.map((item) => <option value={item.version} key={item.version}>Версия {item.version}</option>)}
              </select>
              <button disabled={previousVersion === currentVersion} onClick={() => void compareVersions()}>Сравнить</button>
            </div>
            {comparisonError && <p className="version-error">{comparisonError}</p>}
            {comparison && <div className="version-comparison-result">
              <div><span>Добавлено</span><strong>+{comparison.added_lines}</strong></div>
              <div><span>Удалено</span><strong>−{comparison.removed_lines}</strong></div>
              <div><span>Изменено</span><strong>{comparison.changed_lines}</strong></div>
              {comparison.unchanged
                ? <p>Версии не отличаются по извлечённому тексту.</p>
                : <pre>{comparison.preview.join("\n")}{comparison.preview_truncated ? "\n… сравнение сокращено" : ""}</pre>}
            </div>}
          </section>}
        </> : <div className="empty"><FileText /><p>Выберите документ слева</p></div>}
      </div>
    </div>
  </section>;
}
