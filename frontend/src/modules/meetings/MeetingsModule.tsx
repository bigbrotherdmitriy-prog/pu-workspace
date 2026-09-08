import { Users } from "lucide-react";
import { MeetingSourcePanel } from "./MeetingSourcePanel";

export type MeetingRow = {
  id: number;
  contract_id?: number;
  title: string;
  scheduled_at?: string;
  participants?: string;
  agenda?: string;
  minutes?: string;
  status: string;
  record_version?: number;
};

type Props = {
  collapsed: boolean;
  meetings: MeetingRow[];
  title: string;
  date: string;
  agenda: string;
  onTitleChange: (value: string) => void;
  onDateChange: (value: string) => void;
  onAgendaChange: (value: string) => void;
  onCreate: () => void;
  onRecordMinutes: (meeting: MeetingRow) => void;
  projectId?: number | null;
  members?: { user_id: number; name: string }[];
  onMeetingVersionChange?: (meeting: MeetingRow, version: number) => void;
};

export function MeetingsModule({
  collapsed,
  meetings,
  title,
  date,
  agenda,
  onTitleChange,
  onDateChange,
  onAgendaChange,
  onCreate,
  onRecordMinutes,
  projectId,
  members = [],
  onMeetingVersionChange,
}: Props) {
  return (
    <section className={`module-overlay ${collapsed ? "collapsed" : ""}`}>
      <div className="module-page">
        <section className="card meeting-create">
          <div>
            <h2>Новое совещание</h2>
            <p>
              Внесите протокол, привяжите подтверждённый источник и проверьте
              предложения перед созданием поручений и решений.
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
                  ? new Date(item.scheduled_at).toLocaleString("ru-RU")
                  : "Дата не назначена"}
              </p>
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
              {item.status !== "cancelled" && (
                <button onClick={() => onRecordMinutes(item)}>
                  Внести протокол и проанализировать
                </button>
              )}
              {projectId && item.status === "completed" && item.minutes && Number.isSafeInteger(item.record_version) && item.record_version! > 0 && (
                <MeetingSourcePanel key={`${projectId}:${item.id}`} projectId={projectId}
                  meetingId={item.id} recordVersion={item.record_version!} members={members}
                  onVersionChange={version => onMeetingVersionChange?.(item, version)} />
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
