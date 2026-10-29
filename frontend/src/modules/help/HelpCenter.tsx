import { useMemo, useState } from "react";
import { generalGuide, helpTopics, type HelpTopic } from "./helpContent";
import "./help.css";

const topics: HelpTopic[] = helpTopics.filter((t) => t.id !== "work-docs");

export function matchTopics(query: string): HelpTopic[] {
  const q = query.trim().toLowerCase();
  if (!q) return topics;
  return topics.filter((t) =>
    [t.screen, t.title, t.summary, ...t.keywords, ...t.steps, ...t.tips].some((part) => part.toLowerCase().includes(q)),
  );
}

export function HelpCenter({ active }: { active: string }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);

  const found = useMemo(() => matchTopics(query), [query]);
  const current = topics.find((t) => t.screen === active) ?? null;
  const shown = picked ? topics.find((t) => t.id === picked) ?? null : query.trim() || showAll ? null : current;

  return (
    <>
      <button type="button" className="help-fab" aria-label="Справка и справочник" title="Как пользоваться" onClick={() => setOpen(true)}>?</button>
      {open && (
        <div className="help-panel" role="dialog" aria-label="Справочник">
          <div className="help-top">
            <strong className="help-heading">Справочник</strong>
            <button type="button" aria-label="Закрыть справочник" onClick={() => setOpen(false)}>×</button>
          </div>
          <input
            className="help-search"
            aria-label="Поиск по справочнику"
            placeholder="Например: счёт, оборудование, ДДС"
            value={query}
            onChange={(e) => { setQuery(e.target.value); setPicked(null); setShowAll(false); }}
          />
          {shown && (
            <article className="help-topic">
              <h3>{shown.title}</h3>
              <p>{shown.summary}</p>
              <h4>Как сделать</h4>
              <ol>{shown.steps.map((s) => <li key={s}>{s}</li>)}</ol>
              <h4>Советы</h4>
              <ul>{shown.tips.map((s) => <li key={s}>{s}</li>)}</ul>
              <button type="button" onClick={() => { setPicked(null); setShowAll(true); }}>Все темы</button>
            </article>
          )}
          {!shown && (
            <ul className="help-list">
              {found.map((t) => (
                <li key={t.id}><button type="button" onClick={() => setPicked(t.id)}>{t.title}</button></li>
              ))}
              {found.length === 0 && <li className="help-empty">Ничего не найдено. Попробуйте другое слово.</li>}
            </ul>
          )}
          {!query.trim() && !picked && (
            <section className="help-guide">
              <h3>Общее</h3>
              {generalGuide.map((g) => (
                <details key={g.title}><summary>{g.title}</summary><p>{g.text}</p></details>
              ))}
            </section>
          )}
        </div>
      )}
    </>
  );
}
