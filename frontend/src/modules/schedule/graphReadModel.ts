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
};
export type Graph = {
  baseline_id: number; version: number; status: string; graph_revision: number;
  planning_mode: string; project_start: string | null; items: GraphItem[];
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
        !nullableDate(v.planned_start) || !nullableDate(v.planned_finish)) throw new Error("invalid_graph_item");
    seen.add(v.id);
    return v as GraphItem;
  });
  return { baseline_id: baselineId, version: value.version, graph_revision: value.graph_revision,
    status: value.status, planning_mode: value.planning_mode, project_start: value.project_start as string | null, items };
}
export function toDraft(graph: Graph): Draft {
  return { anchor: graph.project_start ?? "", items: graph.items.map(v => ({ id: v.id,
    duration: v.duration_days === null ? "" : String(v.duration_days), milestone: v.is_milestone === true,
    dependencies: v.predecessor_ids ?? "", constraint: v.constraint_type ?? "asap",
    constraintDate: v.constraint_date ?? "", notBefore: v.not_before_date ?? "" })) };
}
export function sameItems(draft: Draft, graph: Graph): boolean {
  return draft.items.length === graph.items.length && draft.items.every(item => graph.items.some(v => v.id === item.id));
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
