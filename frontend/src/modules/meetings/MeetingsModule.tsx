import { useEffect, useState } from "react";
import { CheckCircle2, FileCheck2, RefreshCw, ShieldAlert, Users } from "lucide-react";

export type MeetingSourceCandidate = {
  document_id?: number;
  display_name: string;
  media_type?: string;
  source_id: string;
  source_version_id: string;
  evidence_id: string;
  materialization_id: string;
  observed_at?: string;
};

export type MeetingProposal = {
  id: number;
  record_version: number;
  proposal_type: "task" | "risk" | "decision";
  payload: Record<string, unknown>;
  status: "proposed" | "confirmed";
  target_entity_type?: string;
  target_entity_id?: number;
};

export type MeetingSourceBinding = Omit<MeetingSourceCandidate, "document_id">;

export type MeetingAuthorityState = {
  loaded: boolean;
  loading: boolean;
  error?: string;
  candidates: MeetingSourceCandidate[];
  proposals: MeetingProposal[];
  source_binding?: MeetingSourceBinding;
};

export type MeetingRow = {
  id: number;
  record_version?: number;
  contract_id?: number;
  title: string;
  scheduled_at?: string;
  duration_minutes?: number;
  participants?: string;
  participant_user_ids: number[];
  participant_contact_ids: number[];
  participant_refs: Array<{ kind: "user" | "contact"; id: number; name: string; email: string }>;
  resource_ids: number[];
  resource_refs: BookableResourceRow[];
  has_conflicts: boolean;
  conflict_count: number;
  conflicts: Array<{
    meeting_id?: number; project_id?: number; title: string; overlap_from: string; overlap_to: string;
    participants: Array<{ kind: "user" | "contact"; id: number; name: string; email: string }>;
    resources: BookableResourceRow[];
    redacted: boolean;
  }>;
  has_resource_warnings: boolean;
  resource_warnings: Array<{
    code: "capacity_exceeded"; resource_id: number; resource_name: string;
    capacity: number; participant_count: number;
  }>;
  agenda?: string;
  minutes?: string;
  status: string;
  can_edit: boolean;
  can_manage: boolean;
};

export type BookableResourceRow = {
  id: number;
  record_version: number;
  managing_project_id: number;
  kind: "room" | "equipment" | "other";
  name: string;
  timezone: string;
  capacity?: number;
  active: boolean;
};

type Props = {
  projectId: number;
  collapsed: boolean;
  meetings: MeetingRow[];
  title: string;
  date: string;
  duration: string;
  agenda: string;
  participantUserIds: number[];
  participantContactIds: number[];
  members: Array<{ user_id: number; name: string; email: string }>;
  contacts: Array<{ id: number; name: string; email: string; active: boolean }>;
  resources: BookableResourceRow[];
  resourceIds: number[];
  canManageResources: boolean;
  resourceName: string;
  resourceKind: BookableResourceRow["kind"];
  resourceTimezone: string;
  resourceCapacity: string;
  onTitleChange: (value: string) => void;
  onDateChange: (value: string) => void;
  onDurationChange: (value: string) => void;
  onAgendaChange: (value: string) => void;
  onParticipantUserIdsChange: (value: number[]) => void;
  onParticipantContactIdsChange: (value: number[]) => void;
  onResourceIdsChange: (value: number[]) => void;
  onResourceNameChange: (value: string) => void;
  onResourceKindChange: (value: BookableResourceRow["kind"]) => void;
  onResourceTimezoneChange: (value: string) => void;
  onResourceCapacityChange: (value: string) => void;
  onCreateResource: () => void;
  onDeactivateResource: (resource: BookableResourceRow) => void;
  onCreate: () => void;
  onRecordMinutes: (meeting: MeetingRow) => void;
  authority: Record<number, MeetingAuthorityState>;
  busyAuthorityId: number;
  onRefreshAuthority: (meeting: MeetingRow) => void;
  onBindSource: (meeting: MeetingRow, source: MeetingSourceCandidate) => void;
  onConfirmProposal: (meeting: MeetingRow, proposal: MeetingProposal) => void;
};

