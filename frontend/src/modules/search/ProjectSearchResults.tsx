import { FileText, ListTodo, Mail, ScrollText } from "lucide-react";
import "./project-search.css";

export type ProjectSearchHit = {
  id: number;
  kind: "project" | "document" | "contract" | "task" | "obligation" | "risk" | "decision" | "message";
  title: string;
  detail: string;
};

type Props = {
  query: string;
  hits: ProjectSearchHit[];
  onOpen: (hit: ProjectSearchHit) => void;
};

const icons = {
  project: ScrollText,
  document: FileText,
  contract: ScrollText,
  task: ListTodo,
  obligation: ListTodo,
  risk: Mail,
  decision: Mail,
  message: Mail,
};

const labels = {
  project: "Проект",
  document: "Документ",
  contract: "Договор",
  task: "Задача",
  obligation: "Обязательство",
  risk: "Риск",
  decision: "Решение",
  message: "Письмо",
};

export function ProjectSearchResults({ query, hits, onOpen }: Props) {
  if (query.trim().length < 2) return null;
  return <div className="project-search-results" role="listbox" aria-label="Результаты поиска по проекту">
    <div className="project-search-result-head">
      <strong>Найдено в проекте</strong><span>{hits.length}</span>
    </div>
    {hits.slice(0, 12).map((hit) => {
      const Icon = icons[hit.kind];
      return <button type="button" onClick={() => onOpen(hit)} key={`${hit.kind}-${hit.id}`}>
        <Icon />
        <span><small>{labels[hit.kind]}</small><strong>{hit.title}</strong><em>{hit.detail}</em></span>
      </button>;
    })}
    {!hits.length && <p>Совпадений в документах, договорах, задачах и письмах нет.</p>}
  </div>;
}
