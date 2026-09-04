import { Activity, RefreshCw } from "lucide-react";

export type AnalyticsDistribution = { key: string; count: number }[];

export type ProjectAnalytics = {
  summary: {
    documents: number;
    document_coverage: number;
    open_tasks: number;
    overdue_tasks: number;
    open_risks: number;
    pending_decisions: number;
    contracts: number;
    active_contracts: number;
    messages: number;
    pending_messages: number;
  };
  documents_by_source: AnalyticsDistribution;
  documents_by_status: AnalyticsDistribution;
  tasks_by_status: AnalyticsDistribution;
  risks_by_criticality: AnalyticsDistribution;
  messages_by_channel: AnalyticsDistribution;
};

type Props = {
  analytics: ProjectAnalytics | null;
  collapsed: boolean;
  onReload: () => void;
};

const analyticsLabel = (value: string) => ({
  assigned: "Назначены",
  in_progress: "В работе",
  completed: "Выполнены",
  needs_confirmation: "Требуют подтверждения",
  confirmed: "Подтверждены",
  mitigating: "Снижаются",
  resolved: "Закрыты",
  low: "Низкая",
  medium: "Средняя",
  high: "Высокая",
  critical: "Критическая",
  email: "Email",
  telegram: "Telegram",
  manual: "Вручную",
  document: "Документ",
  discovered: "Обнаружены",
  indexed: "Проиндексированы",
  unknown: "Не определено",
} as Record<string, string>)[value] || value.replaceAll("_", " ");

export function AnalyticsModule({ analytics, collapsed, onReload }: Props) {
  const distributions: [string, AnalyticsDistribution][] = analytics ? [
    ["Источники документов", analytics.documents_by_source],
    ["Состояние документов", analytics.documents_by_status],
    ["Состояние задач", analytics.tasks_by_status],
    ["Критичность рисков", analytics.risks_by_criticality],
    ["Каналы входящих", analytics.messages_by_channel],
  ] : [];

  return <section className={`module-overlay ${collapsed ? "collapsed" : ""}`}>
    <div className="module-page analytics-page">
      <section className="analytics-hero card">
        <div>
          <span className="eyebrow">PROJECT CORE</span>
          <h2>Состояние проекта</h2>
          <p>Единая аналитика по документам, задачам, рискам и входящим — независимо от подключённых сервисов.</p>
        </div>
        <button type="button" onClick={onReload}><RefreshCw /> Обновить</button>
      </section>
      {analytics ? <>
        <section className="analytics-metrics">
          {[
            ["Документы", analytics.summary.documents],
            ["Извлечена сводка", `${analytics.summary.document_coverage}%`],
            ["Открытые задачи", analytics.summary.open_tasks],
            ["Просрочено", analytics.summary.overdue_tasks],
            ["Открытые риски", analytics.summary.open_risks],
            ["Входящие без реакции", analytics.summary.pending_messages],
          ].map(([label, value]) => <article key={String(label)}><span>{label}</span><strong>{value}</strong></article>)}
        </section>
        <section className="analytics-grid">
          {distributions.map(([title, rows]) => {
            const maximum = Math.max(1, ...rows.map((row) => row.count));
            return <section className="card analytics-panel" key={title}>
              <h2>{title}</h2>
              <div className="analytics-bars">
                {rows.map((row) => <div key={row.key}>
                  <span>{analyticsLabel(row.key)}</span><b>{row.count}</b>
                  <i><em style={{ width: `${Math.max(4, row.count / maximum * 100)}%` }} /></i>
                </div>)}
                {!rows.length && <p className="analytics-empty">Данных пока нет</p>}
              </div>
            </section>;
          })}
          <section className="card analytics-panel analytics-summary">
            <h2>Контур управления</h2>
            <p><strong>{analytics.summary.active_contracts}</strong> активных договоров из {analytics.summary.contracts}</p>
            <p><strong>{analytics.summary.pending_decisions}</strong> решений требуют фиксации</p>
            <p><strong>{analytics.summary.messages}</strong> входящих обработано системой</p>
          </section>
        </section>
      </> : <section className="card empty"><Activity /><p>Аналитика загружается…</p></section>}
    </div>
  </section>;
}
