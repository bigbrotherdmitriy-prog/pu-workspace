import { describe, expect, it } from 'vitest';
import { flattenWbs, moveWbsRow, parseWbsGraph, wbsDraft, wbsPayload, wbsResponse, type WbsRow } from './wbsReadModel';

const item = (id: number, title: string, parent: number | null, order: number, level: number, summary = false) => ({
  id, title, wbs_parent_id: parent, wbs_order: order, wbs_level: level, is_summary: summary,
  duration_days: summary ? null : 2, is_milestone: summary ? null : false,
  predecessor_ids: null,
});
const fixture = () => ({ baseline_id: 8, version: 2, status: 'draft', graph_revision: 3, project_start: '2026-09-01',
  items: [item(10, 'Preparation', 1, 0, 1), item(1, 'Phase one', null, 0, 0, true),
    item(20, 'Installation', 2, 0, 1), item(2, 'Phase two', null, 1, 0, true)],
  plan: { tasks: [{ task_id: 10 }, { task_id: 20 }], topological_order: [10, 20] },
});

describe('WBS runtime contract', () => {
  it('accepts a hierarchy and keeps summary rows out of the plan', () => {
    const graph = parseWbsGraph(fixture(), 8);
    expect(flattenWbs(wbsDraft(graph)).map(entry => [entry.row.key, entry.level])).toEqual([
      ['1', 0], ['10', 1], ['2', 0], ['20', 1],
    ]);
  });
  it.each([
    ['cycle', (raw: ReturnType<typeof fixture>) => { raw.items[1].wbs_parent_id = 2; raw.items[1].wbs_level = 1; raw.items[3].wbs_parent_id = 1; raw.items[3].wbs_level = 2; }],
    ['leaf parent', (raw: ReturnType<typeof fixture>) => { raw.items[2].wbs_parent_id = 10; raw.items[2].wbs_level = 2; }],
    ['wrong level', (raw: ReturnType<typeof fixture>) => { raw.items[0].wbs_level = 3; }],
    ['duplicate order', (raw: ReturnType<typeof fixture>) => { raw.items[3].wbs_order = 0; }],
    ['summary in plan', (raw: ReturnType<typeof fixture>) => { raw.plan.tasks.push({ task_id: 1 }); raw.plan.topological_order.push(1); }],
  ])('rejects %s fail closed', (_name, mutate) => {
    const raw = fixture(); mutate(raw); expect(() => parseWbsGraph(raw, 8)).toThrow();
  });
  it('rejects execution fields on a summary', () => {
    const raw = fixture(); raw.items[1].duration_days = 1; expect(() => parseWbsGraph(raw, 8)).toThrow('summary_has_execution_fields');
  });
  it('rejects an incomplete leaf-only plan', () => {
    const raw = fixture(); raw.plan.tasks.pop(); expect(() => parseWbsGraph(raw, 8)).toThrow('summary_in_plan');
  });
});

describe('WBS local editing', () => {
  it('moves between parents and normalizes sibling order', () => {
    const rows = wbsDraft(parseWbsGraph(fixture(), 8));
    const moved = moveWbsRow(rows, '20', '1', 0);
    expect(moved.find(row => row.key === '20')).toMatchObject({ parentKey: '1', order: 0 });
    expect(moved.find(row => row.key === '10')).toMatchObject({ parentKey: '1', order: 1 });
  });
  it('moves a sibling up', () => {
    const rows: WbsRow[] = [
      { key: '1', title: 'One', parentKey: null, order: 0, summary: false, duration: '1', milestone: false, dependencies: '' },
      { key: '2', title: 'Two', parentKey: null, order: 1, summary: false, duration: '1', milestone: false, dependencies: '' },
    ];
    const moved = moveWbsRow(rows, '2', null, 0);
    expect(flattenWbs(moved).map(entry => entry.row.key)).toEqual(['2', '1']);
  });
  it('rejects moving a summary below its descendant', () => {
    const rows = wbsDraft(parseWbsGraph(fixture(), 8));
    expect(() => moveWbsRow(rows, '1', '10', 0)).toThrow('invalid_wbs_move');
    expect(() => moveWbsRow(rows, '1', '2', 0)).not.toThrow();
    const nested = moveWbsRow(rows, '2', '1', 1);
    expect(() => moveWbsRow(nested, '1', '2', 0)).toThrow('wbs_cycle');
  });
  it('serializes exact parent references for new summaries and leaves', () => {
    const graph = parseWbsGraph(fixture(), 8); const rows = wbsDraft(graph);
    rows.push({ key: 'new1', title: 'New phase', parentKey: null, order: 2, summary: true, duration: '', milestone: false, dependencies: '' });
    rows.push({ key: 'new2', title: 'New task', parentKey: 'new1', order: 0, summary: false, duration: '3', milestone: false, dependencies: '10FS' });
    expect(wbsPayload(graph, rows).items.slice(-2)).toEqual([
      { client_ref: 'new1', title: 'New phase', wbs_parent_id: null, wbs_order: 2, is_summary: true, duration_days: null, is_milestone: null,
        constraint_type: null, constraint_date: null, not_before_date: null, dependencies: [] },
      { client_ref: 'new2', title: 'New task', wbs_parent_ref: 'new1', wbs_order: 0, is_summary: false, duration_days: 3, is_milestone: false,
        constraint_type: 'asap', constraint_date: null, not_before_date: null,
        dependencies: [{ predecessor_id: 10, link_type: 'FS', lag_days: 0 }] },
    ]);
  });
  it('rejects summary dependencies and a fifth nesting level', () => {
    const graph = parseWbsGraph(fixture(), 8); const rows = wbsDraft(graph);
    rows.find(row => row.key === '1')!.dependencies = '10FS'; expect(() => wbsPayload(graph, rows)).toThrow('summary_has_dependencies');
    rows.find(row => row.key === '1')!.dependencies = '';
    for (let i = 1; i <= 5; i += 1) rows.push({ key: `new${i}`, title: `Level ${i}`,
      parentKey: i === 1 ? '1' : `new${i - 1}`, order: 0, summary: true, duration: '', milestone: false, dependencies: '' });
    expect(() => wbsPayload(graph, rows)).toThrow();
  });
  it('accepts only the exact acknowledged save result', () => {
    const graph = parseWbsGraph(fixture(), 8); const rows = wbsDraft(graph);
    rows.find(row => row.key === '20')!.title = 'Installed scope';
    const payload = wbsPayload(graph, rows); const raw = fixture(); raw.graph_revision = 4;
    raw.items.find(entry => entry.id === 20)!.title = 'Installed scope';
    expect(wbsResponse({ ...raw, client_ref_map: {} }, graph, payload).graph_revision).toBe(4);
    expect(() => wbsResponse({ ...raw, client_ref_map: {}, items: fixture().items }, graph, payload)).toThrow('invalid_wbs_result');
  });
});
