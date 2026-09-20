import { Bell } from "lucide-react";

export type NotificationItem = {
  id: number;
  record_version: number;
  kind: string;
  title: string;
  body: string;
  entity_type: string;
  entity_id: number;
  is_read: boolean;
  created_at: string;
};

export type NotificationPolicy = {
  record_version: number;
  timezone: string;
  deadline_local_time: string;
  quiet_start: string;
  quiet_end: string;
  escalation_delays: number[];
  channels: string[];
  enabled: boolean;
  digest_enabled: boolean;
  digest_cadence: "daily" | "weekdays";
  digest_local_time: string;
};

type Props = {
  collapsed: boolean;
  notifications: NotificationItem[];
  onRefresh: () => void;
  onMarkRead: (notification: NotificationItem) => void;
  policy: NotificationPolicy | null;
  canManagePolicy: boolean;
  onPolicyChange: (policy: NotificationPolicy) => void;
  onSavePolicy: () => void;
};

export function NotificationsModule({
  collapsed, notifications, onRefresh, onMarkRead, policy, canManagePolicy,
  onPolicyChange, onSavePolicy,
}: Props) {
  return (
    <section className={`module-overlay ${collapsed ? "collapsed" : ""}`}>
      <div className="module-page">
        <section className="card management-intro">
          <div>
            <h2>Центр уведомлений</h2>
            <p>Просроченные и ближайшие сроки, открытые риски и решения.</p>
          </div>
          <button onClick={onRefresh}>Обновить контроль</button>
        </section>
        {policy && (
          <section className="card digest-settings">
            <div>
              <h3>Ежедневная управленческая сводка</h3>
              <p>Содержит только подтверждённые активные элементы и ссылки на основания. Внешние действия не создаются.</p>
            </div>
            <label>
              <input
                type="checkbox"
                checked={policy.digest_enabled}
                disabled={!canManagePolicy}
                onChange={(event) => onPolicyChange({ ...policy, digest_enabled: event.target.checked })}
              />
              Включить сводку
            </label>
            <label>
              Периодичность
              <select
                value={policy.digest_cadence}
                disabled={!canManagePolicy}
                onChange={(event) => onPolicyChange({
                  ...policy, digest_cadence: event.target.value as NotificationPolicy["digest_cadence"],
                })}
              >
                <option value="daily">Каждый день</option>
                <option value="weekdays">По рабочим дням</option>
              </select>
            </label>
            <label>
              Время сводки
              <input
                type="time"
                value={policy.digest_local_time.slice(0, 5)}
                disabled={!canManagePolicy}
                onChange={(event) => onPolicyChange({ ...policy, digest_local_time: `${event.target.value}:00` })}
              />
            </label>
            <label>
              Часовой пояс
              <input
                value={policy.timezone}
                disabled={!canManagePolicy}
                onChange={(event) => onPolicyChange({ ...policy, timezone: event.target.value })}
              />
            </label>
            <div className="digest-quiet-hours">
              <label>Тихие часы с <input type="time" value={policy.quiet_start.slice(0, 5)} disabled={!canManagePolicy}
                onChange={(event) => onPolicyChange({ ...policy, quiet_start: `${event.target.value}:00` })} /></label>
              <label>до <input type="time" value={policy.quiet_end.slice(0, 5)} disabled={!canManagePolicy}
                onChange={(event) => onPolicyChange({ ...policy, quiet_end: `${event.target.value}:00` })} /></label>
            </div>
            <small>Каналы политики: {policy.channels.join(", ")}. Сводка сейчас создаётся только внутри приложения.</small>
            {canManagePolicy && <button onClick={onSavePolicy}>Сохранить настройки сводки</button>}
          </section>
        )}
        <section className="card notification-list">
          {notifications.map((item) => (
            <article className={item.is_read ? "read" : ""} key={item.id}>
              <div className={`notification-kind ${item.kind}`}><Bell /></div>
              <div>
                <strong>{item.title}</strong>
                <p>{item.body}</p>
                <small>{new Date(item.created_at).toLocaleString("ru-RU")}</small>
              </div>
              {!item.is_read && <button onClick={() => onMarkRead(item)}>Прочитано</button>}
            </article>
          ))}
          {!notifications.length && (
            <div className="empty">
              <Bell />
              <p>Нажмите «Обновить контроль», чтобы собрать актуальные уведомления.</p>
            </div>
          )}
        </section>
      </div>
    </section>
  );
}
