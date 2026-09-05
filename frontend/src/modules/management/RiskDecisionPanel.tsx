import { evidencePinLabel, type AttentionItem, type HistoryEvent } from "./managementReadModel";
import type { LoadState, MutationState } from "./useManagementCenter";

type Props = {
  item: AttentionItem | null;
  history: HistoryEvent[];
  historyState: LoadState;
  mutationState: MutationState;
  mutationMessage?: string | null;
  onLoadHistory: (item: AttentionItem) => void;
  onTransition: (item: AttentionItem, status: string) => void;
  canManage?: boolean;
};

export function RiskDecisionPanel({ item, history, historyState, mutationState, mutationMessage, onLoadHistory, onTransition, canManage = true }: Props) {
  if (!item || (item.entityType !== "risk" && item.entityType !== "decision")) {
    return <section className="management-card management-empty"><h2>Риски и решения</h2><p>Выберите риск или решение в контрольном списке.</p></section>;
  }
  const needsReview = item.explanation === "human_review_required";
  const closeStatus = item.entityType === "risk" ? "resolved" : "executed";
  const confirmStatus = "confirmed";
  return <section className="management-card" aria-labelledby="governance-title">
    <header><div><span className="management-eyebrow">{item.entityType === "risk" ? "РИСК" : "РЕШЕНИЕ"} · v{item.recordVersion}</span>
      <h2 id="governance-title">{item.title}</h2></div><span className={`management-status ${needsReview ? "needs-review" : ""}`}>{needsReview ? "Нужна проверка" : item.status}</span></header>
    {needsReview && <p role="alert" className="management-warning">Вывод не подтверждён человеком. Исполняющие действия заблокированы.</p>}
    {!canManage && <p role="status" className="management-warning">Для изменения требуется роль менеджера проекта.</p>}
    <div className="evidence-list"><h3>Основания</h3>{item.evidencePins.length
      ? <ul>{item.evidencePins.map((pin, index) => <li key={index}>{evidencePinLabel(pin)}</li>)}</ul>
      : <p role="alert">Нет закреплённого доказательства.</p>}</div>
    <div className="management-actions">
      <button type="button" disabled={!canManage || mutationState === "saving" || !item.evidencePins.length} onClick={() => onTransition(item, confirmStatus)}>Подтвердить</button>
      <button type="button" disabled={!canManage || mutationState === "saving" || needsReview || !item.evidencePins.length} onClick={() => onTransition(item, closeStatus)}>{item.entityType === "risk" ? "Закрыть риск" : "Зафиксировать исполнение"}</button>
      <button type="button" disabled={!canManage || mutationState === "saving"} onClick={() => onTransition(item, "dismissed")}>Отклонить</button>
    </div>
    {mutationMessage && <p role={mutationState === "conflict" || mutationState === "error" ? "alert" : "status"} className={`mutation-${mutationState}`}>{mutationMessage}</p>}
    <section className="management-history"><div><h3>История</h3><button type="button" onClick={() => onLoadHistory(item)}>Обновить историю</button></div>
      {historyState === "loading" && <p role="status">Загружаем историю…</p>}
      {historyState === "empty" && <p>История пока пуста.</p>}
      {historyState === "error" && <p role="alert">История недоступна.</p>}
      {history.length > 0 && <ol>{history.map((event) => <li key={event.sequence}><strong>{event.event}</strong><span>{event.fromStatus || "создано"} → {event.toStatus} · v{event.recordVersion}</span></li>)}</ol>}
    </section>
  </section>;
}
