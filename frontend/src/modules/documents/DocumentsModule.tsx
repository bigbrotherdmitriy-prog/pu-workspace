import { useEffect, useMemo, useState } from "react";
import { FileScan, FileText, RefreshCw, Search, X } from "lucide-react";
import { api } from "../../api/client";

export type DocumentListItem = {
  id: number; name: string; source: string; status: string; current_version: number;
  mime_type?: string; summary?: string; source_modified_at?: string;
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

type StatusFilter = "all" | "analyzed" | "working" | "attention";
type SortOrder = "default" | "name_asc" | "name_desc" | "modified_desc";

const sourceLabels: Record<string, string> = {
  gmail: "Gmail",
  google_drive: "Google Диск",
  google_drive_copy: "Рабочая копия Google Диска",
  google_drive_snapshot: "Снимок рабочей папки",
  google_workspace: "Google Workspace",
  local_upload: "Загружено с компьютера",
  telegram: "Telegram",
  yandex_disk: "Яндекс Диск",
  yandex_disk_copy: "Рабочая копия Яндекс Диска",
};

const statusLabels: Record<string, string> = {
  analyzed: "Проанализирован",
  discovered: "Ожидает анализа",
  indexed: "Проиндексирован",
  ready: "Готов",
  pending: "Ожидает обработки",
  queued: "В очереди",
  running: "Обрабатывается",
  processing: "Обрабатывается",
  failed: "Ошибка обработки",
  error: "Ошибка обработки",
  dead_letter: "Требует вмешательства",
};

function humanize(value: string) {
  return value.replaceAll("_", " ").replace(/^./, (letter) => letter.toLocaleUpperCase("ru-RU"));
}

function sourceLabel(source: string) {
  return sourceLabels[source] || humanize(source || "Другой источник");
}

function statusLabel(status: string) {
  return statusLabels[status] || humanize(status || "Статус не указан");
}

function statusGroup(status: string): Exclude<StatusFilter, "all"> {
  if (["analyzed", "indexed", "ready"].includes(status)) return "analyzed";
  if (["discovered", "pending", "queued", "running", "processing"].includes(status)) return "working";
  return "attention";
}

function extensionLabel(name: string, mimeType?: string) {
  const extension = name.includes(".") ? name.split(".").pop()?.toLocaleUpperCase("ru-RU") : "";
  if (extension && extension.length <= 5) return extension;
  if (mimeType?.includes("pdf")) return "PDF";
  if (mimeType?.includes("word")) return "DOCX";
  if (mimeType?.includes("sheet") || mimeType?.includes("excel")) return "XLSX";
  return "ФАЙЛ";
}

function documentTypeLabel(name: string, mimeType?: string) {
  const kind = extensionLabel(name, mimeType);
  if (kind === "PDF") return "PDF-документ";
  if (["DOC", "DOCX", "ODT"].includes(kind)) return "Документ Word";
  if (["XLS", "XLSX", "CSV"].includes(kind)) return "Таблица";
  if (["PNG", "JPG", "JPEG", "WEBP", "TIFF"].includes(kind)) return "Изображение";
  return "Документ";
}

function versionCountLabel(count: number) {
  const lastTwo = count % 100;
  const last = count % 10;
  if (last === 1 && lastTwo !== 11) return `${count} версия`;
  if (last >= 2 && last <= 4 && (lastTwo < 12 || lastTwo > 14)) return `${count} версии`;
  return `${count} версий`;
}

function documentCountLabel(count: number) {
  const lastTwo = count % 100;
  const last = count % 10;
  if (last === 1 && lastTwo !== 11) return `${count} документ`;
  if (last >= 2 && last <= 4 && (lastTwo < 12 || lastTwo > 14)) return `${count} документа`;
  return `${count} документов`;
}

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
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [sourceFilter, setSourceFilter] = useState("all");
  const [sortOrder, setSortOrder] = useState<SortOrder>("default");

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
  const sources = useMemo(() => [...new Set(documents.map((item) => item.source).filter(Boolean))]
    .sort((left, right) => sourceLabel(left).localeCompare(sourceLabel(right), "ru-RU")), [documents]);
  const documentNameCounts = useMemo(() => documents.reduce<Record<string, number>>((counts, item) => {
    const key = item.name.trim().toLocaleLowerCase("ru-RU");
    counts[key] = (counts[key] || 0) + 1;
    return counts;
  }, {}), [documents]);
  const filteredDocuments = useMemo(() => {
    const normalizedQuery = searchQuery.trim().toLocaleLowerCase("ru-RU");
    const filtered = documents.filter((item) => {
      const matchesQuery = !normalizedQuery || `${item.name} ${item.summary || ""}`
        .toLocaleLowerCase("ru-RU").includes(normalizedQuery);
      const matchesStatus = statusFilter === "all" || statusGroup(item.status) === statusFilter;
      const matchesSource = sourceFilter === "all" || item.source === sourceFilter;
      return matchesQuery && matchesStatus && matchesSource;
    });
    return [...filtered].sort((left, right) => {
      if (sortOrder === "name_asc") return left.name.localeCompare(right.name, "ru-RU", { numeric: true });
      if (sortOrder === "name_desc") return right.name.localeCompare(left.name, "ru-RU", { numeric: true });
      if (sortOrder === "modified_desc") {
        const byModified = (Date.parse(right.source_modified_at || "") || 0) - (Date.parse(left.source_modified_at || "") || 0);
        return byModified || right.id - left.id;
      }
      return 0;
    });
  }, [documents, searchQuery, sortOrder, sourceFilter, statusFilter]);
  const filtersActive = Boolean(searchQuery.trim()) || statusFilter !== "all" || sourceFilter !== "all" || sortOrder !== "default";
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

  function resetFilters() {
    setSearchQuery("");
    setStatusFilter("all");
    setSourceFilter("all");
    setSortOrder("default");
  }

  return <section className={`documents-overlay ${collapsed ? "collapsed" : ""}`}>
    <div className="documents-layout">
      <div className="card document-register-panel">
        <div className="card-head document-register-head"><div><h2>{knowledgeMode ? "Центр знаний" : "Реестр документов"}</h2><p>{documents.length === filteredDocuments.length ? documentCountLabel(documents.length) : `Показано ${filteredDocuments.length} из ${documents.length}`}</p></div>
          <button className="document-ocr-bulk" disabled={Boolean(ocrBusy) || !eligibleDocuments.length} onClick={() => void startOcr(eligibleDocuments.map((item) => item.id))} title={eligibleDocuments.length ? `Повторно распознать ${eligibleDocuments.length} доступных PDF и изображений, не изменяя оригиналы` : "Нет документов с доступным оригиналом для повторного OCR"} aria-label="Повторно распознать доступные сканы"><RefreshCw className={ocrBusy ? "spin" : ""} />{ocrBusy ? (ocrBatch?.status === "queued" ? "В очереди" : "Распознаю…") : "OCR сканов"}</button>
        </div>
        {ocrResult && <div className="ocr-batch-result" role="status" aria-live="polite"><strong>OCR завершён</strong><span>Распознано: {ocrResult.processed.length} из {ocrResult.total}</span><span>Пропущено: {ocrResult.skipped.length}</span><span>Новых предложений: задач {ocrResult.tasks}, рисков {ocrResult.risks}, решений {ocrResult.decisions}</span></div>}
        {ocrError && <p className="version-error" role="alert">{ocrError}</p>}
        <div className="document-register-tools">
          <label className="document-search"><span>Поиск по названию или сводке</span><Search aria-hidden="true" /><input value={searchQuery} onChange={(event) => setSearchQuery(event.target.value)} placeholder="Поиск по названию или сводке" />{searchQuery && <button type="button" onClick={() => setSearchQuery("")} aria-label="Очистить поиск"><X /></button>}</label>
          <div className="document-filter-row">
            <label><span>Статус</span><select aria-label="Статус" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value as StatusFilter)}><option value="all">Все статусы</option><option value="analyzed">Обработаны</option><option value="working">В работе</option><option value="attention">Требуют внимания</option></select></label>
            <label><span>Источник</span><select aria-label="Источник" value={sourceFilter} onChange={(event) => setSourceFilter(event.target.value)}><option value="all">Все источники</option>{sources.map((source) => <option value={source} key={source}>{sourceLabel(source)}</option>)}</select></label>
            <label><span>Сортировка</span><select aria-label="Сортировка" value={sortOrder} onChange={(event) => setSortOrder(event.target.value as SortOrder)}><option value="default">Сначала новые</option><option value="name_asc">По названию: А–Я</option><option value="name_desc">По названию: Я–А</option><option value="modified_desc">По дате изменения</option></select></label>
          </div>
          <div className="document-quick-filters" aria-label="Быстрые фильтры">
            {([[
              "all", "Все", documents.length,
            ], [
              "analyzed", "Обработаны", documents.filter((item) => statusGroup(item.status) === "analyzed").length,
            ], [
              "attention", "Требуют внимания", documents.filter((item) => statusGroup(item.status) === "attention").length,
            ]] as [StatusFilter, string, number][]).map(([value, label, count]) => <button type="button" className={statusFilter === value ? "selected" : ""} aria-pressed={statusFilter === value} onClick={() => setStatusFilter(value)} key={value}>{label}<span>{count}</span></button>)}
            {filtersActive && <button type="button" className="document-reset-filters" onClick={resetFilters}>Сбросить фильтры</button>}
          </div>
        </div>
        <div className="document-register" aria-label="Список документов">
          {filteredDocuments.map((item) => <button type="button" className={selected?.id === item.id ? "selected" : ""} aria-current={selected?.id === item.id ? "true" : undefined} onClick={() => onSelect(item)} key={item.id} title={`${item.name} · документ № ${item.id}`}>
            <span className="document-file-kind" aria-hidden="true"><FileText /><small>{extensionLabel(item.name, item.mime_type)}</small></span>
            <span className="document-register-copy"><strong>{item.name}</strong><span className="document-register-meta"><small className={`document-status ${statusGroup(item.status)}`}>{statusLabel(item.status)}</small><small>{sourceLabel(item.source)}</small><small>Версия {item.current_version || 1}</small><small>№ {item.id}</small></span>{documentNameCounts[item.name.trim().toLocaleLowerCase("ru-RU")] > 1 && <small className="document-duplicate-note">Ещё {documentNameCounts[item.name.trim().toLocaleLowerCase("ru-RU")] - 1} с таким названием</small>}{item.extraction_method && <small className={`ocr-quality ${item.extraction_quality || ""}`}>OCR: {humanize(item.extraction_method)} · {item.ocr_pages ? `${item.ocr_pages} стр.` : "страницы не указаны"}</small>}</span>
          </button>)}
          {!filteredDocuments.length && <div className="empty document-register-empty"><FileText /><strong>{documents.length ? "По заданным условиям документы не найдены" : "Документы не найдены"}</strong><p>{documents.length ? "Измените запрос или сбросьте фильтры." : "Подключите источник или загрузите файл — он появится здесь."}</p>{documents.length > 0 && <button type="button" onClick={resetFilters}>Сбросить фильтры</button>}</div>}
        </div>
      </div>
      <div className="card document-detail">
        {selected ? <>
          <div className="card-head"><div><h2>{selected.name}</h2><p>{documentTypeLabel(selected.name, selected.mime_type)} · {sourceLabel(selected.source)} · {statusLabel(selected.status)}</p></div>
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
          <p className="versions">{versionCountLabel(selected.versions.length || 1)}</p>
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
