import { ClipboardCheck } from "lucide-react";

export type ObligationRow = {
  id: number;
  contract_id?: number;
  task_id?: number;
  title: string;
  status: string;
  due_date?: string;
  result_note?: string;
  source_type: string;
  source_name: string;
  source_excerpt: string;
  confidence: number;
};

type Props = {
  collapsed: boolean;
  obligations: ObligationRow[];
  onUpdate: (item: ObligationRow, status: string) => void;
};

export function ObligationsModule({ collapsed, obligations, onUpdate }: Props) {
  const openCount = obligations.filter(
    (item) => !["fulfilled", "dismissed"].includes(item.status),
  ).length;

  return (
    <section className={`module-overlay ${collapsed ? "collapsed" : ""}`}>
      <div className="module-page">
        <section className="card management-intro">
          <div>
            <h2>Реестр обязательств</h2>
            <p>
              Обязательства выделяются из документов, сообщений и протоколов.
              Каждый вывод хранит источник и требует подтверждения.
            </p>
          </div>
          <span>{openCount} открыто</span>
        </section>
        <section className="card management-list">
          {obligations.map((item) => (
            <article key={item.id}>
              <div>
                <span className={`management-status ${item.status}`}>
                  {item.status}
                </span>
                <h3>{item.title}</h3>
                <p>
                  {item.source_name} · уверенность{" "}
                  {Math.round(item.confidence * 100)}%
                </p>
                <small>{item.source_excerpt}</small>
              </div>
              <div className="management-meta">
                <strong>
                  {item.due_date ? `до ${item.due_date}` : "срок не определён"}
                </strong>
                {item.status === "needs_confirmation" && (
                  <button onClick={() => onUpdate(item, "confirmed")}>
                    Подтвердить
                  </button>
                )}
                {item.status === "confirmed" && (
                  <button onClick={() => onUpdate(item, "in_progress")}>
                    В работу
                  </button>
                )}
                {!["fulfilled", "dismissed"].includes(item.status) && (
                  <button
                    className="complete"
                    onClick={() => onUpdate(item, "fulfilled")}
                  >
                    Исполнено
                  </button>
                )}
              </div>
            </article>
          ))}
          {!obligations.length && (
            <div className="empty">
              <ClipboardCheck />
              <p>
                Обязательства появятся после анализа нового документа,
                сообщения или протокола.
              </p>
            </div>
          )}
        </section>
      </div>
    </section>
  );
}
