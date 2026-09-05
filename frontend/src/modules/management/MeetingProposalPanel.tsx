import type { MeetingProposal } from "./managementReadModel";

type Props = {
  state: "unavailable" | "loading" | "ready" | "empty" | "error";
  proposals: MeetingProposal[];
  error?: string | null;
  busyId?: number | null;
  onConfirm: (proposal: MeetingProposal, createInternalTask: boolean) => void;
};

const label = { obligation: "Обязательство", task: "Внутренняя задача", decision: "Решение" } as const;

export function MeetingProposalPanel({ state, proposals, error, busyId, onConfirm }: Props) {
  return <section className="management-card" aria-labelledby="meeting-proposals-title">
    <header><div><span className="management-eyebrow">ПРОТОКОЛ ВСТРЕЧИ</span><h2 id="meeting-proposals-title">Предложения действий</h2></div></header>
    {state === "unavailable" && <p className="management-warning" role="status">Просмотр предложений ещё не подключён к HTTP API. Никакие действия не выполняются.</p>}
    {state === "loading" && <p role="status">Загружаем предложения…</p>}
    {state === "error" && <p role="alert">{error || "Предложения недоступны."}</p>}
    {state === "empty" && <div className="management-empty"><strong>Предложений нет</strong><p>Завершите протокол и дождитесь подтверждённого анализа.</p></div>}
    {state === "ready" && <ul className="proposal-list">{proposals.map((proposal) => {
      const waiting = proposal.status === "needs_confirmation" || proposal.reviewState === "needs_review";
      return <li key={`${proposal.entityType}:${proposal.entityId}`}>
        <div><span className="management-kind">{label[proposal.kind]}</span><strong>№ {proposal.entityId}</strong>
          <span>Версия {proposal.recordVersion} · {waiting ? "ожидает человека" : proposal.status}</span></div>
        <button type="button" disabled={busyId === proposal.entityId || !waiting} onClick={() => onConfirm(proposal, proposal.kind === "task")}>Подтвердить</button>
      </li>;
    })}</ul>}
  </section>;
}
