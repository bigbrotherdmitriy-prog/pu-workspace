import { ArrowRight } from "lucide-react";

type Props = {
  tasks: number;
  obligations: number;
  onOpenTasks: () => void;
};

export function OverdueMetric({ tasks, obligations, onOpenTasks }: Props) {
  return (
    <button type="button" className="danger" onClick={onOpenTasks}
      aria-label={`Просрочено: задач ${tasks}, обязательств ${obligations}. Открыть просроченные задачи`}>
      <span>Просрочено всего</span>
      <strong>{tasks + obligations}</strong>
      <span>Задачи: {tasks} · обязательства: {obligations}</span>
      <small>Открыть задачи <ArrowRight /></small>
    </button>
  );
}
