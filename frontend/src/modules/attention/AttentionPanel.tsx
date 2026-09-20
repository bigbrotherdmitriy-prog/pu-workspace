import { useEffect, useRef, useState } from "react";
import { AlertTriangle, ArrowRight, CheckCircle2, Filter } from "lucide-react";
import { api } from "../../api/client";
import "./attention.css";

type AttentionOrigin = {
  type: string;
  id: string;
  name?: string;
  excerpt?: string;
  source_version_id?: string;
  evidence_id?: string;
};

export type AttentionItem = {
  kind: string;
  entity_id: number;
  status: string;
  priority: "critical" | "high" | "normal";
  project_id: number;
  contract_id?: number;
  owner_user_id?: number;
  title: string;
  effective_date?: string;
  explanation: string;
  origin: AttentionOrigin;
  navigation: { section: string; project_id: number; entity_type: string; entity_id: number };
};

type AttentionPage = { items: AttentionItem[]; count: number; next_cursor?: string };

type Props = {
  projectId: number;
  onOpenSection: (section: string) => void;
};

const kindLabels: Record<string, string> = {
  obligation: "Обязательство",
  risk: "Риск",
  decision: "Решение",
  notification: "Уведомление",
  meeting_proposal: "Предложение из протокола",
  contact_conflict: "Конфликт контакта",
  meeting_conflict: "Конфликт совещания",
};

export function AttentionPanel({ projectId, onOpenSection }: Props) {
  const [kind, setKind] = useState("");
  const [status, setStatus] = useState("");
  const [contract, setContract] = useState("");
  const [owner, setOwner] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [items, setItems] = useState<AttentionItem[]>([]);
  const [cursor, setCursor] = useState<string>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const loadSequence = useRef(0);

  function query(nextCursor?: string) {
    const params = new URLSearchParams({ project_id: String(projectId), limit: "25" });
    if (kind) params.set("kind", kind);
    if (status.trim()) params.set("status", status.trim());
    if (contract) params.set("contract_id", contract);
    if (owner) params.set("owner_user_id", owner);
    if (dateFrom) params.set("date_from", dateFrom);
    if (dateTo) params.set("date_to", dateTo);
    if (nextCursor) params.set("cursor", nextCursor);
    return `/management/attention?${params}`;
  }

  async function load(nextCursor?: string, append = false) {
    const sequence = ++loadSequence.current;
    setLoading(true);
    setError("");
    try {
      const result = await api<AttentionPage>(query(nextCursor));
      if (sequence !== loadSequence.current) return;
      setItems((current) => append ? [...current, ...result.items] : result.items);
      setCursor(result.next_cursor || undefined);
    } catch (reason) {
      if (sequence !== loadSequence.current) return;
      setError(reason instanceof Error ? reason.message : "Не удалось загрузить контрольный список");
    } finally {
      if (sequence === loadSequence.current) setLoading(false);
    }
  }

  useEffect(() => {
    setItems([]);
    setCursor(undefined);
    const timer = window.setTimeout(() => void load(), 150);
    return () => {
      window.clearTimeout(timer);
      loadSequence.current += 1;
    };
  }, [projectId, kind, status, contract, owner, dateFrom, dateTo]);

  return <section className="card attention-register">
    <div className="attention-register-heading">
      <div><span className="eyebrow">ЕДИНЫЙ READ-MODEL</span><h2>Требует внимания</h2><p>Только доступные вам проекты, с точным источником и следующим действием.</p></div>
      <b>{items.length}{cursor ? "+" : ""}</b>
    </div>
    <div className="attention-filters" aria-label="Фильтры списка внимания">
      <Filter aria-hidden="true" />
      <select aria-label="Тип" value={kind} onChange={(event) => setKind(event.target.value)}>
        <option value="">Все типы</option>
        {Object.entries(kindLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select>
      <input aria-label="Статус" value={status} onChange={(event) => setStatus(event.target.value)} placeholder="Статус" />
      <input aria-label="ID договора" type="number" min="1" value={contract} onChange={(event) => setContract(event.target.value)} placeholder="ID договора" />
      <input aria-label="ID ответственного" type="number" min="1" value={owner} onChange={(event) => setOwner(event.target.value)} placeholder="ID ответственного" />
      <input aria-label="Дата с" type="date" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} />
      <input aria-label="Дата по" type="date" value={dateTo} onChange={(event) => setDateTo(event.target.value)} />
    </div>
    {error && <div className="error">{error}</div>}
    <div className="attention-register-list">
      {items.map((item) => <article key={`${item.kind}-${item.entity_id}`} className={`priority-${item.priority}`}>
        <div className="attention-register-icon">{item.priority === "critical" ? <AlertTriangle /> : <CheckCircle2 />}</div>
        <div>
          <span>{kindLabels[item.kind] || item.kind} · {item.status}{item.effective_date ? ` · ${new Date(`${item.effective_date}T00:00:00`).toLocaleDateString("ru-RU")}` : ""}</span>
          <strong>{item.title}</strong>
          <p>{item.explanation}</p>
          <small>Источник: {item.origin.name || `${item.origin.type} · ${item.origin.id}`}{item.origin.source_version_id ? ` · версия ${item.origin.source_version_id}` : ""}{item.origin.excerpt ? ` · ${item.origin.excerpt}` : ""}</small>
        </div>
        <button className="secondary" onClick={() => onOpenSection(item.navigation.section)}>Открыть <ArrowRight /></button>
      </article>)}
      {!loading && !items.length && <div className="attention-register-empty"><CheckCircle2 /><p>Пунктов, требующих внимания, нет.</p></div>}
    </div>
    {cursor && <button className="attention-load-more" disabled={loading} onClick={() => void load(cursor, true)}>{loading ? "Загрузка…" : "Показать ещё"}</button>}
  </section>;
}
