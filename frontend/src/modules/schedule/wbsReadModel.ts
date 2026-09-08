export type WbsItem = {
  id: number;
  title: string;
  duration_days: number | null;
  is_milestone: boolean | null;
  predecessor_ids: string | null;
  wbs_parent_id: number | null;
  wbs_order: number;
  is_summary: boolean;
  wbs_level: number;
};

export type WbsGraph = {
  baseline_id: number;
  version: number;
  status: string;
  graph_revision: number;
  items: WbsItem[];
  plan: { tasks: { task_id: number }[]; topological_order: number[] } | null;
};

export type WbsRow = {
  key: string;
  title: string;
  parentKey: string | null;
  order: number;
  summary: boolean;
  duration: string;
  milestone: boolean;
  dependencies: string;
};

export type WbsPutItem = {
  id?: number;
  client_ref?: string;
  title: string;
  wbs_parent_id?: number | null;
  wbs_parent_ref?: string;
  wbs_order: number;
  is_summary: boolean;
  duration_days: number | null;
  is_milestone: boolean | null;
  predecessor_ids: string | null;
};

const object = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);
const positiveInteger = (value: unknown): value is number =>
  typeof value === 'number' && Number.isSafeInteger(value) && value > 0;
const nonNegativeInteger = (value: unknown): value is number =>
  typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;

function assertHierarchy(items: WbsItem[]): void {
  const byId = new Map(items.map(item => [item.id, item]));
  const siblingOrder = new Set<string>();
  for (const item of items) {
    if (item.wbs_parent_id !== null) {
      const parent = byId.get(item.wbs_parent_id);
      if (!parent || !parent.is_summary || parent.wbs_level + 1 !== item.wbs_level) throw new Error('invalid_wbs_parent');
    } else if (item.wbs_level !== 0) throw new Error('invalid_wbs_level');
    const position = `${item.wbs_parent_id ?? 'root'}:${item.wbs_order}`;
    if (siblingOrder.has(position)) throw new Error('duplicate_wbs_order');
    siblingOrder.add(position);
    const visited = new Set<number>([item.id]);
    let parentId = item.wbs_parent_id;
    while (parentId !== null) {
      if (visited.has(parentId)) throw new Error('wbs_cycle');
      visited.add(parentId);
      parentId = byId.get(parentId)?.wbs_parent_id ?? null;
    }
  }
}

export function parseWbsGraph(value: unknown, baselineId: number): WbsGraph {
  if (!object(value) || value.baseline_id !== baselineId || !positiveInteger(value.baseline_id)
    || !positiveInteger(value.version) || !positiveInteger(value.graph_revision)
    || typeof value.status !== 'string' || !Array.isArray(value.items) || value.items.length > 500)
    throw new Error('invalid_wbs_graph');
  const seen = new Set<number>();
  const items = value.items.map((raw): WbsItem => {
    if (!object(raw) || !positiveInteger(raw.id) || seen.has(raw.id) || typeof raw.title !== 'string'
      || raw.title.trim().length < 2 || raw.title.length > 500
      || !(raw.duration_days === null || nonNegativeInteger(raw.duration_days) && raw.duration_days <= 10000)
      || !(raw.is_milestone === null || typeof raw.is_milestone === 'boolean')
      || !(raw.predecessor_ids === null || typeof raw.predecessor_ids === 'string' && raw.predecessor_ids.length <= 2000)
      || !(raw.wbs_parent_id === null || positiveInteger(raw.wbs_parent_id))
      || !nonNegativeInteger(raw.wbs_order) || typeof raw.is_summary !== 'boolean'
      || !nonNegativeInteger(raw.wbs_level) || raw.wbs_level > 4) throw new Error('invalid_wbs_item');
    if (raw.is_summary) {
      if (raw.duration_days !== null || raw.is_milestone !== null || raw.predecessor_ids !== null) throw new Error('summary_has_execution_fields');
    } else if (typeof raw.is_milestone !== 'boolean' || !nonNegativeInteger(raw.duration_days)
      || (raw.is_milestone ? raw.duration_days !== 0 : raw.duration_days < 1)) throw new Error('invalid_wbs_leaf');
    seen.add(raw.id);
    return raw as WbsItem;
  });
  assertHierarchy(items);
  let plan: WbsGraph['plan'] = null;
  if (value.plan !== null) {
    if (!object(value.plan) || !Array.isArray(value.plan.tasks) || !Array.isArray(value.plan.topological_order)) throw new Error('invalid_wbs_plan');
    const leaves = items.filter(item => !item.is_summary).map(item => item.id).sort((a, b) => a - b);
    const taskIds = value.plan.tasks.map(task => object(task) && positiveInteger(task.task_id) ? task.task_id : 0);
    const order = value.plan.topological_order;
    if (taskIds.some(id => !id) || order.some(id => !positiveInteger(id))
      || [...taskIds].sort((a, b) => a - b).join() !== leaves.join()
      || [...order].sort((a, b) => a - b).join() !== leaves.join()
      || new Set(taskIds).size !== leaves.length || new Set(order).size !== leaves.length) throw new Error('summary_in_plan');
    plan = { tasks: value.plan.tasks as { task_id: number }[], topological_order: order as number[] };
  }
  return { baseline_id: baselineId, version: value.version, status: value.status,
    graph_revision: value.graph_revision, items, plan };
}

