import type { DeadlinePolicy, DigestEnqueueResult, DigestNotification, DigestState } from "./managementReadModel";

type Props = {
  deadlinePolicy: DeadlinePolicy | null;
  digestState: DigestState | null;
  digestJob?: DigestEnqueueResult | null;
  notifications: DigestNotification[];
  configurationAvailable: boolean;
};

const digestLabels: Record<DigestState["status"], string> = {
  created: "Сводка создана",
  already_created: "Сводка за этот день уже создана",
  empty: "Для сводки нет элементов",
  disabled: "Ежедневная сводка отключена",
  deferred_quiet_hours: "Сводка отложена до окончания тихих часов",
  stale: "Запрос сводки устарел",
};

export function DeadlineDigestPanel({ deadlinePolicy, digestState, digestJob, notifications, configurationAvailable }: Props) {
  const digests = notifications.filter((item) => item.kind === "management_digest");
  return <section className="management-card" aria-labelledby="deadline-digest-title">
    <header><div><span className="management-eyebrow">СРОКИ И СВОДКА</span><h2 id="deadline-digest-title">Контроль уведомлений</h2></div></header>
    {!configurationAvailable && <p className="management-warning" role="status">Настройки quiet-hours и канала пока не опубликованы через HTTP API. Показаны только подтверждённые данные.</p>}
    <dl className="management-facts">
      <div><dt>Напоминания</dt><dd>{deadlinePolicy ? deadlinePolicy.reminderDays.map((day) => `за ${day} дн.`).join(", ") : "Нет данных"}</dd></div>
      <div><dt>Тихие часы</dt><dd>{deadlinePolicy ? `${deadlinePolicy.quietHours.start}–${deadlinePolicy.quietHours.end}` : "Нет данных"}</dd></div>
      <div><dt>Внешние действия</dt><dd>{digestState?.externalActionsCreated === false ? "Не создавались" : "Нет подтверждения"}</dd></div>
    </dl>
    {digestState && <p className="management-digest-state" role="status"><strong>{digestLabels[digestState.status]}</strong>
      <span>Локальная дата: {digestState.localDate}</span>{digestState.deferredUntil && <span>Отложено до {new Date(digestState.deferredUntil).toLocaleString("ru-RU")}</span>}</p>}
    {digestJob && <p className="management-digest-state" role="status"><strong>Задание сводки № {digestJob.jobId}</strong><span>Состояние очереди: {digestJob.status}</span><span>Внешние действия не создавались</span></p>}
    <div className="management-history"><h3>Последние сводки</h3>{digests.length
      ? <ol>{digests.map((item) => <li key={item.id}><strong>{item.title}</strong><span>{item.body}</span><time dateTime={item.createdAt}>{new Date(item.createdAt).toLocaleString("ru-RU")}</time></li>)}</ol>
      : <p>Сводок пока нет.</p>}</div>
  </section>;
}
