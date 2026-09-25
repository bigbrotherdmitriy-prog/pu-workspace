import { Activity, ArrowRight, Bot, Route } from "lucide-react";
import { ProjectIsometric } from "./ProjectIsometric";

export type WorkCenterSummary = {
  attention: number;
  overdue_tasks: number;
  overdue_obligations: number;
  open_risks: number;
  pending_decisions: number;
  open_obligations: number;
  unread_notifications: number;
};

type Props = {
  projectName: string;
  nextStep?: string;
  summary: WorkCenterSummary | null;
  budgetActual?: number;
  budgetPlanned?: number;
  onOpenPlan: () => void;
  onAskAi: () => void;
  onOpenDocuments: () => void;
  onOpenSchedule: () => void;
  onOpenFinance: () => void;
  onOpenObligations: () => void;
  onOpenOverdueTasks: () => void;
  onOpenOverdueObligations: () => void;
  onOpenRisks: () => void;
  onOpenDecisions: () => void;
  onOpenNotifications: () => void;
};

export function budgetProgress(actual?: number, planned?: number) {
  if (!planned || planned <= 0 || actual == null) return null;
  return Math.round((actual / planned) * 100);
}

export function attentionBreakdown(summary: WorkCenterSummary | null) {
  return [
    { label: "Просроченные задачи", value: summary?.overdue_tasks || 0, target: "tasks" },
    { label: "Просроченные обязательства", value: summary?.overdue_obligations || 0, target: "obligations" },
    { label: "Открытые риски", value: summary?.open_risks || 0, target: "risks" },
    { label: "Ждут решения", value: summary?.pending_decisions || 0, target: "decisions" },
    { label: "Непрочитанные уведомления", value: summary?.unread_notifications || 0, target: "notifications" },
  ] as const;
}

export function WorkCenterDashboard({
  projectName, nextStep, summary, budgetActual, budgetPlanned,
  onOpenPlan, onAskAi, onOpenDocuments, onOpenSchedule, onOpenFinance,
  onOpenObligations, onOpenOverdueTasks, onOpenOverdueObligations,
  onOpenRisks, onOpenDecisions, onOpenNotifications,
}: Props) {
  const focusRows = attentionBreakdown(summary);
  const focusActions = {
    tasks: onOpenOverdueTasks,
    obligations: onOpenOverdueObligations,
    risks: onOpenRisks,
    decisions: onOpenDecisions,
    notifications: onOpenNotifications,
  };
  const budgetPercent = budgetProgress(budgetActual, budgetPlanned);
  const overdueTotal = (summary?.overdue_tasks || 0) + (summary?.overdue_obligations || 0);
  const metrics = [
    { label: "Обязательства", value: summary?.open_obligations || 0, action: onOpenObligations },
    { label: "Просрочено", value: overdueTotal, action: onOpenOverdueTasks },
    { label: "Риски", value: summary?.open_risks || 0, action: onOpenRisks },
    { label: "Уведомления", value: summary?.unread_notifications || 0, action: onOpenNotifications },
    { label: "Бюджет освоен", value: budgetPercent == null ? "—" : `${budgetPercent}%`, action: onOpenFinance },
  ];

  return <>
    <section className="dashboard-overview-deck">
      <div className="dashboard-hero">
        <div className="dashboard-hero-copy">
          <span className="dashboard-kicker"><Activity /> Оперативный контур · {projectName || "Текущий проект"}</span>
          <h2>Штаб управления проектом</h2>
          <p>{nextStep || (summary?.attention
            ? `Сначала разберите ${summary.attention} пунктов, требующих вашего решения.`
            : "Проект под контролем. Новых критических событий нет.")}</p>
          <div className="dashboard-hero-actions">
            <button type="button" onClick={onOpenPlan}><Route /> Открыть план дня</button>
            <button type="button" className="secondary" onClick={onAskAi}><Bot /> Запросить сводку</button>
          </div>
        </div>
        <ProjectIsometric onDocuments={onOpenDocuments} onSchedule={onOpenSchedule} onFinance={onOpenFinance} />
        <aside className={`dashboard-focus ${summary?.attention ? "needs-attention" : "clear"}`} aria-label="Требует решения">
          <span>{summary?.attention ? "Требует решения" : "Контур стабилен"}</span>
          <strong>{String(summary?.attention || 0).padStart(2, "0")}</strong>
          <p>контрольных пунктов</p>
          <nav className="hq-live-controls" aria-label="Разбивка контрольных пунктов">
            {focusRows.map((row) => <button type="button" key={row.target} onClick={focusActions[row.target]}>
              <span>{row.label}</span><b>{row.value}</b>
            </button>)}
          </nav>
        </aside>
      </div>
      <div className="metrics dashboard-metrics fl-metrics">
        {metrics.map((metric) => <button type="button" key={metric.label} onClick={metric.action} aria-label={`Открыть раздел: ${metric.label}`}>
          <span>{metric.label}</span><strong>{metric.value}</strong><small>В реестр <ArrowRight /></small>
        </button>)}
      </div>
    </section>
  </>;
}
