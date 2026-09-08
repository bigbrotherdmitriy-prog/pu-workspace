export type CashScope={project_id:number;contract_id:number|null;date_from:string;date_to:string};
type Money={inflow:string;outflow:string;net:string};
type Basis={planned:Money;actual:Money};
export type CashDetail={id:number;project_id:number;contract_id:number|null;record_version:number;title:string;counterparty:string|null;direction:string;status:string;review_status:string;planned_date:string|null;actual_date:string|null;planned_amount:string|null;actual_amount:string|null;plan_in_period:boolean;actual_in_period:boolean;exclusion_reasons:string[]};
export type CashViews={scope:CashScope;currency:'RUB';details:CashDetail[];months:(Basis&{month:string})[];calendar:(Basis&{date:string;planned_entry_ids:number[];actual_entry_ids:number[]})[];summary:Basis;excluded:Record<string,number>;decision_requirements:{code:string;decision_by:string;message:string}[]};
const obj=(v:unknown):v is Record<string,unknown>=>typeof v==='object'&&v!==null&&!Array.isArray(v);
const id=(v:unknown):v is number=>typeof v==='number'&&Number.isSafeInteger(v)&&v>0;
const nullableId=(v:unknown)=>v===null||id(v);
export function cents(v:unknown):bigint{if(typeof v!=='string'||! /^-?(0|[1-9]\d{0,29})\.\d{2}$/.test(v)||v==='-0.00')throw Error('invalid_money');return BigInt(v.replace('.',''));}
const date=(v:unknown):v is string=>typeof v==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(v)&&!v.startsWith('0000')&&Number.isFinite(Date.parse(v+'T00:00:00Z'))&&new Date(v+'T00:00:00Z').toISOString().slice(0,10)===v;
export function periodDays(scope:CashScope):string[]{
 if(!id(scope.project_id)||!nullableId(scope.contract_id)||!date(scope.date_from)||!date(scope.date_to))throw Error('invalid_scope');
 const start=Date.parse(scope.date_from+'T00:00:00Z'),end=Date.parse(scope.date_to+'T00:00:00Z');const count=(end-start)/86400000+1;
 if(count<1||count>366||!Number.isInteger(count))throw Error('invalid_period');
 return Array.from({length:count},(_,i)=>new Date(start+i*86400000).toISOString().slice(0,10));
}
const reasons=['proposed','cancelled','unsupported_status','invalid_actual','invalid_plan','invalid_direction','unconfirmed_plan'];
const zero=()=>({inflow:0n,outflow:0n,net:0n});type Sum=ReturnType<typeof zero>;
function money(raw:unknown):Money{if(!obj(raw)||Object.keys(raw).sort().join()!=='inflow,net,outflow')throw Error('invalid_totals');const a=cents(raw.inflow),b=cents(raw.outflow),n=cents(raw.net);if(a<0n||b<0n||a-b!==n)throw Error('invalid_totals');return raw as Money;}
function basis(raw:Record<string,unknown>):Basis{return {planned:money(raw.planned),actual:money(raw.actual)};}
function equal(value:Money,sum:Sum){if(cents(value.inflow)!==sum.inflow||cents(value.outflow)!==sum.outflow||cents(value.net)!==sum.net)throw Error('inconsistent_totals');}
function add(sum:Sum,other:Money){sum.inflow+=cents(other.inflow);sum.outflow+=cents(other.outflow);sum.net+=cents(other.net);}
export function parseCashFlowViews(raw:unknown,scope:CashScope):CashViews{
 const days=periodDays(scope),months=[...new Set(days.map(d=>d.slice(0,7)))];
 if(!obj(raw)||!obj(raw.scope)||Object.keys(scope).some(k=>raw.scope && (raw.scope as Record<string,unknown>)[k]!==scope[k as keyof CashScope])||raw.currency!=='RUB'||!Array.isArray(raw.details)||raw.details.length>5000||!Array.isArray(raw.calendar)||raw.calendar.length!==days.length||!Array.isArray(raw.months)||raw.months.length!==months.length||!obj(raw.summary)||!obj(raw.excluded)||!obj(raw.external_effects)||Object.keys(raw.external_effects).sort().join()!=='automatic_conversion,payment_created,posting_created'||Object.values(raw.external_effects).some(v=>v!==false))throw Error('invalid_views');
 const seen=new Set<number>();const counts:Record<string,number>=Object.fromEntries(reasons.map(r=>[r,0]));
 const details=raw.details.map((v):CashDetail=>{
  if(!obj(v)||!id(v.id)||seen.has(v.id)||v.project_id!==scope.project_id||!nullableId(v.contract_id)||(scope.contract_id!==null&&v.contract_id!==scope.contract_id)||!id(v.record_version)||typeof v.title!=='string'||!(v.counterparty===null||typeof v.counterparty==='string')||typeof v.direction!=='string'||typeof v.status!=='string'||typeof v.review_status!=='string'||!(v.planned_date===null||date(v.planned_date))||!(v.actual_date===null||date(v.actual_date))||typeof v.plan_in_period!=='boolean'||typeof v.actual_in_period!=='boolean'||!Array.isArray(v.exclusion_reasons)||v.exclusion_reasons.some(r=>typeof r!=='string'||!reasons.includes(r))||new Set(v.exclusion_reasons).size!==v.exclusion_reasons.length)throw Error('invalid_detail');
  for(const key of ['planned_amount','actual_amount'])if(v[key]!==null&&cents(v[key])<0n)throw Error('invalid_amount');
  const inPeriod=(d:unknown)=>typeof d==='string'&&d>=scope.date_from&&d<=scope.date_to;
  if(!inPeriod(v.planned_date)&&!inPeriod(v.actual_date))throw Error('outside_period');
  const active=['approved','paid','received'].includes(v.status),direction=['inflow','outflow'].includes(v.direction);
  const planValid=v.planned_date!==null&&v.planned_amount!==null;
  const fact=['paid','received'].includes(v.status),factValid=v.actual_date!==null&&v.actual_amount!==null&&cents(v.actual_amount)>0n&&v.review_status==='confirmed'&&v.status===(v.direction==='inflow'?'received':'paid');
  const expected:string[]=[];if(!active)expected.push(['proposed','cancelled'].includes(v.status)?v.status:'unsupported_status');if(!direction)expected.push('invalid_direction');if(active&&!planValid)expected.push('invalid_plan');if(active&&v.review_status!=='confirmed')expected.push('unconfirmed_plan');if(fact&&!factValid)expected.push('invalid_actual');
  if([...v.exclusion_reasons].sort().join()!==expected.sort().join()||v.plan_in_period!==(active&&v.review_status==='confirmed'&&direction&&planValid&&inPeriod(v.planned_date))||v.actual_in_period!==(fact&&direction&&factValid&&inPeriod(v.actual_date)))throw Error('invalid_basis');
  expected.forEach(r=>counts[r]++);seen.add(v.id);
  return v as unknown as CashDetail;
 });
 if(Object.keys(raw.excluded).sort().join()!==reasons.sort().join()||reasons.some(r=>raw.excluded && (raw.excluded as Record<string,unknown>)[r]!==counts[r]))throw Error('invalid_exclusions');
 const calendar=raw.calendar.map((v,index)=>{
  if(!obj(v)||v.date!==days[index])throw Error('invalid_calendar');const sums=basis(v);
  const result={date:days[index],...sums,planned_entry_ids:[] as number[],actual_entry_ids:[] as number[]};
  for(const kind of ['planned','actual'] as const){
   const list=v[`${kind}_entry_ids`];if(!Array.isArray(list)||list.some(i=>!id(i)||!seen.has(i))||new Set(list).size!==list.length)throw Error('invalid_calendar_ids');
   const rows=details.filter(r=>(kind==='planned'?r.plan_in_period:r.actual_in_period)&&(kind==='planned'?r.planned_date:r.actual_date)===v.date);
   if([...list].sort().join()!==rows.map(r=>r.id).sort().join())throw Error('missing_calendar_ids');
   const sum=zero();for(const row of rows){const amount=cents(kind==='planned'?row.planned_amount:row.actual_amount);sum[row.direction as 'inflow'|'outflow']+=amount;sum.net+=row.direction==='inflow'?amount:-amount;}
   equal(sums[kind],sum);result[`${kind}_entry_ids`]=[...list] as number[];
  }return result;
 });
 const monthViews=raw.months.map((v,index)=>{if(!obj(v)||v.month!==months[index])throw Error('invalid_month');const sums=basis(v);for(const kind of ['planned','actual'] as const){const sum=zero();calendar.filter(d=>d.date.startsWith(months[index])).forEach(d=>add(sum,d[kind]));equal(sums[kind],sum);}return {month:months[index],...sums};});
 const summary=basis(raw.summary);for(const kind of ['planned','actual'] as const){const sum=zero();calendar.forEach(d=>add(sum,d[kind]));equal(summary[kind],sum);}
 if(!Array.isArray(raw.decision_requirements)||raw.decision_requirements.some(v=>!obj(v)||typeof v.code!=='string'||!['OWNER','LEGAL'].includes(String(v.decision_by))||typeof v.message!=='string'))throw Error('invalid_decisions');
 return {scope:{...scope},currency:'RUB',details,calendar,months:monthViews,summary,excluded:counts,decision_requirements:raw.decision_requirements as CashViews['decision_requirements']};
}
