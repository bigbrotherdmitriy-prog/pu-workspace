import { parseGraph, putPayload, toDraft, validDate, type Graph } from './graphReadModel';
export type Row = ReturnType<typeof toDraft>['items'][number] & {key:string;title:string};
export const rowDraft=(graph:Graph):Row[]=>toDraft(graph).items.map(item=>({...item,key:String(item.id),title:graph.items.find(v=>v.id===item.id)!.title}));
export function rowsPayload(graph:Graph, rows:Row[], anchor:string){
 if(graph.status!=='draft'||!validDate(anchor)||rows.length>500 || new Set(rows.map(r=>r.key)).size!==rows.length)throw Error('invalid_rows');
 const keys=new Set(rows.map(r=>r.key));
 const items=rows.map(row=>{
  const existing=/^[1-9]\d*$/.test(row.key);
  if(existing ? !graph.items.some(i=>String(i.id)===row.key) : !/^new[1-9]\d*$/.test(row.key))throw Error('invalid_identity');
  if(row.title.trim().length<2 || row.title.length>500)throw Error('invalid_title');
  // Reuse existing intent validator with a local validation-only identity; never sent as an allocated ID.
  const validationGraph={...graph,items:[{...graph.items[0],id:1}]};
  const intent=putPayload({anchor,items:[{...row,id:1}]},validationGraph).items[0];
  const seen=new Set<string>();
  const dependencies=row.dependencies.trim()?row.dependencies.split(/[,;]/).map(token=>{
   const match=/^\s*(new[1-9]\d*|[1-9]\d*)\s*(FS|SS|FF|SF)?\s*(?:([+-])\s*(\d+)\s*[dд])?\s*$/i.exec(token);
   if(!match || !keys.has(match[1]) || match[1]===row.key || seen.has(match[1]))throw Error('invalid_dependency');
   seen.add(match[1]);const lag=Number(match[4]??0)*(match[3]==='-'?-1:1);
   if(!Number.isSafeInteger(lag)||Math.abs(lag)>10000)throw Error('invalid_lag');
   return {...(match[1].startsWith('new')?{predecessor_ref:match[1]}:{predecessor_id:Number(match[1])}),link_type:(match[2]??'FS').toUpperCase(),lag_days:lag};
  }):[];
  const {id: _id, predecessor_ids: _predecessors,...fields}=intent;
  return {...(existing?{id:Number(row.key)}:{client_ref:row.key}),title:row.title,...fields,dependencies};
 });
 return {expected_graph_revision:graph.graph_revision,project_start:anchor,deleted_ids:graph.items.filter(i=>!keys.has(String(i.id))).map(i=>i.id),items};
}
export function rowsResponse(raw:unknown, previous:Graph,payload:ReturnType<typeof rowsPayload>):Graph{
 const result=parseGraph(raw,previous.baseline_id);
 if(result.status!=='draft'||result.version!==previous.version||result.graph_revision!==previous.graph_revision+1||result.project_start!==payload.project_start||!result.plan)throw Error('invalid_result');
 const map=typeof raw==='object'&&raw!==null&&'client_ref_map'in raw?raw.client_ref_map:null;
 if(typeof map!=='object'||map===null||Array.isArray(map))throw Error('invalid_mapping');
 const mapping=map as Record<string,unknown>;const refs=payload.items.flatMap(i=>'client_ref'in i&&i.client_ref?[i.client_ref]:[]);
 if(Object.keys(mapping).sort().join()!==refs.sort().join())throw Error('invalid_mapping');
 if(Object.values(mapping).some(id=>previous.items.some(item=>item.id===id)))throw Error('reused_identity');
 const ids=payload.items.map(item=>('id'in item?item.id:mapping[item.client_ref!]));
 if(ids.some(id=>typeof id!=='number'||!Number.isSafeInteger(id)||id<1)||new Set(ids).size!==ids.length||result.items.length!==ids.length)throw Error('invalid_mapping');
 payload.items.forEach((item,index)=>{
  const row=result.items.find(r=>r.id===ids[index]);
  if(!row || row.title!==item.title || row.duration_days!==item.duration_days || row.is_milestone!==item.is_milestone || row.constraint_type!==item.constraint_type || row.constraint_date!==item.constraint_date || row.not_before_date!==item.not_before_date)throw Error('invalid_result');
  const expected=item.dependencies.map(d=>`${'predecessor_id'in d?d.predecessor_id:mapping[d.predecessor_ref!]}${d.link_type}${d.lag_days?`${d.lag_days>0?'+':''}${d.lag_days}d`:''}`).sort();
  if((row.predecessor_ids?.split(/[,;]/).map(v=>v.trim()).sort()??[]).join()!==expected.join())throw Error('invalid_dependencies');
 });
 return result;
}
