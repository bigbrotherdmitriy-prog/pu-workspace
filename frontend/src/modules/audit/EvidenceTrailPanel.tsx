import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "../../api/client";

type Ref = { type: string; id: string; revision?: number | null };
type Actor = { kind: string; id: string };

export type EvidenceTrailItem = {
  id: string;
  occurred_at: string;
  phase: string;
  event: string;
  subject: Ref;
  linkage: "typed" | "legacy_unlinked";
  authorization: "AUTO" | "CONFIRM" | "UNKNOWN";
  outcome?: string | null;
  actor?: Actor | null;
  correlation_id?: string | null;
  source_refs: Ref[];
  evidence_refs: Ref[];
  relation_refs: Ref[];
  ai?: { confidence?: number | null; extraction_method?: string } | null;
};

type Page = { project_id: number; items: EvidenceTrailItem[]; next_cursor?: string | null };

const phaseLabels: Record<string, string> = {
  SOURCE: "Источник",
  ANALYSIS: "Анализ",
  PROPOSAL: "Предложение",
  CONTEXT_CONFIRMATION: "Подтверждение контекста",
  ACTION_AUTHORIZATION: "Разрешение действия",
  EXECUTION: "Исполнение",
  OUTCOME: "Результат",
  CORRECTION: "Коррекция",
  ACTIVITY: "Событие",
};

function refLabel(ref: Ref): string {
  return `${ref.type} · ${ref.id}${ref.revision ? ` · версия ${ref.revision}` : ""}`;
}

export function EvidenceTrailPanel({ projectId }: { projectId: number | null }) {
  const [items, setItems] = useState<EvidenceTrailItem[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const requestGeneration = useRef(0);

  const load = useCallback(async (cursor?: string, append = false) => {
    if (!projectId) {
      setItems([]);
      setNextCursor(null);
      return;
    }
    const generation = ++requestGeneration.current;
    setLoading(true);
    setError("");
    try {
      const suffix = cursor ? `?limit=50&cursor=${encodeURIComponent(cursor)}` : "?limit=50";
      const page = await api<Page>(`/api/v54/projects/${projectId}/evidence-trail${suffix}`, { cache: "no-store" });
      if (generation !== requestGeneration.current) return;
      setItems((current) => append ? [...current, ...page.items] : page.items);
      setNextCursor(page.next_cursor || null);
    } catch (reason) {
      if (generation !== requestGeneration.current) return;
      setError(reason instanceof Error ? reason.message : "Не удалось загрузить цепочку доказательств");
      if (!append) setItems([]);
    } finally {
      if (generation === requestGeneration.current) setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    requestGeneration.current += 1;
    setItems([]);
    setNextCursor(null);
    void load();
  }, [load]);

  return <section className="card evidence-trail" aria-label="Цепочка доказательств и действий">
    <div className="card-head">
      <div>
        <h2>Цепочка доказательств и действий</h2>
        <p>Источник → решение → подтверждение → исполнение → результат. Текст источников открывается отдельно по защищённому запросу.</p>
      </div>
      <button onClick={() => void load()} disabled={loading || !projectId}>Обновить</button>
    </div>
    {error && <div className="notice error" role="alert">{error}</div>}
    <div className="audit-list">
      {items.map((item) => <article key={item.id} data-linkage={item.linkage}>
        <div className="audit-dot" />
        <div>
          <div className="trail-badges">
            <span>{phaseLabels[item.phase] || item.phase}</span>
            <span className={`trail-mode ${item.authorization.toLowerCase()}`}>{item.authorization}</span>
            {item.outcome && <span>{item.outcome}</span>}
            {item.linkage === "legacy_unlinked" && <span className="trail-warning">legacy · связь неполная</span>}
          </div>
          <strong>{item.event}</strong>
          <p>{refLabel(item.subject)}</p>
          {item.actor && <small>Инициатор: {item.actor.kind} · {item.actor.id}</small>}
          {!!item.source_refs.length && <small>Источники: {item.source_refs.map(refLabel).join(", ")}</small>}
          {!!item.evidence_refs.length && <small>Доказательства: {item.evidence_refs.map(refLabel).join(", ")}</small>}
          {item.ai?.confidence != null && <small>Уверенность модели: {Math.round(item.ai.confidence * 100)}%</small>}
        </div>
        <time>{new Date(item.occurred_at).toLocaleString("ru-RU")}</time>
      </article>)}
      {!items.length && !loading && <div className="empty"><p>{projectId ? "Для проекта пока нет связанных событий" : "Сначала выберите проект"}</p></div>}
    </div>
    {loading && <p className="muted">Загрузка…</p>}
    {nextCursor && <button onClick={() => void load(nextCursor, true)} disabled={loading}>Показать ещё</button>}
  </section>;
}
