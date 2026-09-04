import { AlertTriangle, Bot, CalendarClock, CheckCircle2 } from "lucide-react";

export type DailyBriefingItem = {
  kind: "overdue_task" | "overdue_obligation" | "risk" | "decision" | "draft" | "context";
  entity_id: number;
  priority: "critical" | "high" | "normal";
  title: string;
  due_date?: string;
  source_name?: string;
  evidence?: string;
  next_step: string;
};

export type DailyBriefing = {
  project_id: number;
  date: string;
  summary: {
    attention: number;
    overdue_tasks: number;
    overdue_obligations: number;
    open_risks: number;
    pending_decisions: number;
    drafts_waiting_approval: number;
    messages_waiting_context: number;
  };
  attention: DailyBriefingItem[];
  next_step: string;
  external_actions_created: false;
};

const sectionByKind: Record<DailyBriefingItem["kind"], string> = {
  overdue_task: "Задачи",
  overdue_obligation: "Обязательства",
  risk: "Риски и решения",
  decision: "Риски и решения",
  draft: "Письма",
  context: "AI Secretary",
};

const labelByKind: Record<DailyBriefingItem["kind"], string> = {
  overdue_task: "Просроченная задача",
  overdue_obligation: "Просроченное обязательство",
  risk: "Открытый риск",
  decision: "Требуется решение",
  draft: "Черновик ждёт проверки",
  context: "Нужно подтвердить контекст",
};

type Props = {
  briefing: DailyBriefing | null;
  onOpenSection: (section: string) => void;
};

export function DailyBriefingPanel({ briefing, onOpenSection }: Props) {
  if (!briefing) return null;

  return (
    <section className="card daily-briefing">
      <div className="daily-briefing-heading">
        <div className="source-icon"><Bot /></div>
        <div>
          <span className="eyebrow">ЕЖЕДНЕВНЫЙ КОНТРОЛЬ</span>
          <h2>Что требует внимания сегодня</h2>
          <p>{briefing.next_step}</p>
        </div>
        <b className={briefing.summary.attention ? "has-attention" : "all-clear"}>
          {briefing.summary.attention || "Всё под контролем"}
        </b>
      </div>

      <div className="daily-briefing-metrics">
        <span><strong>{briefing.summary.overdue_tasks}</strong> задач просрочено</span>
        <span><strong>{briefing.summary.overdue_obligations}</strong> обязательств просрочено</span>
        <span><strong>{briefing.summary.open_risks}</strong> рисков открыто</span>
        <span><strong>{briefing.summary.pending_decisions}</strong> решений ожидают</span>
        <span><strong>{briefing.summary.drafts_waiting_approval}</strong> черновиков на проверке</span>
        <span><strong>{briefing.summary.messages_waiting_context}</strong> контекстов не подтверждено</span>
      </div>

      {briefing.attention.length ? (
        <div className="daily-briefing-list">
          {briefing.attention.map((item) => (
            <article key={`${item.kind}-${item.entity_id}`} className={`priority-${item.priority}`}>
              <div className="daily-briefing-icon">
                {item.priority === "critical" ? <AlertTriangle /> : <CalendarClock />}
              </div>
              <div>
                <span>{labelByKind[item.kind]}{item.due_date ? ` · ${new Date(`${item.due_date}T00:00:00`).toLocaleDateString("ru-RU")}` : ""}</span>
                <strong>{item.title}</strong>
                <p>{item.next_step}</p>
                {(item.source_name || item.evidence) && <small>Источник: {item.source_name || "сообщение"}{item.evidence ? ` · ${item.evidence}` : ""}</small>}
              </div>
              <button className="secondary" onClick={() => onOpenSection(sectionByKind[item.kind])}>Открыть</button>
            </article>
          ))}
        </div>
      ) : (
        <div className="daily-briefing-empty"><CheckCircle2 /><p>Критических действий на сегодня нет.</p></div>
      )}

      <small className="daily-briefing-safety">AI Secretary только собирает контрольный список. Внешние действия создаются после подтверждения человеком.</small>
    </section>
  );
}
