import { useEffect, useState } from "react";
import type { DeadlinePolicy, DigestEnqueueResult, DigestNotification, DigestPreference, DigestState } from "./managementReadModel";

type Props = {
  deadlinePolicy: DeadlinePolicy | null;
  digestState: DigestState | null;
  digestJob?: DigestEnqueueResult | null;
  notifications: DigestNotification[];
  configurationAvailable: boolean;
  preference?: DigestPreference | null;
  mutationState?: "idle" | "saving" | "saved" | "conflict" | "error";
  mutationMessage?: string | null;
  onSave?: (preference: Omit<DigestPreference, "projectId" | "userId" | "persisted" | "externalActionsEnabled">) => void;
  onEnqueue?: (preference: { timezone: string; quietStart: string; quietEnd: string;
    channel: "in_app" | "disabled"; cadence: "daily" | "weekdays"; localDate: string }) => void;
};

const digestLabels: Record<DigestState["status"], string> = {
  created: "Сводка создана",
  already_created: "Сводка за этот день уже создана",
  empty: "Для сводки нет элементов",
  disabled: "Ежедневная сводка отключена",
  deferred_quiet_hours: "Сводка отложена до окончания тихих часов",
  stale: "Запрос сводки устарел",
};

function localDate(timezone: string) {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit" })
    .formatToParts(new Date());
  const value = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${value.year}-${value.month}-${value.day}`;
}

export function DeadlineDigestPanel({ deadlinePolicy, digestState, digestJob, notifications, configurationAvailable,
  preference, mutationState = "idle", mutationMessage, onSave, onEnqueue }: Props) {
  const digests = notifications.filter((item) => item.kind === "management_digest");
  const [form, setForm] = useState(() => ({ timezone: preference?.timezone || "Europe/Moscow",
    quietStart: (preference?.quietStart || "20:00").slice(0, 5), quietEnd: (preference?.quietEnd || "08:00").slice(0, 5),
    channel: preference?.channel || "in_app" as const, cadence: preference?.cadence || "daily" as const }));
  useEffect(() => {
    if (preference) setForm({ timezone: preference.timezone, quietStart: preference.quietStart.slice(0, 5),
      quietEnd: preference.quietEnd.slice(0, 5), channel: preference.channel, cadence: preference.cadence });
  }, [preference]);
  return <section className="management-card" aria-labelledby="deadline-digest-title">
    <header><div><span className="management-eyebrow">СРОКИ И СВОДКА</span><h2 id="deadline-digest-title">Контроль уведомлений</h2></div></header>
    {!configurationAvailable && <p className="management-warning" role="status">Настройки quiet-hours и канала пока не опубликованы через HTTP API. Показаны только подтверждённые данные.</p>}
    {preference && <form className="management-digest-settings" onSubmit={(event) => {
      event.preventDefault();
      onSave?.({ ...form, recordVersion: preference.recordVersion });
    }}>
      <label>Часовой пояс<input value={form.timezone} onChange={(event) => setForm({ ...form, timezone: event.target.value })} /></label>
      <label>Тихие часы с<input type="time" value={form.quietStart} onChange={(event) => setForm({ ...form, quietStart: event.target.value })} /></label>
      <label>до<input type="time" value={form.quietEnd} onChange={(event) => setForm({ ...form, quietEnd: event.target.value })} /></label>
      <label>Расписание<select value={form.cadence} onChange={(event) => setForm({ ...form, cadence: event.target.value as DigestPreference["cadence"] })}>
        <option value="daily">Каждый день</option><option value="weekdays">По рабочим дням</option>
      </select></label>
      <label>Канал<select value={form.channel} onChange={(event) => setForm({ ...form, channel: event.target.value as DigestPreference["channel"] })}>
        <option value="in_app">В приложении</option><option value="disabled">Отключено</option>
      </select></label>
      <div className="management-actions"><button type="submit" disabled={mutationState === "saving"}>Сохранить настройки</button>
        <button type="button" disabled={mutationState === "saving" || form.channel === "disabled"} onClick={() => {
          try { onEnqueue?.({ ...form, localDate: localDate(form.timezone) }); } catch { /* Invalid timezone remains fail-closed. */ }
        }}>Сформировать сводку сейчас</button></div>
      <p>Настройки v{preference.recordVersion}{preference.persisted ? " сохранены" : " ещё не сохранены"}. Внешние действия отключены.</p>
      {mutationMessage && <p role={mutationState === "error" || mutationState === "conflict" ? "alert" : "status"}>{mutationMessage}</p>}
    </form>}
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