const proposalTitle = (proposal: MeetingProposal) => {
  const value = proposal.proposal_type === "decision"
    ? proposal.payload.question
    : proposal.payload.title;
  return typeof value === "string" && value.trim() ? value : "Предложение без названия";
};

const proposalType = (kind: MeetingProposal["proposal_type"]) => ({
  task: "Поручение",
  risk: "Риск",
  decision: "Решение",
}[kind]);

const evidenceQuotes = (proposal: MeetingProposal) => [
  proposal.payload.excerpt,
  proposal.payload.due_date_evidence_quote,
  proposal.payload.assignee_evidence_quote,
  proposal.payload.amount_evidence_quote,
].filter((value, index, values): value is string => (
  typeof value === "string" && value.trim().length > 0 && values.indexOf(value) === index
));

function MeetingAuthorityPanel({
  meeting,
  state,
  busy,
  onRefresh,
  onBind,
  onConfirm,
}: {
  meeting: MeetingRow;
  state?: MeetingAuthorityState;
  busy: boolean;
  onRefresh: () => void;
  onBind: (source: MeetingSourceCandidate) => void;
  onConfirm: (proposal: MeetingProposal) => void;
}) {
  const [selectedSource, setSelectedSource] = useState("");
  const candidates = state?.candidates || [];
  useEffect(() => {
    if (!candidates.some((candidate) => candidate.materialization_id === selectedSource)) {
      setSelectedSource(candidates[0]?.materialization_id || "");
    }
  }, [candidates, selectedSource]);
  const selected = candidates.find((candidate) => candidate.materialization_id === selectedSource);

  return <section className="meeting-authority" aria-label={`Безопасные действия: ${meeting.title}`}>
    <div className="meeting-authority-head">
      <div>
        <strong>Действия из протокола</strong>
        <small>Ничего не создаётся без точного источника и отдельного подтверждения manager.</small>
      </div>
      <button className="secondary" disabled={busy || state?.loading} onClick={onRefresh}>
        <RefreshCw size={15} /> Обновить
      </button>
    </div>
    {state?.loading && <p role="status">Проверяем источник и предложения…</p>}
    {state?.error && <div className="meeting-authority-error" role="alert">
      <ShieldAlert size={18} /><span>{state.error}</span>
    </div>}
    {state?.source_binding && <div className="meeting-source-proof">
      <FileCheck2 size={18} />
      <span><strong>{state.source_binding.display_name}</strong><small>Точная версия источника подтверждена</small></span>
    </div>}
    {state?.loaded && !state.proposals.length && meeting.can_manage && <div className="meeting-source-picker">
      {candidates.length ? <>
        <label>
          Текущий локальный документ-источник
          <select aria-label={`Источник протокола ${meeting.title}`} value={selectedSource}
            onChange={(event) => setSelectedSource(event.target.value)}>
            {candidates.map((candidate) => <option key={candidate.materialization_id} value={candidate.materialization_id}>
              {candidate.display_name}{candidate.document_id ? ` · документ #${candidate.document_id}` : ""}
            </option>)}
          </select>
        </label>
        <button disabled={busy || !selected} onClick={() => selected && onBind(selected)}>
          Привязать точную версию и подготовить предложения
        </button>
      </> : <p>Нет действующего local-upload источника. Загрузите протокол как файл и дождитесь обработки.</p>}
    </div>}
    {state?.loaded && !state.proposals.length && !meeting.can_manage && <p>
      Предложения ещё не подготовлены. Привязать источник и подтвердить действия может manager проекта.
    </p>}
    {!!state?.proposals.length && <div className="meeting-proposal-list">
      {state.proposals.map((proposal) => {
        const quotes = evidenceQuotes(proposal);
        return <article key={proposal.id} className={`meeting-proposal ${proposal.status}`}>
          <div className="meeting-proposal-heading">
            <span>{proposalType(proposal.proposal_type)}</span>
            <strong>{proposalTitle(proposal)}</strong>
          </div>
          {!!quotes.length && <blockquote>{quotes.join(" · ")}</blockquote>}
          {proposal.status === "confirmed" ? <p className="meeting-proposal-confirmed">
            <CheckCircle2 size={17} /> Применено: {proposal.target_entity_type} #{proposal.target_entity_id}
          </p> : meeting.can_manage ? <button disabled={busy} onClick={() => onConfirm(proposal)}>
            Подтвердить только это действие
          </button> : <small>Ожидает подтверждения manager.</small>}
        </article>;
      })}
    </div>}
  </section>;
}

