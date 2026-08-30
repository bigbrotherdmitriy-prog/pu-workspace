export type AuditRow = { id: number; action: string; entity_type: string; entity_id?: number; details?: string; created_at: string };

type Props = { collapsed: boolean; logs: AuditRow[]; query: string; onReload: () => void };

export function AuditModule({ collapsed, logs, query, onReload }: Props) {
  const normalized = query.toLocaleLowerCase("ru-RU");
  const visible = logs.filter((item) => !query || `${item.action} ${item.entity_type} ${item.details || ""}`.toLocaleLowerCase("ru-RU").includes(normalized));
  return <section className={`module-overlay ${collapsed ? "collapsed" : ""}`}><div className="module-page"><section className="card">
    <div className="card-head"><div><h2>Журнал действий</h2><p>Последние {logs.length} зафиксированных операций</p></div><button onClick={onReload}>Обновить</button></div>
    <div className="audit-list">{visible.map((item) => <article key={item.id}><div className="audit-dot" /><div><strong>{item.action}</strong><p>{item.entity_type}{item.entity_id ? ` №${item.entity_id}` : ""}</p>{item.details && <small>{item.details}</small>}</div><time>{new Date(item.created_at).toLocaleString("ru-RU")}</time></article>)}
      {!visible.length && <div className="empty"><p>{logs.length ? "По запросу ничего не найдено" : "Журнал пока пуст"}</p></div>}
    </div>
  </section></div></section>;
}
