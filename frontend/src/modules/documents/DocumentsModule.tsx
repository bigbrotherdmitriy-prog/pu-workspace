import { FileText } from "lucide-react";

export type DocumentListItem = {
  id: number; name: string; source: string; status: string; current_version: number;
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
};

export function DocumentsModule({ collapsed, knowledgeMode, documents, selected, onSelect }: Props) {
  return <section className={`documents-overlay ${collapsed ? "collapsed" : ""}`}>
    <div className="documents-layout">
      <div className="card">
        <div className="card-head"><div><h2>{knowledgeMode ? "Центр знаний" : "Реестр документов"}</h2><p>{knowledgeMode ? "Поиск по названиям, сводкам и извлечённому тексту" : `Найдено: ${documents.length}`}</p></div></div>
        <div className="document-register">
          {documents.map((item) => <button className={selected?.id === item.id ? "selected" : ""} onClick={() => onSelect(item)} key={item.id}>
            <FileText /><span><strong>{item.name}</strong><small>{item.source} · версия {item.current_version || 1} · {item.status}</small></span>
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
          <p className="versions">Версий: {selected.versions.length || 1}</p>
        </> : <div className="empty"><FileText /><p>Выберите документ слева</p></div>}
      </div>
    </div>
  </section>;
}