export function MeetingsModule({
  projectId,
  collapsed,
  meetings,
  title,
  date,
  duration,
  agenda,
  participantUserIds,
  participantContactIds,
  members,
  contacts,
  resources,
  resourceIds,
  canManageResources,
  resourceName,
  resourceKind,
  resourceTimezone,
  resourceCapacity,
  onTitleChange,
  onDateChange,
  onDurationChange,
  onAgendaChange,
  onParticipantUserIdsChange,
  onParticipantContactIdsChange,
  onResourceIdsChange,
  onResourceNameChange,
  onResourceKindChange,
  onResourceTimezoneChange,
  onResourceCapacityChange,
  onCreateResource,
  onDeactivateResource,
  onCreate,
  onRecordMinutes,
  authority,
  busyAuthorityId,
  onRefreshAuthority,
  onBindSource,
  onConfirmProposal,
}: Props) {
  return (
    <section className={`module-overlay ${collapsed ? "collapsed" : ""}`}>
      <div className="module-page">
        <section className="card meeting-create">
          <div>
            <h2>Новое совещание</h2>
            <p>
              После встречи внесите протокол — система выделит поручения,
              риски и решения.
            </p>
          </div>
          <div>
            <input
              value={title}
              onChange={(event) => onTitleChange(event.target.value)}
              placeholder="Название совещания"
            />
            <input
              type="datetime-local"
              value={date}
              onChange={(event) => onDateChange(event.target.value)}
            />
            <label>
              Длительность, минут
              <input
                type="number"
                min="1"
                max="10080"
                value={duration}
                onChange={(event) => onDurationChange(event.target.value)}
                disabled={!date}
              />
            </label>
            <label>
              Участники проекта
              <select
                multiple
                aria-label="Участники проекта"
                value={participantUserIds.map(String)}
                onChange={(event) => onParticipantUserIdsChange(
                  Array.from(event.currentTarget.selectedOptions, (option) => Number(option.value)),
                )}
              >
                {members.map((member) => (
                  <option value={member.user_id} key={member.user_id}>{member.name} · {member.email}</option>
                ))}
              </select>
            </label>
            <label>
              Внешние контакты
              <select
                multiple
                aria-label="Внешние контакты"
                value={participantContactIds.map(String)}
                onChange={(event) => onParticipantContactIdsChange(
                  Array.from(event.currentTarget.selectedOptions, (option) => Number(option.value)),
                )}
              >
                {contacts.filter((contact) => contact.active).map((contact) => (
                  <option value={contact.id} key={contact.id}>{contact.name} · {contact.email}</option>
                ))}
              </select>
            </label>
            <label>
              Помещения и ресурсы
              <select
                multiple
                aria-label="Помещения и ресурсы"
                value={resourceIds.map(String)}
                onChange={(event) => onResourceIdsChange(
                  Array.from(event.currentTarget.selectedOptions, (option) => Number(option.value)),
                )}
              >
                {resources.filter((resource) => resource.active).map((resource) => (
                  <option value={resource.id} key={resource.id}>
                    {resource.name} · {resource.kind}{resource.capacity ? ` · до ${resource.capacity}` : ""}
                  </option>
                ))}
              </select>
            </label>
            <textarea
              value={agenda}
              onChange={(event) => onAgendaChange(event.target.value)}
              placeholder="Повестка"
            />
            <button disabled={!title.trim()} onClick={onCreate}>
              Запланировать
            </button>
          </div>
        </section>
        {canManageResources && (
          <section className="card meeting-resource-catalog">
            <div>
              <h2>Каталог помещений и ресурсов</h2>
              <p>Ресурс доступен всем проектам организации. Отключение сохраняет историю бронирований.</p>
            </div>
            <div className="meeting-resource-form">
              <input value={resourceName} onChange={(event) => onResourceNameChange(event.target.value)} placeholder="Название ресурса" />
              <select aria-label="Тип ресурса" value={resourceKind} onChange={(event) => onResourceKindChange(event.target.value as BookableResourceRow["kind"])}>
                <option value="room">Помещение</option>
                <option value="equipment">Техника</option>
                <option value="other">Другое</option>
              </select>
              <input aria-label="Часовой пояс ресурса" value={resourceTimezone} onChange={(event) => onResourceTimezoneChange(event.target.value)} />
              <input aria-label="Вместимость ресурса" type="number" min="1" max="100000" value={resourceCapacity} onChange={(event) => onResourceCapacityChange(event.target.value)} placeholder="Вместимость" />
              <button disabled={resourceName.trim().length < 2} onClick={onCreateResource}>Добавить ресурс</button>
            </div>
            {!!resources.length && <div className="meeting-resource-list">
              {resources.map((resource) => <div key={resource.id}>
                <span>{resource.name} · {resource.kind} · {resource.timezone}{resource.capacity ? ` · до ${resource.capacity}` : ""}{resource.active ? "" : " · отключён"}</span>
                {resource.active && resource.managing_project_id === projectId && <button className="secondary" onClick={() => onDeactivateResource(resource)}>Отключить</button>}
              </div>)}
            </div>}
          </section>
        )}
        <section className="meeting-grid">
          {meetings.map((item) => (
            <article className="card meeting-card" key={item.id}>
              <span className={`management-status ${item.status}`}>
                {item.status}
              </span>
              <h2>{item.title}</h2>
              <p>
                {item.scheduled_at
                  ? `${new Date(item.scheduled_at).toLocaleString("ru-RU")} · ${item.duration_minutes || "?"} мин.`
                  : "Дата не назначена"}
              </p>
              {!!item.participant_refs?.length && (
                <p>{item.participant_refs.map((participant) => participant.name).join(", ")}</p>
              )}
              {!!item.resource_refs?.length && (
                <p><strong>Ресурсы:</strong> {item.resource_refs.map((resource) => resource.name).join(", ")}</p>
              )}
              {item.has_conflicts && (
                <div className="meeting-conflict-warning" role="alert">
                  <strong>Конфликт времени: {item.conflict_count}</strong>
                  {item.conflicts.map((conflict, index) => (
                    <p key={`${conflict.meeting_id || "redacted"}-${index}`}>
                      {conflict.title}
                      {!!conflict.participants.length && ` · участники: ${conflict.participants.map((participant) => participant.name).join(", ")}`}
                      {!!conflict.resources?.length && ` · ресурсы: ${conflict.resources.map((resource) => resource.name).join(", ")}`}
                    </p>
                  ))}
                  <small>Встреча создана. Проверьте время и занятость.</small>
                </div>
              )}
              {item.has_resource_warnings && (
                <div className="meeting-conflict-warning" role="alert">
                  <strong>Проверьте вместимость</strong>
                  {item.resource_warnings.map((warning) => (
                    <p key={warning.resource_id}>{warning.resource_name}: участников {warning.participant_count}, вместимость {warning.capacity}</p>
                  ))}
                  <small>Встреча создана, предупреждение не блокирует бронирование.</small>
                </div>
              )}
              {item.agenda && (
                <div className="meeting-agenda">
                  <strong>Повестка</strong>
                  <p>{item.agenda}</p>
                </div>
              )}
              {item.minutes && (
                <div className="meeting-agenda">
                  <strong>Протокол</strong>
                  <p>{item.minutes}</p>
                </div>
              )}
              {!["completed", "cancelled"].includes(item.status) && (
                <button disabled={!item.can_edit} onClick={() => onRecordMinutes(item)}>
                  Внести протокол и проанализировать
                </button>
              )}
              {item.status === "completed" && <MeetingAuthorityPanel
                meeting={item}
                state={authority[item.id]}
                busy={busyAuthorityId === item.id}
                onRefresh={() => onRefreshAuthority(item)}
                onBind={(source) => onBindSource(item, source)}
                onConfirm={(proposal) => onConfirmProposal(item, proposal)}
              />}
            </article>
          ))}
          {!meetings.length && (
            <div className="card empty">
              <Users />
              <p>Совещаний пока нет.</p>
            </div>
          )}
        </section>
      </div>
    </section>
  );
}
