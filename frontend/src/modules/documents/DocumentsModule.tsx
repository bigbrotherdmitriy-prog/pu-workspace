import { useEffect, useState } from "react";
import { FileText } from "lucide-react";
import { api } from "../../api/client";

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
  const [previousVersion, setPreviousVersion] = useState(0);
  const [currentVersion, setCurrentVersion] = useState(0);
  const [comparison, setComparison] = useState<null | {
    added_lines: number; removed_lines: number; changed_lines: number;
    unchanged: boolean; preview: string[]; preview_truncated: boolean;
  }>(null);
  const [comparisonError, setComparisonError] = useState("");

  useEffect(() => {
    const versions = selected?.versions.map((item) => item.version) || [];
    setCurrentVersion(versions[0] || 0);
    setPreviousVersion(versions[1] || versions[0] || 0);
    setComparison(null);
    setComparisonError("");
  }, [selected?.id, selected?.versions.length]);

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
