import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { ChevronRight, FileText, Folder, Home, Search, X } from "lucide-react";

export type ContractDocumentTab = "recommended" | "server" | "upload" | "google";

type DocumentItem = { id: number; name: string; source: string; source_url?: string; external_id?: string; parent_external_id?: string; mime_type?: string };
type Candidate = { document_id: number; name: string; score: number; reasons: string[] };

type Props = {
  contractId: number;
  sourceDocumentId?: number;
  open: boolean;
  busy: boolean;
  tab: ContractDocumentTab;
  query: string;
  documents: DocumentItem[];
  candidates: Candidate[];
  onOpen: () => void;
  onClose: () => void;
  onSuggest: () => void;
  onTabChange: (tab: ContractDocumentTab) => void;
  onQueryChange: (query: string) => void;
  onLink: (documentId: number) => void;
};

const tabs: [ContractDocumentTab, string][] = [
  ["recommended", "Рекомендованные"], ["server", "Сервер / реестр"],
  ["upload", "Облако / загрузки"], ["google", "Google Drive"],
];

export function ContractDocumentPicker(props: Props) {
  const [selectedDocumentId, setSelectedDocumentId] = useState(props.sourceDocumentId || 0);
  const [currentFolderId, setCurrentFolderId] = useState<string | null>(null);

  useEffect(() => {
    setSelectedDocumentId(props.sourceDocumentId || 0);
    setCurrentFolderId(null);
  }, [props.sourceDocumentId, props.open]);

  const sourceDocuments = props.documents.filter((document) => {
    const source = (document.source || "").toLowerCase();
    const candidate = props.candidates.find((row) => row.document_id === document.id);
    return props.tab === "recommended" ? Boolean(candidate?.score)
      : props.tab === "server" ? true : props.tab === "upload" ? !source.includes("google") : source.includes("google");
  });
  const folderIds = new Set(sourceDocuments.filter((item) => item.mime_type?.includes("folder")).map((item) => item.external_id).filter(Boolean));
  const search = props.query.trim().toLocaleLowerCase("ru-RU");
  const visible = sourceDocuments.filter((document) => {
    if (search) return document.name.toLocaleLowerCase("ru-RU").includes(search);
    if (currentFolderId) return document.parent_external_id === currentFolderId;
    return !document.parent_external_id || !folderIds.has(document.parent_external_id);
  }).sort((left, right) => {
    const leftFolder = left.mime_type?.includes("folder") ? 1 : 0;
    const rightFolder = right.mime_type?.includes("folder") ? 1 : 0;
    const leftScore = props.candidates.find((row) => row.document_id === left.id)?.score || 0;
    const rightScore = props.candidates.find((row) => row.document_id === right.id)?.score || 0;
    return rightFolder - leftFolder || rightScore - leftScore || left.name.localeCompare(right.name, "ru");
  });
  const folderByExternalId = useMemo(() => new Map(props.documents.filter((item) => item.external_id).map((item) => [item.external_id!, item])), [props.documents]);
  const breadcrumbs = useMemo(() => {
    const result: DocumentItem[] = [];
    let cursor = currentFolderId ? folderByExternalId.get(currentFolderId) : undefined;
    const visited = new Set<string>();
    while (cursor?.external_id && !visited.has(cursor.external_id)) {
      visited.add(cursor.external_id); result.unshift(cursor);
      cursor = cursor.parent_external_id ? folderByExternalId.get(cursor.parent_external_id) : undefined;
    }
    return result;
  }, [currentFolderId, folderByExternalId]);
  const selectedDocument = props.documents.find((document) => document.id === selectedDocumentId);

  return <>
    <label>1. Документ-источник</label>
    <button type="button" title="Выбрать файл из каталога" onClick={props.onOpen}>
      Выбрать договор самому
    </button>
    <button type="button" className="secondary" disabled={props.busy} onClick={props.onSuggest}>
      {props.busy ? "Анализирую реестр…" : "Найти договор по номеру, контрагенту и тексту"}
    </button>
    {props.open && createPortal(<div data-ai-overlay className="contract-document-modal" role="dialog" aria-modal="true" aria-label="Ручной выбор документа договора">
      <div className="contract-document-dialog">
        <div className="contract-document-dialog-head">
          <div><span className="eyebrow">РУЧНОЙ ВЫБОР</span><h2>Найдите файл договора</h2><p>Выберите источник, введите часть названия и подтвердите конкретный файл.</p></div>
          <button type="button" className="icon-button" aria-label="Закрыть" onClick={props.onClose}><X /></button>
        </div>
        <div className="contract-source-tabs">{tabs.map(([source, title]) =>
          <button type="button" className={props.tab === source ? "selected" : "secondary"} onClick={() => props.onTabChange(source)} key={source}>{title}</button>,
        )}</div>
        <label className="contract-document-search"><Search /><input autoFocus value={props.query} onChange={(event) => props.onQueryChange(event.target.value)} aria-label="Поиск документа по названию" placeholder="Например: ГК-08-194 или Налог-Сервис" /></label>
        <nav className="contract-folder-breadcrumbs" aria-label="Путь к папке">
          <button type="button" onClick={() => setCurrentFolderId(null)}><Home /> Корень проекта</button>
          {breadcrumbs.map((folder) => <span key={folder.id}><ChevronRight /><button type="button" onClick={() => setCurrentFolderId(folder.external_id || null)}>{folder.name}</button></span>)}
        </nav>
        <div className="contract-document-results">
          {visible.slice(0, 100).map((document) => {
            const score = props.candidates.find((row) => row.document_id === document.id)?.score;
            const selected = selectedDocumentId === document.id;
            const isFolder = document.mime_type?.includes("folder");
            if (isFolder) return <button type="button" className="contract-folder-row" onClick={() => setCurrentFolderId(document.external_id || null)} key={document.id}><Folder /><span><strong>{document.name}</strong><small>Открыть папку</small></span><ChevronRight /></button>;
            return <label className={selected ? "selected" : ""} key={document.id}>
              <input
                type="radio"
                name={`contract-${props.contractId}-source-document`}
                value={document.id}
                checked={selected}
                onChange={() => setSelectedDocumentId(document.id)}
                aria-label={`Выбрать файл ${document.name}`}
              />
              <FileText /><span><strong>{document.name}</strong><small>{score ? `${score}% совпадения · ` : ""}{document.source}</small></span><b>{selected ? "Выбран" : "Выбрать"}</b>
            </label>;
          })}
          {!visible.length && <div className="empty"><FileText /><p>В этой папке файлы не найдены. Вернитесь выше или очистите поиск.</p></div>}
        </div>
        <div className="contract-document-dialog-actions">
          <span aria-live="polite">{selectedDocument ? `Выбран: ${selectedDocument.name}` : "Сначала выберите файл в списке"}</span>
          <div>{selectedDocument?.source_url && <a href={selectedDocument.source_url} target="_blank" rel="noreferrer">Открыть оригинал</a>}<button type="button" disabled={!selectedDocumentId} onClick={() => props.onLink(selectedDocumentId)}>Привязать выбранный файл</button></div>
        </div>
      </div>
    </div>, document.body)}
    {props.tab === "recommended" && !props.candidates.length && <small>Нажмите «Найти договор…»: система проверит не только имя файла, но и извлечённый текст.</small>}
    {props.candidates.slice(0, 3).map((candidate) => <div className="contract-candidate" key={candidate.document_id}>
      <span><strong>{candidate.score}% · {candidate.name}</strong><small>{candidate.reasons.join("; ") || "слабое совпадение"}</small></span>
      <button type="button" className="secondary" disabled={props.sourceDocumentId === candidate.document_id} onClick={() => props.onLink(candidate.document_id)}>
        {props.sourceDocumentId === candidate.document_id ? "Привязан" : "Привязать"}
      </button>
    </div>)}
    <small>Для ручного выбора нажмите «Выбрать договор самому». Сам файл и его название не изменяются. «Сервер / реестр» показывает все документы проекта; «Google Drive» — проиндексированные документы подключённого Диска.</small>
  </>;
}
