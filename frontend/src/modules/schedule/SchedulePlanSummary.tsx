import { parseGraph, type Graph } from "./graphReadModel";

type Props = { graph: Graph; stale?: boolean; hasLocalEdits?: boolean };

/** Read-only projection of one saved server response, never a browser planner. */
export function SchedulePlanSummary({ graph, stale = false, hasLocalEdits = false }: Props) {
  if (stale) return <section aria-label="Сохранённый расчёт ГПР"><p role="status">Расчёт скрыт до подтверждения актуальной серверной ревизии.</p></section>;
  let checked: Graph;
  try { checked = parseGraph(graph, graph.baseline_id); }
  catch { return <section aria-label="Сохранённый расчёт ГПР"><p role="status">Серверный расчёт неполон или не соответствует этапам. Резервы не показаны.</p></section>; }
  const plan = checked.plan;
  if (!plan) return <section aria-label="Сохранённый расчёт ГПР"><p role="status">Сохранённый серверный расчёт недоступен. Критичность и резервы не определены.</p></section>;
  const title = (id: number) => `${checked.items.find(item => item.id === id)!.title} (#${id})`;
  const critical = new Set(plan.critical_ids);
  return <section className="schedule-plan-summary" aria-label="Сохранённый расчёт ГПР">
    <h3>Критические работы и резервы</h3>
    <p>Сохранённая версия {checked.version}, ревизия графа {checked.graph_revision}. Данные рассчитаны сервером.</p>
    {checked.status === "approved" && <p>Утверждённая версия — только просмотр.</p>}
    {hasLocalEdits && <p role="status">Локальные правки не включены в этот сохранённый расчёт.</p>}
    <p>Горизонт расчёта: {plan.project_start} → {plan.project_finish ?? "нет этапов"}.</p>
    <p>Критические работы имеют нулевой полный резерв. Возможны несколько ветвей или отдельные работы с фиксированной датой, а не один критический путь.</p>
    {plan.critical_ids.length ? <ul aria-label="Критические работы">{plan.critical_ids.map(id => <li key={id}>{title(id)}</li>)}</ul>
      : <p>Критические работы отсутствуют в серверном результате.</p>}
    <h4>Критические связи</h4>
    {plan.critical_edges.length ? <ul aria-label="Критические связи">{plan.critical_edges.map(([from, to]) => <li key={`${from}:${to}`}>{title(from)} → {title(to)}</li>)}</ul>
      : <p>Критических связей в серверном результате нет.</p>}
    {!!plan.tasks.length && <div className="schedule-graph-scroll"><table>
      <caption>Резервы сохранённого плана, календарные дни</caption>
      <thead><tr><th scope="col">Этап</th><th scope="col">Критичность</th><th scope="col">Раннее начало / окончание</th>
        <th scope="col">Позднее начало / окончание</th><th scope="col">Полный резерв, дней</th><th scope="col">Свободный резерв, дней</th></tr></thead>
      <tbody>{plan.tasks.map(task => <tr key={task.task_id}><th scope="row">{title(task.task_id)}</th>
        <td>{critical.has(task.task_id) ? "Критическая" : "Есть резерв"}</td>
        <td>{task.earliest_start} / {task.earliest_finish}</td><td>{task.latest_start} / {task.latest_finish}</td>
        <td>{task.total_float_days}</td><td>{task.free_float_days}</td></tr>)}</tbody>
    </table></div>}
  </section>;
}
