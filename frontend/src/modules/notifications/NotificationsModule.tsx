import { Bell } from "lucide-react";

export type NotificationItem = {
  id: number;
  kind: string;
  title: string;
  body: string;
  entity_type: string;
  entity_id: number;
  is_read: boolean;
  created_at: string;
};

type Props = {
  collapsed: boolean;
  notifications: NotificationItem[];
  onRefresh: () => void;
  onMarkRead: (notification: NotificationItem) => void;
};

export function NotificationsModule({ collapsed, notifications, onRefresh, onMarkRead }: Props) {
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
