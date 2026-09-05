import { Activity, Bot, CalendarDays, CheckCircle2, CircleDashed, FolderTree, Mail, Network, RefreshCw, ShieldCheck } from "lucide-react";

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
  const connectedCount = items.filter((item) => item.connected).length;
  const availableCount = items.filter((item) => item.available).length;
  return (
    <section className={`module-overlay ${collapsed ? "collapsed" : ""}`}>
      <div className="module-page integrations-page">
        <header className="integrations-command">
          <div>
            <span className="integrations-command-icon"><Network /></span>
            <div><small>Контур данных проекта</small><h2>Интеграции</h2><p>Каналы, хранилища и AI подключены к единому защищённому контуру.</p></div>
          </div>
          <div className="integrations-command-stats">
            <span><b>{connectedCount}</b><small>подключено</small></span>
            <span><b>{Math.max(availableCount - connectedCount, 0)}</b><small>ожидает</small></span>
            <button onClick={onReload}><RefreshCw /> Проверить контур</button>
          </div>
        </header>
        <div className="integrations-section-title"><span>01</span><div><h3>Источники и сервисы</h3><p>Управляйте подключениями без изменения проектных данных.</p></div></div>
        <div className="integration-grid">
          {items.map((item) => (
            <article className={`card integration-card ${item.connected ? "is-connected" : "is-idle"}`} key={item.key}>
              <div className={`integration-icon ${item.connected ? "connected" : ""}`}>
                <CapabilityIcon capability={item.capability} />
              </div>
              <div className="integration-copy">
                <small className="integration-code">{item.provider} / {item.capability}</small>
                <h2>{item.name}</h2>
                <p>{item.description}</p>
                {item.detail && <small className="integration-detail">{item.detail}</small>}
              </div>
              <span className={item.connected ? "connected" : ""}>
                {item.connected ? <CheckCircle2 /> : <CircleDashed />}
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
        <div className="integrations-section-title"><span>02</span><div><h3>Диагностика контура</h3><p>Технические проверки без раскрытия ключей и содержимого документов.</p></div></div>
        <section className="card system-checks integrations-system-checks">
          <div className="card-head">
            <div><ShieldCheck /><span><h2>Состояние системы</h2><p>Обязательные компоненты и внешние сервисы</p></span></div>
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
