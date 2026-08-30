import { useEffect, useState } from "react";
import { FileText, Search, X } from "lucide-react";

export type ContractDocumentTab = "recommended" | "server" | "upload" | "google";

type DocumentItem = { id: number; name: string; source: string; source_url?: string };
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
  onToggle: () => void;
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

  useEffect(() => {
    setSelectedDocumentId(props.sourceDocumentId || 0);
  }, [props.sourceDocumentId, props.open]);

  const visible = props.documents.filter((document) => {
    const source = (document.source || "").toLowerCase();
    const candidate = props.candidates.find((row) => row.document_id === document.id);
    const sourceMatches = props.tab === "recommended" ? Boolean(candidate?.score)
      : props.tab === "server" ? true : props.tab === "upload" ? !source.includes("google") : source.includes("google");
    const search = props.query.trim().toLocaleLowerCase("ru-RU");
    return sourceMatches && (!search || document.name.toLocaleLowerCase("ru-RU").includes(search));
  }).sort((left, right) => {
    const leftScore = props.candidates.find((row) => row.document_id === left.id)?.score || 0;
    const rightScore = props.candidates.find((row) => row.document_id === right.id)?.score || 0;
    return rightScore - leftScore || right.id - left.id;
  });
  const selectedDocument = props.documents.find((document) => document.id === selectedDocumentId);

  return <>
    <label>1. Документ-источник</label>
    <button type="button" title="Выбрать файл из каталога" onClick={props.onToggle}>
      {props.open ? "Закрыть ручной выбор" : "Выбрать договор самому"}
    </button>
    <button type="button" className="secondary" disabled={props.busy} onClick={props.onSuggest}>
      {props.busy ? "Анализирую реестр…" : "Найти договор по номеру, контрагенту и тексту"}
    </button>
    {props.open && <div className="contract-document-modal" role="dialog" aria-modal="true" aria-label="Ручной выбор документа договора">
      <div className="contract-document-dialog">
        <div className="contract-document-dialog-head">
          <div><span className="eyebrow">РУЧНОЙ ВЫБОР</span><h2>Найдите файл договора</h2><p>Выберите источник, введите часть названия и подтвердите конкретный файл.</p></div>
          <button type="button" className="icon-button" aria-label="Закрыть" onClick={props.onToggle}><X /></button>
        </div>
        <div className="contract-source-tabs">{tabs.map(([source, title]) =>
          <button type="button" className={props.tab === source ? "selected" : "secondary"} onClick={() => props.onTabChange(source)} key={source}>{title}</button>,
        )}</div>
        <label className="contract-document-search"><Search /><input autoFocus value={props.query} onChange={(event) => props.onQueryChange(event.target.value)} aria-label="Поиск документа по названию" placeholder="Например: ГК-08-194 или Налог-Сервис" /></label>
        <div className="contract-document-results">
          {visible.slice(0, 100).map((document) => {
            const score = props.candidates.find((row) => row.document_id === document.id)?.score;
            return <button type="button" className={selectedDocumentId === document.id ? "selected" : ""} onClick={() => setSelectedDocumentId(document.id)} key={document.id}>
              <FileText /><span><strong>{document.name}</strong><small>{score ? `${score}% совпадения · ` : ""}{document.source}</small></span><b>{selectedDocumentId === document.id ? "Выбран" : "Выбрать"}</b>
            </button>;
          })}
          {!visible.length && <div className="empty"><FileText /><p>Файлы не найдены. Смените источник или очистите поиск.</p></div>}
        </div>
        <div className="contract-document-dialog-actions">
          <span>{selectedDocument ? `Выбран: ${selectedDocument.name}` : "Сначала выберите файл в списке"}</span>
          <div>{selectedDocument?.source_url && <a href={selectedDocument.source_url} target="_blank" rel="noreferrer">Открыть оригинал</a>}<button type="button" disabled={!selectedDocumentId} onClick={() => props.onLink(selectedDocumentId)}>Привязать выбранный файл</button></div>
        </div>
      </div>
    </div>}
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
