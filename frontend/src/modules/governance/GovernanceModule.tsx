export type RiskRow = {
  id: number;
  record_version?: number;
  title: string;
  kind: string;
  criticality: string;
  status: string;
  action_note?: string;
  source_name: string;
  confidence: number;
};

export type DecisionRow = {
  id: number;
  record_version?: number;
  question: string;
  status: string;
  decision_text?: string;
  reason?: string;
  source_name: string;
  confidence: number;
};

type Props = {
  risks: RiskRow[];
  decisions: DecisionRow[];
  onUpdateRisk: (risk: RiskRow, status: string) => void;
  onUpdateDecision: (decision: DecisionRow, status: string) => void;
};

export function GovernanceModule({ risks, decisions, onUpdateRisk, onUpdateDecision }: Props) {
  const pendingDecisions = decisions.filter(
    (item) => !["executed", "dismissed"].includes(item.status),
  ).length;

  const openRisks = risks.filter((item) => !["resolved", "dismissed"].includes(item.status)).length;
  const criticalRisks = risks.filter(
    (item) => !["resolved", "dismissed"].includes(item.status)
      && (item.criticality === "critical" || item.criticality === "high"),
  ).length;
  const statusLabel = (status: string) => ({
    needs_confirmation: "Нужно подтвердить",
    confirmed: "Подтверждено",
    resolved: "Закрыто",
    dismissed: "Отклонено",
    decided: "Решение принято",
    executed: "Исполнено",
  }[status] || "На проверке");

  return <section className="governance-workspace">
    <div className="governance-overview">
      <div><span>Открытые риски</span><strong>{openRisks}</strong><small>требуют контроля</small></div>
      <div><span>Высокий приоритет</span><strong>{criticalRisks}</strong><small>проверить в первую очередь</small></div>
      <div><span>Решения</span><strong>{pendingDecisions}</strong><small>ожидают фиксации</small></div>
    </div>
    <div className="governance-grid">
    <div className="card governance-column risks-column">
      <div className="card-head">
        <div><span className="eyebrow">КОНТРОЛЬ</span><h2>Риски проекта</h2><p>{risks.length} обнаружено · {openRisks} открыто</p></div>
      </div>
      <div className="governance-list">
        {risks.map((risk) => <article key={risk.id}>
          <div>
            <strong>{risk.title}</strong>
            <p>{risk.source_name} · уверенность {Math.round(risk.confidence * 100)}%</p>
            <span className={`governance-status ${risk.criticality}`}>{statusLabel(risk.status)}</span>
          </div>
          <div className="task-actions">
            {risk.status === "needs_confirmation" && (
              <button onClick={() => onUpdateRisk(risk, "confirmed")}>Подтвердить</button>
            )}
            {!['resolved', 'dismissed'].includes(risk.status) && (
              <button className="complete" onClick={() => onUpdateRisk(risk, "resolved")}>Закрыть</button>
            )}
          </div>
        </article>)}
      </div>
    </div>
    <div className="card governance-column decisions-column">
      <div className="card-head">
        <div><span className="eyebrow">РЕШЕНИЯ</span><h2>Ждут вашего выбора</h2><p>{pendingDecisions} необходимо зафиксировать</p></div>
      </div>
      <div className="governance-list">
        {decisions.map((decision) => <article key={decision.id}>
          <div>
            <strong>{decision.question}</strong>
            <p>{decision.source_name} · уверенность {Math.round(decision.confidence * 100)}%</p>
            <span className="governance-status">{statusLabel(decision.status)}</span>
          </div>
          <div className="task-actions">
            {!['decided', 'executed', 'dismissed'].includes(decision.status) && (
              <button className="complete" onClick={() => onUpdateDecision(decision, "decided")}>Принять</button>
            )}
            {!['executed', 'dismissed'].includes(decision.status) && (
              <button onClick={() => onUpdateDecision(decision, "dismissed")}>Отклонить</button>
            )}
          </div>
        </article>)}
      </div>
    </div></div>
  </section>;
}
