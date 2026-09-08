/** Wire contract of execution_finance GET/PUT /baselines/{id}/graph (a20).
 * Dates are calculated only by the server. Legacy intent stays blank until entered.
 */
export const constraints = ["asap", "snet", "fnet", "snlt", "fnlt", "mso", "mfo"] as const;
export type Constraint = typeof constraints[number];
export type GraphItem = {
  id: number; title: string; duration_days: number | null; is_milestone: boolean | null;
  predecessor_ids: string | null; constraint_type: Constraint | null;
  constraint_date: string | null; not_before_date: string | null;
  planned_start: string | null; planned_finish: string | null;
  planned_progress?: number; actual_progress?: number; status?: string;
  wbs_parent_id: number | null; wbs_order: number; is_summary: boolean; wbs_level: number;
};
export type TaskPlan = { task_id: number; earliest_start: string; earliest_finish: string;
  latest_start: string; latest_finish: string; total_float_days: number; free_float_days: number };
export type SchedulePlan = { project_start: string; project_finish: string | null; tasks: TaskPlan[];
  topological_order: number[]; critical_ids: number[]; critical_edges: [number, number][] };
export type Graph = {
  baseline_id: number; version: number; status: string; graph_revision: number;
  planning_mode: string; project_start: string | null; items: GraphItem[]; plan: SchedulePlan | null;
};
export type DraftItem = {
  id: number; duration: string; milestone: boolean; dependencies: string;
  constraint: Constraint; constraintDate: string; notBefore: string;
};
export type Draft = { anchor: string; items: DraftItem[] };
export type GraphPut = {
  expected_graph_revision: number; project_start: string;
  items: { id: number; duration_days: number; is_milestone: boolean; predecessor_ids: string | null;
    constraint_type: Constraint; constraint_date: string | null; not_before_date: string | null }[];
};
const object = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const integer = (v: unknown): v is number => typeof v === "number" && Number.isSafeInteger(v) && v > 0;
export function validDate(v: unknown): v is string {
  if (typeof v !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(v) || v.startsWith("0000")) return false;
  const date = new Date(`${v}T00:00:00Z`);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === v;
}
const nullableDate = (v: unknown) => v === null || validDate(v);
const exactKeys = (value: Record<string, unknown>, keys: string[]) => Object.keys(value).sort().join() === [...keys].sort().join();
/** Validate wire identity/shape only. Dates, criticality and slack are not recalculated. */
function parsePlan(raw: unknown, items: GraphItem[], anchor: string | null, mode: string): SchedulePlan | null {
  if (raw == null) return null; // Explicitly unavailable, never a made-up zero-slack plan.
  if (!object(raw) || mode !== "calendar_graph" || !validDate(raw.project_start) || raw.project_start !== anchor
    || !exactKeys(raw, ["project_start", "project_finish", "tasks", "topological_order", "critical_ids", "critical_edges"])
    || !Array.isArray(raw.tasks) || !Array.isArray(raw.critical_edges)) throw new Error("invalid_schedule_plan");
  const leaves = items.filter(item => !item.is_summary);
  if (raw.tasks.length !== leaves.length || raw.critical_edges.length > leaves.length * leaves.length) throw new Error("invalid_schedule_plan");
  const byId = new Map(leaves.map(item => [item.id, item]));
  function ids(value: unknown, complete: boolean): number[] {
    if (!Array.isArray(value) || (complete && value.length !== leaves.length) || value.length > leaves.length
      || value.some(id => !integer(id) || !byId.has(id)) || new Set(value).size !== value.length) throw new Error("invalid_plan_ids");
    return [...value] as number[];
  }
  const order = ids(raw.topological_order, true), critical = ids(raw.critical_ids, false);
  const positions = new Map(order.map((id, index) => [id, index]));
  const seen = new Set<number>();
  const tasks = raw.tasks.map((v): TaskPlan => {
    if (!object(v) || !exactKeys(v, ["task_id", "earliest_start", "earliest_finish", "latest_start", "latest_finish", "total_float_days", "free_float_days"])
      || !integer(v.task_id) || !byId.has(v.task_id) || seen.has(v.task_id)
      || !validDate(v.earliest_start) || !validDate(v.earliest_finish) || !validDate(v.latest_start) || !validDate(v.latest_finish)
      || v.earliest_start > v.earliest_finish || v.latest_start > v.latest_finish
      || v.latest_start < v.earliest_start || v.latest_finish < v.earliest_finish
      || typeof v.total_float_days !== "number" || !Number.isSafeInteger(v.total_float_days) || v.total_float_days < 0
      || typeof v.free_float_days !== "number" || !Number.isSafeInteger(v.free_float_days) || v.free_float_days < 0
      || v.free_float_days > v.total_float_days) throw new Error("invalid_task_plan");
    const item = byId.get(v.task_id)!;
    if (item.planned_start !== v.earliest_start || item.planned_finish !== v.earliest_finish
      || critical.includes(v.task_id) !== (v.total_float_days === 0)) throw new Error("plan_item_mismatch");
    seen.add(v.task_id);
    return { task_id: v.task_id, earliest_start: v.earliest_start, earliest_finish: v.earliest_finish,
      latest_start: v.latest_start, latest_finish: v.latest_finish, total_float_days: v.total_float_days, free_float_days: v.free_float_days };
  });
  if (leaves.length ? !validDate(raw.project_finish) || raw.project_finish !== tasks.map(t => t.earliest_finish).sort().at(-1)
      : raw.project_finish !== null) throw new Error("invalid_plan_horizon");
  const declared = new Set<string>();
  for (const item of items) {
    if (!item.predecessor_ids?.trim()) continue;
    for (const token of item.predecessor_ids.split(/[,;]/)) {
      const match = /^\s*([0-9]+)\s*(FS|SS|FF|SF)?\s*(?:([+-])\s*([0-9]+)\s*[dд])?\s*$/i.exec(token);
      const predecessor = Number(match?.[1]);
      const key = `${predecessor}:${item.id}`;
      if (!match || !integer(predecessor) || !byId.has(predecessor) || declared.has(key)
        || positions.get(predecessor)! >= positions.get(item.id)!) throw new Error("invalid_plan_dependency");
      declared.add(key);
    }
  }
  const edges = new Set<string>();
  const criticalEdges = raw.critical_edges.map((pair): [number, number] => {
    if (!Array.isArray(pair) || pair.length !== 2 || !integer(pair[0]) || !integer(pair[1])
      || !critical.includes(pair[0]) || !critical.includes(pair[1]) || !declared.has(`${pair[0]}:${pair[1]}`)
      || edges.has(`${pair[0]}:${pair[1]}`)) throw new Error("invalid_critical_edge");
    edges.add(`${pair[0]}:${pair[1]}`); return [pair[0], pair[1]];
  });
  return { project_start: raw.project_start, project_finish: raw.project_finish as string | null,
    tasks, topological_order: order, critical_ids: critical, critical_edges: criticalEdges };
}
export function parseGraph(value: unknown, baselineId: number): Graph {
  if (!object(value) || value.baseline_id !== baselineId || !integer(value.baseline_id) ||
      !integer(value.version) || !integer(value.graph_revision) || typeof value.status !== "string" ||
      typeof value.planning_mode !== "string" || !nullableDate(value.project_start) ||
      !Array.isArray(value.items) || value.items.length > 500) throw new Error("invalid_graph");
  const seen = new Set<number>();
  const items = value.items.map((v: unknown): GraphItem => {
    if (!object(v) || !integer(v.id) || seen.has(v.id) || typeof v.title !== "string" ||
        !(v.duration_days === null || typeof v.duration_days === "number" && Number.isInteger(v.duration_days) && v.duration_days >= 0 && v.duration_days <= 10000) ||
        !(v.is_milestone === null || typeof v.is_milestone === "boolean") ||
        !(v.predecessor_ids === null || typeof v.predecessor_ids === "string" && v.predecessor_ids.length <= 2000) ||
        !(v.constraint_type === null || constraints.includes(v.constraint_type as Constraint)) ||
        !nullableDate(v.constraint_date) || !nullableDate(v.not_before_date) ||
        !nullableDate(v.planned_start) || !nullableDate(v.planned_finish) ||
        !(v.wbs_parent_id === undefined || v.wbs_parent_id === null || integer(v.wbs_parent_id)) ||
        !(v.wbs_order === undefined || typeof v.wbs_order === "number" && Number.isSafeInteger(v.wbs_order) && v.wbs_order >= 0) ||
        !(v.is_summary === undefined || typeof v.is_summary === "boolean") ||
        !(v.wbs_level === undefined || typeof v.wbs_level === "number" && Number.isSafeInteger(v.wbs_level) && v.wbs_level >= 0 && v.wbs_level <= 4)) throw new Error("invalid_graph_item");
    seen.add(v.id);
    return { ...v, wbs_parent_id: (v.wbs_parent_id ?? null) as number | null,
      wbs_order: (v.wbs_order ?? 0) as number, is_summary: (v.is_summary ?? false) as boolean,
      wbs_level: (v.wbs_level ?? 0) as number } as GraphItem;
  });
  return { baseline_id: baselineId, version: value.version, graph_revision: value.graph_revision,
    status: value.status, planning_mode: value.planning_mode, project_start: value.project_start as string | null, items,
    plan: parsePlan(value.plan, items, value.project_start as string | null, value.planning_mode) };
}
export function toDraft(graph: Graph): Draft {
  return { anchor: graph.project_start ?? "", items: graph.items.filter(v => !v.is_summary).map(v => ({ id: v.id,
    duration: v.duration_days === null ? "" : String(v.duration_days), milestone: v.is_milestone === true,
    dependencies: v.predecessor_ids ?? "", constraint: v.constraint_type ?? "asap",
    constraintDate: v.constraint_date ?? "", notBefore: v.not_before_date ?? "" })) };
}
export function sameItems(draft: Draft, graph: Graph): boolean {
  const leaves = graph.items.filter(item => !item.is_summary);
  return draft.items.length === leaves.length && draft.items.every(item => leaves.some(v => v.id === item.id));
}
export function putPayload(draft: Draft, graph: Graph): GraphPut {
  if (graph.status !== "draft" || !validDate(draft.anchor) || !sameItems(draft, graph) ||
      new Set(draft.items.map(v => v.id)).size !== draft.items.length) throw new Error("invalid_draft");
  return { expected_graph_revision: graph.graph_revision, project_start: draft.anchor, items: draft.items.map(v => {
    const duration = Number(v.duration);
    if (!/^\d+$/.test(v.duration) || !Number.isInteger(duration) || duration > 10000 ||
      (v.milestone ? duration !== 0 : duration < 1) || v.dependencies.length > 2000 ||
      !constraints.includes(v.constraint) || (v.constraint === "asap" ? v.constraintDate !== "" : !validDate(v.constraintDate)) ||
      (v.notBefore !== "" && !validDate(v.notBefore))) throw new Error("invalid_intent");
    return { id: v.id, duration_days: duration, is_milestone: v.milestone,
      predecessor_ids: v.dependencies.trim() || null, constraint_type: v.constraint,
      constraint_date: v.constraintDate || null, not_before_date: v.notBefore || null };
  }) };
}
