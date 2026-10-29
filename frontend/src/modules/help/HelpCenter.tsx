import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "../../api/client";
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

type Step = { key: string; title: string; done: boolean; screen: string; hint: string };
type Onboarding = { items: Step[]; done: number; total: number; percent: number; next: string | null };

export function HelpCenter({ active, projectId = 0, onNavigate }: { active: string; projectId?: number; onNavigate?: (screen: string) => void }) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<"start" | "ref">("ref");
  const [onboarding, setOnboarding] = useState<Onboarding | null>(null);

  useEffect(() => {
    if (projectId <= 0) { setOnboarding(null); return; }
    let alive = true;
    api<Onboarding>(`/projects/${projectId}/onboarding`)
      .then((res) => {
        if (!alive || !res || !Array.isArray(res.items)) return;
        setOnboarding(res);
        let seen = false;
        try { seen = localStorage.getItem(`pu-onboarding-seen:${projectId}`) === "1"; } catch { /* storage is optional */ }
        if (!seen && res.done < 3) {
          setTab("start");
          setOpen(true);
          try { localStorage.setItem(`pu-onboarding-seen:${projectId}`, "1"); } catch { /* storage is optional */ }
        }
      })
      .catch(() => undefined);
    return () => { alive = false; };
  }, [projectId, open]);
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);

  const found = useMemo(() => matchTopics(query), [query]);
  const current = topics.find((t) => t.screen === active) ?? null;
  const shown = picked ? topics.find((t) => t.id === picked) ?? null : query.trim() || showAll ? null : current;

  return createPortal(
    <>
      <button type="button" className="help-fab" aria-label="Справка и справочник" title="Как пользоваться" onClick={() => setOpen(true)}>?</button>
      {open && (
        <div className="help-panel" role="dialog" aria-label="Справочник">
          <div className="help-top">
            <strong className="help-heading">Справочник</strong>
            <button type="button" aria-label="Закрыть справочник" onClick={() => setOpen(false)}>×</button>
          </div>
          <div className="help-tabs" role="tablist">
            <button type="button" role="tab" aria-selected={tab === "start"} className={tab === "start" ? "help-tab help-tab-on" : "help-tab"} onClick={() => setTab("start")}>С чего начать</button>
            <button type="button" role="tab" aria-selected={tab === "ref"} className={tab === "ref" ? "help-tab help-tab-on" : "help-tab"} onClick={() => setTab("ref")}>Справочник</button>
          </div>
          {tab === "start" && (
            <section className="help-start" aria-label="С чего начать">
              {!onboarding && <p>Выберите проект, и здесь появится пошаговый план: что уже сделано и что делать дальше.</p>}
              {onboarding && (
                <>
                  <p><b>Готово {onboarding.done} из {onboarding.total}</b> ({onboarding.percent}%)</p>
                  <div className="help-bar"><div className="help-bar-fill" style={{ width: `${onboarding.percent}%` }} /></div>
                  <ol className="help-steps">
                    {onboarding.items.map((step) => (
                      <li key={step.key} className={step.done ? "help-step help-step-done" : step.key === onboarding.next ? "help-step help-step-next" : "help-step"}>
                        <span className="help-step-mark" aria-hidden="true">{step.done ? "✓" : "○"}</span>
                        <span>
                          <b>{step.title}</b>
                          <span className="help-step-hint">{step.hint}</span>
                          {!step.done && onNavigate && (
                            <button type="button" onClick={() => { onNavigate(step.screen); setOpen(false); }}>Перейти: {step.screen}</button>
                          )}
                        </span>
                      </li>
                    ))}
                  </ol>
                </>
              )}
            </section>
          )}
          {tab === "ref" && <input
            className="help-search"
            aria-label="Поиск по справочнику"
            placeholder="Например: счёт, оборудование, ДДС"
            value={query}
            onChange={(e) => { setQuery(e.target.value); setPicked(null); setShowAll(false); }}
          />}
          {tab === "ref" && shown && (
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
          {tab === "ref" && !shown && (
            <ul className="help-list">
              {found.map((t) => (
                <li key={t.id}><button type="button" onClick={() => setPicked(t.id)}>{t.title}</button></li>
              ))}
              {found.length === 0 && <li className="help-empty">Ничего не найдено. Попробуйте другое слово.</li>}
            </ul>
          )}
          {tab === "ref" && !query.trim() && !picked && (
            <section className="help-guide">
              <h3>Общее</h3>
              {generalGuide.map((g) => (
                <details key={g.title}><summary>{g.title}</summary><p>{g.text}</p></details>
              ))}
            </section>
          )}
        </div>
      )}
    </>,
    document.body,
  );
}
