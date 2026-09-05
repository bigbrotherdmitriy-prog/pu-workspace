import { evidencePinLabel, type HistoryEvent, type Obligation } from "./managementReadModel";
import type { LoadState, MutationState } from "./useManagementCenter";

type Props = {
  obligation: Obligation | null;
  history: HistoryEvent[];
  historyState: LoadState;
  historyError?: string | null;
  mutationState: MutationState;
  mutationMessage?: string | null;
  onLoadHistory: (id: number) => void;
  onTransition: (obligation: Obligation, status: string) => void;
};

export function ObligationDetailPanel(props: Props) {
  const item = props.obligation;
  if (!item) return <section className="management-card management-empty"><h2>Обязательство</h2><p>Выберите обязательство в контрольном списке.</p></section>;
  const lowConfidence = item.confidence < 0.8 || item.reviewState === "needs_review";
  return <section className="management-card" aria-labelledby="obligation-title">
    <header><div><span className="management-eyebrow">ОБЯЗАТЕЛЬСТВО · v{item.recordVersion}</span><h2 id="obligation-title">{item.title}</h2></div>
      <span className={`management-status ${lowConfidence ? "needs-review" : ""}`}>{lowConfidence ? "Нужна проверка" : item.status}</span></header>
    {lowConfidence && <p role="alert" className="management-warning">Низкая уверенность или неподтверждённое доказательство. Автоматическое завершение недоступно.</p>}
    <dl className="management-facts">
      <div><dt>Срок</dt><dd>{item.dueDate ? `${item.dueDate}${item.dueTime ? ` · ${item.dueTime}` : ""} · ${item.timezone}` : "Не установлен"}</dd></div>
      <div><dt>Источник</dt><dd>{item.sourceName}</dd></div>
      <div><dt>Уверенность</dt><dd>{new Intl.NumberFormat("ru-RU", { style: "percent", maximumFractionDigits: 0 }).format(item.confidence)}</dd></div>
      <div><dt>Задача</dt><dd>{item.taskId ? `Связана, № ${item.taskId}` : "Не связана"}</dd></div>
    </dl>
    <div className="evidence-list"><h3>Доказательства</h3>{item.evidencePins.length
      ? <ul>{item.evidencePins.map((pin, index) => <li key={index}>{evidencePinLabel(pin)}</li>)}</ul>
      : <p role="alert">Доказательство отсутствует — изменение статуса заблокировано.</p>}</div>
    <div className="management-actions">
      <button type="button" disabled={props.mutationState === "saving" || !item.evidencePins.length} onClick={() => props.onTransition(item, "confirmed")}>Подтвердить</button>
      <button type="button" disabled={props.mutationState === "saving" || lowConfidence || !item.evidencePins.length} onClick={() => props.onTransition(item, "in_progress")}>В работу</button>
    </div>
    {props.mutationMessage && <p role={props.mutationState === "conflict" || props.mutationState === "error" ? "alert" : "status"} className={`mutation-${props.mutationState}`}>{props.mutationMessage}</p>}
    <section className="management-history"><div><h3>История</h3><button type="button" onClick={() => props.onLoadHistory(item.id)}>Обновить историю</button></div>
      {props.historyState === "loading" && <p role="status">Загружаем историю…</p>}
      {props.historyState === "error" && <p role="alert">{props.historyError || "История недоступна."}</p>}
      {props.historyState === "empty" && <p>История пока пуста.</p>}
      {props.history.length > 0 && <ol>{props.history.map((event) => <li key={event.sequence}>
        <strong>{event.event}</strong><span>{event.fromStatus || "создано"} → {event.toStatus} · v{event.recordVersion}</span>
        <time dateTime={event.occurredAt}>{new Date(event.occurredAt).toLocaleString("ru-RU")}</time>
      </li>)}</ol>}
    </section>
  </section>;
}
