import { Users } from "lucide-react";

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
  has_conflicts: boolean;
  conflict_count: number;
  conflicts: Array<{
    meeting_id?: number; project_id?: number; title: string; overlap_from: string; overlap_to: string;
    participants: Array<{ kind: "user" | "contact"; id: number; name: string; email: string }>;
    redacted: boolean;
  }>;
  agenda?: string;
  minutes?: string;
  status: string;
};

type Props = {
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
  onTitleChange: (value: string) => void;
  onDateChange: (value: string) => void;
  onDurationChange: (value: string) => void;
  onAgendaChange: (value: string) => void;
  onParticipantUserIdsChange: (value: number[]) => void;
  onParticipantContactIdsChange: (value: number[]) => void;
  onCreate: () => void;
  onRecordMinutes: (meeting: MeetingRow) => void;
};

export function MeetingsModule({
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
  onTitleChange,
  onDateChange,
  onDurationChange,
  onAgendaChange,
  onParticipantUserIdsChange,
  onParticipantContactIdsChange,
  onCreate,
  onRecordMinutes,
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
              {item.has_conflicts && (
                <div className="meeting-conflict-warning" role="alert">
                  <strong>Конфликт времени: {item.conflict_count}</strong>
                  {item.conflicts.map((conflict, index) => (
                    <p key={`${conflict.meeting_id || "redacted"}-${index}`}>
                      {conflict.title} · {conflict.participants.map((participant) => participant.name).join(", ")}
                    </p>
                  ))}
                  <small>Встреча создана. Проверьте время с участниками.</small>
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
                <button onClick={() => onRecordMinutes(item)}>
                  Внести протокол и проанализировать
                </button>
              )}
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
