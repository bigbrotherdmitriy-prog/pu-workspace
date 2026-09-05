import { GitPullRequest, RotateCcw } from "lucide-react";
import { FolderAnalysisSummary } from "../folder-analysis/FolderAnalysisSummary";
import { StorageMutationPanel } from "./StorageMutationPanel";

export type ProposalAction = { id: number; source: string; proposed_name: string; target_folder: string; edited_name?: string; edited_folder?: string; user_decision: string; confidence: number; special_case?: string; reasoning: string };
export type Proposal = { id: number; folder_name: string; status: string; copy_folder_id: string; originals_modified: boolean; note?: string; actions: ProposalAction[] };
type Props = { projectId: number | null; collapsed: boolean; proposals: Proposal[]; busyProposal: number; targetFolders: string[]; onOpenDocuments: () => void; onApproveSafe: (proposal: Proposal) => void; onApply: (proposal: Proposal) => void; onStandardize: (proposal: Proposal) => void; onRollback: (proposal: Proposal) => void; onDecision: (action: ProposalAction, decision: string) => void; onSave: (proposalId: number, actionId: number) => void; onEdit: (proposalId: number, actionId: number, patch: Partial<ProposalAction>) => void; onApplySource: (proposal: Proposal, action: ProposalAction) => void; onApplySourceBulk: (proposal: Proposal) => void };

export function ProposalsModule(props: Props) {
  return <section className={`module-overlay ${props.collapsed ? "collapsed" : ""}`}><div className="module-page">
    <FolderAnalysisSummary proposals={props.proposals} onOpenDocuments={props.onOpenDocuments} />
    <section className="card proposal-intro"><div><h2>Стандартизация рабочей папки</h2><p>Проверьте «Было → Станет», одобрите нужные строки и примените их одним пакетом. Изменения разрешены только после готовой безопасной копии и записываются для отката.</p></div><span>{props.proposals.length} пакетов</span></section>
    <div className="proposal-list">
      {props.proposals.map((proposal) => <article className="card proposal-card" key={proposal.id}>
        <div className="proposal-head"><div><span className={`proposal-status ${proposal.status}`}>{proposal.status}</span><h2>{proposal.folder_name}</h2><p>Пакет №{proposal.id} · действий {proposal.actions.length} · <strong>{proposal.copy_folder_id.startsWith("virtual:") ? "ОРИГИНАЛ — только одно подтверждённое изменение" : "БЕЗОПАСНАЯ КОПИЯ"}</strong></p></div><div className="proposal-controls">
          {proposal.status === "waiting_confirmation" && !proposal.copy_folder_id.startsWith("virtual:") && <button disabled={props.busyProposal === proposal.id} onClick={() => props.onApproveSafe(proposal)}>Подтвердить только безопасные</button>}
          {["approved", "ready_to_apply_to_copy"].includes(proposal.status) && !proposal.copy_folder_id.startsWith("virtual:") && <button className="apply-safe" disabled={props.busyProposal === proposal.id} onClick={() => props.onApply(proposal)}>Dry-run и применить к копии</button>}
          {["applied", "rollback_partial"].includes(proposal.status) && <button className="apply-safe" disabled={props.busyProposal === proposal.id} onClick={() => props.onStandardize(proposal)}>Стандартизировать все файлы в копии</button>}
          {["applied", "rollback_partial"].includes(proposal.status) && <button className="rollback" disabled={props.busyProposal === proposal.id} onClick={() => props.onRollback(proposal)}><RotateCcw />Откатить</button>}
          {proposal.copy_folder_id.startsWith("virtual:") && ["waiting_confirmation", "approved"].includes(proposal.status) && proposal.actions.some((action) => ["approved", "edited"].includes(action.user_decision)) && <button className="apply-safe" disabled={props.busyProposal === proposal.id} onClick={() => props.onApplySourceBulk(proposal)}>Применить одобренные к рабочей папке</button>}
        </div></div>
        {proposal.note && <div className="proposal-note">{proposal.note}</div>}
        <div className="proposal-actions-table"><div className="proposal-table-head"><span>Решение</span><span>Было</span><span>Станет</span><span>Уверенность и основание</span></div>
          {proposal.actions.slice(0, 500).map((action) => <div className="proposal-action" key={action.id}>
            <select value={action.user_decision} onChange={(event) => props.onDecision(action, event.target.value)} disabled={proposal.status !== "waiting_confirmation"}><option value="pending">Проверить</option><option value="approved">Одобрить</option><option value="edited">Изменить</option><option value="skipped">Пропустить</option></select>
            <strong>{action.source}</strong><div><input value={action.edited_name || action.proposed_name} disabled={proposal.status !== "waiting_confirmation"} onBlur={() => action.user_decision === "edited" && props.onSave(proposal.id, action.id)} onChange={(event) => props.onEdit(proposal.id, action.id, { edited_name: event.target.value, user_decision: "edited" })} /><select value={action.edited_folder || action.target_folder} disabled={proposal.status !== "waiting_confirmation"} onChange={(event) => props.onEdit(proposal.id, action.id, { edited_folder: event.target.value, user_decision: "edited" })}>{props.targetFolders.map((folder) => <option key={folder}>{folder}</option>)}</select></div>
            <div><span className={action.special_case ? "needs-review" : ""}>{Math.round(action.confidence * 100)}%{action.special_case ? ` · ${action.special_case}` : ""}</span><p>{action.reasoning}</p>{proposal.copy_folder_id.startsWith("virtual:") && action.user_decision === "edited" && <button className="apply-source-inline" disabled={props.busyProposal === proposal.id} onClick={() => props.onApplySource(proposal, action)}>Проверить и изменить этот оригинал</button>}{props.projectId && proposal.copy_folder_id.startsWith("virtual:") && ["approved", "edited"].includes(action.user_decision) && <StorageMutationPanel projectId={props.projectId} proposalId={proposal.id} actionId={action.id} />}</div>
          </div>)}
        </div>
      </article>)}
      {!props.proposals.length && <div className="card empty"><GitPullRequest /><p>Предложений пока нет. Запустите анализ подготовленного снимка папки.</p></div>}
    </div>
  </div></section>;
}
