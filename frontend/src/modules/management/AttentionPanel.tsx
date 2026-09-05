import type { AttentionItem } from "./managementReadModel";
import type { LoadState } from "./useManagementCenter";

type Props = {
  state: LoadState;
  items: AttentionItem[];
  total: number;
  error?: string | null;
  onRetry?: () => void;
  onSelect: (item: AttentionItem) => void;
};

const kindLabel: Record<AttentionItem["kind"], string> = {
  overdue_obligation: "Просроченное обязательство",
  obligation_review: "Обязательство на проверку",
  obligation: "Обязательство",
  overdue_task: "Просроченная задача",
  task: "Задача",
  risk: "Риск",
  decision: "Решение",
};

export function AttentionPanel({ state, items, total, error, onRetry, onSelect }: Props) {
  return <section className="management-card" aria-labelledby="management-attention-title">
    <header><div><span className="management-eyebrow">ЦЕНТР УПРАВЛЕНИЯ</span>
      <h2 id="management-attention-title">Требует внимания</h2></div>
      {state !== "loading" && <span className="management-count">{total}</span>}</header>
    {state === "idle" && <p className="management-muted">Выберите проект, чтобы увидеть контрольный список.</p>}
    {state === "loading" && <p role="status">Загружаем актуальное состояние…</p>}
    {state === "error" && <div role="alert" className="management-error"><p>{error || "Не удалось загрузить данные."}</p>
      {onRetry && <button type="button" onClick={onRetry}>Повторить</button>}</div>}
    {state === "empty" && <div className="management-empty"><strong>Сейчас ничего не требует внимания</strong>
      <p>Новые подтверждённые факты появятся здесь автоматически.</p></div>}
    {(state === "ready" || state === "empty") && items.length > 0 && <ul className="attention-list">
      {items.map((item) => <li key={`${item.entityType}:${item.entityId}`} className={`priority-${item.priority}`}>
        <button type="button" onClick={() => onSelect(item)}>
          <span className="management-kind">{kindLabel[item.kind]}</span>
          <strong>{item.title}</strong>
          <span>{item.explanation === "deadline_passed" ? "Срок прошёл" : item.explanation === "human_review_required" ? "Нужна проверка человека" : item.status}</span>
          {item.dueAt && <time dateTime={item.dueAt}>{new Date(item.dueAt).toLocaleString("ru-RU")}</time>}
        </button>
      </li>)}
    </ul>}
  </section>;
}
