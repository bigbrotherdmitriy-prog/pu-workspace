import { useEffect, useRef, useState } from "react";
import { api } from "../../api/client";

type DecisionEvent = { id: number; created_at: string; user_id: number; decision: "approved" | "rejected"; comment: string | null };
type History = { events: DecisionEvent[]; has_more: boolean };

export function CustomerDecisionHistory({ projectId, actId }: { projectId: number; actId: number }) {
  const mounted = useRef(true);
  const [history, setHistory] = useState<History | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  async function load() {
    setLoading(true);
    setError("");
    try {
      const result = await api<History>(`/customer/projects/${projectId}/ks-acts/${actId}/decision-history`);
      if (mounted.current) setHistory(result);
    } catch (e) {
      if (mounted.current) setError(e instanceof Error ? e.message : "Не удалось загрузить историю решений");
    } finally {
      if (mounted.current) setLoading(false);
    }
  }
  return <div>
    <button type="button" disabled={loading} onClick={() => void load()}>{loading ? "Загрузка истории…" : "История решений"}</button>
    {error && <p className="cust-error" role="alert">{error}</p>}
    {history && <section aria-label="История решений">
      {history.events.length === 0 && <p className="cust-note">История решений пока пуста.</p>}
      {history.events.length > 0 && <ul>{history.events.map((event) => <li key={event.id}>
        <time dateTime={event.created_at}>{event.created_at.replace("T", " ")}</time>
        {" · "}<span>{event.decision === "approved" ? "Согласовано" : "Отклонено"}</span>
        {event.comment && <p>{event.comment}</p>}
      </li>)}</ul>}
      {history.has_more && <p className="cust-note">Показаны последние события.</p>}
    </section>}
  </div>;
}
