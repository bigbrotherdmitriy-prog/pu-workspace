import { Users } from "lucide-react";

export type MeetingRow = {
  id: number;
  record_version?: number;
  contract_id?: number;
  title: string;
  scheduled_at?: string;
  participants?: string;
  agenda?: string;
  minutes?: string;
  status: string;
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
