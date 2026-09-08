import { expect, it } from 'vitest';
import { parseGraph } from './graphReadModel';
import { planGraphFixture } from './schedulePlanFixtures';
import { rowDraft, rowsPayload, rowsResponse } from './scheduleRows';
it('keeps complete partition and resolves new references only on server', () => {
 const graph = parseGraph({...planGraphFixture(), status:'draft'},8); const draft=rowDraft(graph);
 draft[0].title='Renamed'; draft.push({...draft[0],key:'new1',dependencies:'12FS+2d'});
 const payload=rowsPayload(graph,draft,graph.project_start!);
 expect(payload.items.at(-1)).toMatchObject({client_ref:'new1',dependencies:[{predecessor_id:12,link_type:'FS',lag_days:2}]});
 expect(payload.deleted_ids).toEqual([]);
 expect(()=>rowsResponse({...graph,graph_revision:4,client_ref_map:{}},graph,payload)).toThrow();
});
it('rejects dangling dependencies and empty names before request',()=>{
 const graph=parseGraph({...planGraphFixture(),status:'draft'},8); const draft=rowDraft(graph);
 draft[0].dependencies='new9FS'; expect(()=>rowsPayload(graph,draft,graph.project_start!)).toThrow();
 draft[0].dependencies='';draft[0].title=' ';expect(()=>rowsPayload(graph,draft,graph.project_start!)).toThrow();
});
it('accepts exact allocated identity and rejects reused or contradictory mapping',()=>{
 const graph=parseGraph({...planGraphFixture(),status:'draft'},8);const draft=rowDraft(graph);draft[2].key='new1';
 const payload=rowsPayload(graph,draft,graph.project_start!);
 const raw={...planGraphFixture(),status:'draft',graph_revision:4,client_ref_map:{new1:99}};
 raw.items[2].id=99;raw.plan.tasks[2].task_id=99;raw.plan.topological_order[2]=99;
 expect(rowsResponse(raw,graph,payload).items[2].id).toBe(99);
 expect(()=>rowsResponse({...raw,client_ref_map:{new1:14}},graph,payload)).toThrow();
 expect(()=>rowsResponse({...raw,client_ref_map:{new1:100}},graph,payload)).toThrow();
});
