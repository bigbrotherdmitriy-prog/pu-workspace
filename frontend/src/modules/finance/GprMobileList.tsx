import { ChevronDown, ChevronRight, Diamond } from "lucide-react";
import type { FinanceOverview } from "./types";

type Task = FinanceOverview["schedule"][number];

/** Per-level indent of the phone list: wide enough to read the tree, narrow enough for deep WBS. */
export const GPR_MOBILE_INDENT = 14;

const dateLabel = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit", year: "2-digit", timeZone: "UTC" });

function formatDate(value?: string) {
  return value ? dateLabel.format(new Date(`${value}T00:00:00Z`)) : "—";
}

type Props = {
  tasks: Task[];
  numberOf: (task: Task) => number;
  depthOf: (task: Task) => number;
  parents: Set<number>;
  collapsedIds: Set<number>;
  critical: Set<number>;
  showCritical: boolean;
  selectedId?: number;
  onSelect: (id: number) => void;
  onToggle: (id: number) => void;
};

/**
 * Phone view of the schedule (≤ 760 px): stages and milestones as a tree list
 * with dates, progress and a critical-path mark, instead of the wide table and
 * the Gantt chart. Uses the same visible rows, selection and collapse state.
 */
export function GprMobileList({ tasks, numberOf, depthOf, parents, collapsedIds, critical, showCritical, selectedId, onSelect, onToggle }: Props) {
  return <ol className="gpr-mobile-list" aria-label="Этапы и вехи ГПР">
    {tasks.map((task) => {
      const progress = Math.max(0, Math.min(100, task.actual_progress || task.planned_progress || 0));
      const isCritical = showCritical && critical.has(task.id);
      const collapsed = collapsedIds.has(task.id);
      return <li key={task.id} className={`${selectedId === task.id ? "selected" : ""} ${isCritical ? "critical" : ""}`.trim() || undefined}
        style={{ paddingLeft: depthOf(task) * GPR_MOBILE_INDENT }} data-depth={depthOf(task)}>
        {parents.has(task.id)
          ? <button type="button" className="gpr-mobile-collapse" aria-expanded={!collapsed}
            aria-label={`${collapsed ? "Развернуть" : "Свернуть"} ${task.title}`} onClick={() => onToggle(task.id)}>
            {collapsed ? <ChevronRight /> : <ChevronDown />}
          </button>
          : <span className="gpr-mobile-collapse-spacer" aria-hidden="true" />}
        <button type="button" className="gpr-mobile-task" aria-pressed={selectedId === task.id} onClick={() => onSelect(task.id)}>
          <span className="gpr-mobile-title">
            <span className="gpr-mobile-no">{numberOf(task)}</span>
            {task.is_milestone && <Diamond className="gpr-mobile-milestone" aria-label="Веха" />}
            <strong>{task.title}</strong>
          </span>
          <span className="gpr-mobile-dates">
            {task.is_milestone
              ? <>Веха · {formatDate(task.planned_finish || task.planned_start)}</>
              : <>{formatDate(task.planned_start)} — {formatDate(task.planned_finish)}</>}
          </span>
          <span className="gpr-mobile-progress" aria-label={`Выполнено ${progress}%`}>
            <span className="gpr-mobile-progress-bar"><i style={{ width: `${progress}%` }} /></span>
            <b>{progress}%</b>
          </span>
          {isCritical && <span className="gpr-mobile-critical">Критический путь</span>}
        </button>
      </li>;
    })}
  </ol>;
}
