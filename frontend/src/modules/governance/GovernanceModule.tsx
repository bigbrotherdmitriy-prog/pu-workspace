export type RiskRow = {
  id: number;
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

  return <section className="governance-grid">
    <div className="card">
      <div className="card-head">
        <div><h2>Риски</h2><p>Обнаружено: {risks.length}</p></div>
      </div>
      <div className="governance-list">
        {risks.map((risk) => <article key={risk.id}>
          <div>
            <strong>{risk.title}</strong>
            <p>{risk.source_name} · уверенность {Math.round(risk.confidence * 100)}%</p>
            <span className={`governance-status ${risk.criticality}`}>{risk.status}</span>
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
    <div className="card">
      <div className="card-head">
        <div><h2>Решения</h2><p>Ожидают фиксации: {pendingDecisions}</p></div>
      </div>
      <div className="governance-list">
        {decisions.map((decision) => <article key={decision.id}>
          <div>
            <strong>{decision.question}</strong>
            <p>{decision.source_name} · уверенность {Math.round(decision.confidence * 100)}%</p>
            <span className="governance-status">{decision.status}</span>
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
    </div>
  </section>;
}
