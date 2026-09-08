/** Synthetic serialized calendar-day planner fixture; never a client document. */
export function planGraphFixture() {
  const item = (id: number, title: string, duration: number, start: string, finish: string, predecessors: string | null = null) => ({
    id, title, duration_days: duration, is_milestone: false, predecessor_ids: predecessors,
    constraint_type: "asap", constraint_date: null, not_before_date: null, planned_start: start, planned_finish: finish,
  });
  const task = (id: number, start: string, finish: string, latestStart = start, latestFinish = finish, slack = 0) => ({
    task_id: id, earliest_start: start, earliest_finish: finish, latest_start: latestStart, latest_finish: latestFinish,
    total_float_days: slack, free_float_days: slack,
  });
  return {
    baseline_id: 8, version: 2, graph_revision: 3, status: "approved", planning_mode: "calendar_graph", project_start: "2026-09-01",
    items: [item(12, "Synthetic preparation", 2, "2026-09-01", "2026-09-02"),
      item(13, "Synthetic installation", 1, "2026-09-03", "2026-09-03", "12FS"),
      item(14, "Synthetic independent work", 1, "2026-09-01", "2026-09-01")],
    plan: { project_start: "2026-09-01", project_finish: "2026-09-03",
      tasks: [task(12, "2026-09-01", "2026-09-02"), task(13, "2026-09-03", "2026-09-03"),
        task(14, "2026-09-01", "2026-09-01", "2026-09-03", "2026-09-03", 2)],
      topological_order: [12, 13, 14], critical_ids: [12, 13], critical_edges: [[12, 13]],
    },
  };
}