export function wbsDraft(graph: WbsGraph): WbsRow[] {
  return graph.items.map(item => ({ key: String(item.id), title: item.title,
    parentKey: item.wbs_parent_id === null ? null : String(item.wbs_parent_id), order: item.wbs_order,
    summary: item.is_summary, duration: item.duration_days === null ? '' : String(item.duration_days),
    milestone: item.is_milestone === true, dependencies: item.predecessor_ids ?? '' }));
}

export function flattenWbs(rows: WbsRow[]): { row: WbsRow; level: number }[] {
  const children = new Map<string | null, WbsRow[]>();
  for (const row of rows) children.set(row.parentKey, [...(children.get(row.parentKey) ?? []), row]);
  for (const list of children.values()) list.sort((a, b) => a.order - b.order || a.key.localeCompare(b.key, 'en', { numeric: true }));
  const result: { row: WbsRow; level: number }[] = [];
  const active = new Set<string>();
  function visit(parent: string | null, level: number): void {
    for (const row of children.get(parent) ?? []) {
      if (active.has(row.key) || level > 4) throw new Error('wbs_cycle');
      active.add(row.key); result.push({ row, level }); visit(row.key, level + 1); active.delete(row.key);
    }
  }
  visit(null, 0);
  if (result.length !== rows.length) throw new Error('invalid_wbs_parent');
  return result;
}

function normalize(rows: WbsRow[]): WbsRow[] {
  const groups = new Map<string | null, WbsRow[]>();
  for (const row of rows) groups.set(row.parentKey, [...(groups.get(row.parentKey) ?? []), row]);
  for (const list of groups.values()) list.sort((a, b) => a.order - b.order || a.key.localeCompare(b.key, 'en', { numeric: true }));
  return rows.map(row => ({ ...row, order: (groups.get(row.parentKey) ?? []).findIndex(item => item.key === row.key) }));
}

export function moveWbsRow(rows: WbsRow[], key: string, parentKey: string | null, index: number): WbsRow[] {
  const row = rows.find(item => item.key === key);
  const parent = parentKey === null ? null : rows.find(item => item.key === parentKey);
  if (!row || parentKey === key || parentKey !== null && (!parent || !parent.summary)) throw new Error('invalid_wbs_move');
  const descendants = new Set<string>();
  let changed = true;
  while (changed) { changed = false; for (const item of rows) if (item.parentKey && (item.parentKey === key || descendants.has(item.parentKey)) && !descendants.has(item.key)) { descendants.add(item.key); changed = true; } }
  if (parentKey !== null && descendants.has(parentKey)) throw new Error('wbs_cycle');
  const without = rows.filter(item => item.key !== key);
  const siblings = without.filter(item => item.parentKey === parentKey).sort((a, b) => a.order - b.order);
  const position = Math.max(0, Math.min(index, siblings.length));
  siblings.splice(position, 0, { ...row, parentKey });
  const orders = new Map(siblings.map((item, siblingIndex) => [item.key, siblingIndex]));
  const result = [...without, { ...row, parentKey }].map(item => orders.has(item.key) ? { ...item, order: orders.get(item.key)! } : item);
  flattenWbs(result); // Also rejects moving a subtree below the supported maximum level.
  return result;
}

