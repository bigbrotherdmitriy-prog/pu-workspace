import { Activity, Bot, CalendarDays, FolderTree, Mail } from "lucide-react";

export type IntegrationItem = {
  key: string;
  provider: string;
  capability: "storage" | "channel" | "task" | "calendar" | "ai";
  name: string;
  description: string;
  available: boolean;
  connected: boolean;
  action?: "oauth" | "sync" | "select_source" | "local_upload" | "ai_policy";
  detail?: string;
};

export type SystemState = {
  ready: boolean;
  google_drive_ready: boolean;
  telegram_ready: boolean;
  checks: Record<string, { ok: boolean; required: boolean; message: string }>;
};

type Props = {
  collapsed: boolean;
  items: IntegrationItem[];
  systemState: SystemState | null;
  gmailSyncing: boolean;
  gmailSyncStatus: string;
  onSyncGmail: () => void;
  onSelectFolder: (provider: string) => void;
  onConnectProvider: (provider: string) => void;
  onLocalUpload: () => void;
  onOpenAIPolicy: () => void;
  onOpenGmailResults: () => void;
  onReload: () => void;
};

function CapabilityIcon({ capability }: Pick<IntegrationItem, "capability">) {
  if (capability === "channel") return <Mail />;
  if (capability === "ai") return <Bot />;
  if (capability === "storage") return <FolderTree />;
  return <CalendarDays />;
}

export function IntegrationsModule({
  collapsed,
  items,
  systemState,
  gmailSyncing,
  gmailSyncStatus,
  onSyncGmail,
  onSelectFolder,
  onConnectProvider,
  onLocalUpload,
  onOpenAIPolicy,
  onOpenGmailResults,
  onReload,
}: Props) {
  return (
    <section className={`module-overlay ${collapsed ? "collapsed" : ""}`}>
      <div className="module-page">
        <div className="integration-grid">
          {items.map((item) => (
            <article className="card integration-card" key={item.key}>
              <div className={`integration-icon ${item.connected ? "connected" : ""}`}>
                <CapabilityIcon capability={item.capability} />
              </div>
              <div>
                <h2>{item.name}</h2>
                <p>{item.description}</p>
                <small>{item.capability} · {item.provider}</small>
                {item.detail && <small className="integration-detail">{item.detail}</small>}
              </div>
              <span className={item.connected ? "connected" : ""}>
                {item.connected ? "Готово" : item.available ? "Не подключено" : "Недоступно"}
              </span>
              {item.action === "sync" && item.connected ? (
                <button onClick={onSyncGmail} disabled={gmailSyncing}>
                  {gmailSyncing ? "Получаю…" : "Получить письма"}
                </button>
              ) : item.action === "select_source" && item.connected ? (
                <button onClick={() => onSelectFolder(item.provider)}>Выбрать папку</button>
              ) : item.action === "oauth" ? (
                <button onClick={() => onConnectProvider(item.provider)} disabled={!item.available}>{item.connected ? "Переподключить" : "Подключить"}</button>
              ) : item.action === "local_upload" ? (
                <button onClick={onLocalUpload}>Загрузить папку</button>
              ) : item.action === "ai_policy" ? (
                <button onClick={onOpenAIPolicy}>Политика AI</button>
              ) : null}
              {item.action === "sync" && gmailSyncStatus && (
                <div className="integration-sync-result">
                  <small>{gmailSyncStatus}</small>
                  {!gmailSyncing && <button onClick={onOpenGmailResults}>Открыть AI Secretary</button>}
                </div>
              )}
            </article>
          ))}
          {!items.length && <div className="card empty"><Activity /><p>Каталог подключений загружается…</p></div>}
        </div>
        <section className="card system-checks">
          <div className="card-head">
            <div><h2>Состояние системы</h2><p>Проверка обязательных и внешних компонентов</p></div>
            <button onClick={onReload}>Проверить снова</button>
          </div>
          {Object.entries(systemState?.checks || {}).map(([name, check]) => (
            <div className="check-row" key={name}>
              <span className={check.ok ? "ok" : "bad"}></span>
              <strong>{name.replaceAll("_", " ")}</strong>
              <p>{check.message}</p>
              <small>{check.required ? "обязательно" : "интеграция"}</small>
            </div>
          ))}
        </section>
      </div>
    </section>
  );
}
