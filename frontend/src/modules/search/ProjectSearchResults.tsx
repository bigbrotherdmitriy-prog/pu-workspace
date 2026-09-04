import { FileText, ListTodo, Mail, ScrollText, SearchX } from "lucide-react";
import "./project-search.css";

export type ProjectSearchHit = {
  id: number;
  kind: "document" | "contract" | "task" | "message";
  title: string;
  detail: string;
};

type Props = {
  query: string;
  hits: ProjectSearchHit[];
  onOpen: (hit: ProjectSearchHit) => void;
};

const icons = {
  document: FileText,
  contract: ScrollText,
  task: ListTodo,
  message: Mail,
};

const labels = {
  document: "Документ",
  contract: "Договор",
  task: "Задача",
  message: "Письмо",
};

export function ProjectSearchResults({ query, hits, onOpen }: Props) {
  if (query.trim().length < 2) return null;
  const uniqueHits = hits.reduce<ProjectSearchHit[]>((result, hit) => {
    const fingerprint = `${hit.kind}:${hit.title.trim().toLocaleLowerCase("ru-RU")}:${hit.detail.trim().toLocaleLowerCase("ru-RU")}`;
    if (!result.some((item) => `${item.kind}:${item.title.trim().toLocaleLowerCase("ru-RU")}:${item.detail.trim().toLocaleLowerCase("ru-RU")}` === fingerprint)) {
      result.push(hit);
    }
    return result;
  }, []);
  return <div className="project-search-results" role="listbox" aria-label="Результаты поиска по проекту">
    <div className="project-search-result-head">
      <span><small>Командный поиск</small><strong>Найдено в проекте</strong></span>
      <b>{uniqueHits.length}</b>
    </div>
    <div className="project-search-query">«{query.trim()}»</div>
    {uniqueHits.slice(0, 10).map((hit) => {
      const Icon = icons[hit.kind];
      return <button type="button" role="option" aria-selected="false" onClick={() => onOpen(hit)} key={`${hit.kind}-${hit.id}`}>
        <Icon />
        <span><small>{labels[hit.kind]}</small><strong>{hit.title}</strong><em>{hit.detail}</em></span>
      </button>;
    })}
    {!uniqueHits.length && <div className="project-search-empty"><SearchX /><strong>Совпадений нет</strong><p>Проверьте номер, название или адрес отправителя.</p></div>}
  </div>;
}