export function wbsPayload(graph: WbsGraph, rows: WbsRow[]) {
  if (graph.status !== 'draft' || rows.length > 500 || new Set(rows.map(row => row.key)).size !== rows.length) throw new Error('invalid_wbs_draft');
  const flattened = flattenWbs(rows);
  const levels = new Map(flattened.map(entry => [entry.row.key, entry.level]));
  const keys = new Set(rows.map(row => row.key));
  const items: WbsPutItem[] = normalize(rows).map(row => {
    const existing = /^[1-9]\d*$/.test(row.key);
    if (!existing && !/^new[1-9]\d*$/.test(row.key) || row.title.trim().length < 2 || row.title.length > 500) throw new Error('invalid_wbs_identity');
    if (row.parentKey !== null && !keys.has(row.parentKey) || (levels.get(row.key) ?? 5) > 4) throw new Error('invalid_wbs_parent');
    let duration: number | null = null; let milestone: boolean | null = null;
    if (!row.summary) {
      duration = Number(row.duration); milestone = row.milestone;
      if (!/^\d+$/.test(row.duration) || !Number.isSafeInteger(duration) || duration > 10000
        || (milestone ? duration !== 0 : duration < 1) || row.dependencies.length > 2000) throw new Error('invalid_wbs_leaf');
    } else if (row.dependencies) throw new Error('summary_has_dependencies');
    return { ...(existing ? { id: Number(row.key) } : { client_ref: row.key }), title: row.title.trim(),
      ...(row.parentKey === null ? { wbs_parent_id: null } : row.parentKey.startsWith('new')
        ? { wbs_parent_ref: row.parentKey } : { wbs_parent_id: Number(row.parentKey) }),
      wbs_order: row.order, is_summary: row.summary, duration_days: duration,
      is_milestone: milestone, predecessor_ids: row.summary ? null : row.dependencies.trim() || null };
  });
  return { expected_graph_revision: graph.graph_revision,
    deleted_ids: graph.items.filter(item => !keys.has(String(item.id))).map(item => item.id), items };
}

export function wbsResponse(raw: unknown, previous: WbsGraph, payload: ReturnType<typeof wbsPayload>): WbsGraph {
  const result = parseWbsGraph(raw, previous.baseline_id);
  if (result.status !== 'draft' || result.version !== previous.version
    || result.graph_revision !== previous.graph_revision + 1 || !object(raw)) throw new Error('invalid_wbs_result');
  const mappingRaw = raw.client_ref_map;
  if (!object(mappingRaw)) throw new Error('invalid_wbs_mapping');
  const refs = payload.items.flatMap(item => item.client_ref ? [item.client_ref] : []);
  if (Object.keys(mappingRaw).sort().join() !== refs.sort().join()) throw new Error('invalid_wbs_mapping');
  const mapping = new Map(Object.entries(mappingRaw).map(([ref, id]) => {
    if (!positiveInteger(id) || previous.items.some(item => item.id === id)) throw new Error('invalid_wbs_mapping');
    return [ref, id];
  }));
  if (result.items.length !== payload.items.length) throw new Error('invalid_wbs_result');
  for (const intent of payload.items) {
    const id = intent.id ?? mapping.get(intent.client_ref!);
    const item = result.items.find(candidate => candidate.id === id);
    const parentId = intent.wbs_parent_ref ? mapping.get(intent.wbs_parent_ref) : intent.wbs_parent_id;
    if (!item || item.title !== intent.title || item.wbs_parent_id !== parentId || item.wbs_order !== intent.wbs_order
      || item.is_summary !== intent.is_summary || item.duration_days !== intent.duration_days
      || item.is_milestone !== intent.is_milestone || item.predecessor_ids !== intent.predecessor_ids)
      throw new Error('invalid_wbs_result');
  }
  return result;
}
