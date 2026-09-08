import {useEffect,useRef,useState} from 'react';
import {api as existingApi} from '../../api/client';
import {parseCashFlowViews,periodDays,type CashViews,type CashScope} from './cashFlowViewsModel';
type Props={projectId:number;contractId:number|null;api?:(path:string,options?:RequestInit)=>Promise<unknown>};
const reasonLabels:Record<string,string>={proposed:'Предложение — не подтверждено',cancelled:'Отменено',unsupported_status:'Неподдерживаемый статус',invalid_actual:'Факт требует проверки',invalid_plan:'Некорректные данные плана',invalid_direction:'Неизвестное направление',unconfirmed_plan:'План не прошёл ручное подтверждение'};
const statusLabels:Record<string,string>={proposed:'Предложение',approved:'План утверждён',paid:'Оплачено',received:'Получено',cancelled:'Отменено'};
const directionLabels:Record<string,string>={inflow:'Поступление',outflow:'Выплата'};
const label=(map:Record<string,string>,value:string)=>map[value]??`Неизвестное значение (${value})`;
export function CashFlowViews(props:Props){
 if(!Number.isSafeInteger(props.projectId)||props.projectId<1||!(props.contractId===null||Number.isSafeInteger(props.contractId)&&props.contractId>0))return <p role="status">Выберите область ДДС.</p>;
 return <Session key={`${props.projectId}:${props.contractId}`} {...props}/>;
}
function Session({projectId,contractId,api=existingApi}:Props){
 const [from,setFrom]=useState(''),[to,setTo]=useState('');const [view,setView]=useState<'details'|'months'|'calendar'|'summary'>('details');
 const [data,setData]=useState<CashViews|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const sequence=useRef(0),alive=useRef(true);const controller=useRef<AbortController|null>(null);
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;sequence.current++;controller.current?.abort();};},[]);
 function change(set:(s:string)=>void,value:string){sequence.current++;controller.current?.abort();set(value);setData(null);setBusy(false);setError('');}
 async function load(){
  const scope:CashScope={project_id:projectId,contract_id:contractId,date_from:from,date_to:to};
  try{periodDays(scope);}catch{setError('Выберите корректный период до 366 дней включительно.');return;}
  const ticket=++sequence.current;controller.current?.abort();controller.current=new AbortController();setBusy(true);setData(null);setError('');
  const query=new URLSearchParams({project_id:String(projectId),date_from:from,date_to:to});if(contractId!==null)query.set('contract_id',String(contractId));
  try{const result=await api(`/execution/cash-flow/views?${query}`,{signal:controller.current.signal});
   if(alive.current&&ticket===sequence.current)setData(parseCashFlowViews(result,scope));
  }catch{if(alive.current&&ticket===sequence.current)setError('ДДС недоступен или результаты не согласованы. Суммы скрыты; уточните период или доступ и обновите явно.');}
  finally{if(alive.current&&ticket===sequence.current)setBusy(false);}
 }
 const names={details:'Детали',months:'Месяцы',calendar:'Календарь',summary:'Сводка'};
 return <section className="card" aria-label="Согласованные представления ДДС" aria-busy={busy}>
 <h2>ДДС — один период и область</h2><p>Проект #{projectId} · {contractId===null?'все договоры':`договор #${contractId}`}. Подтверждённый план и факт сравниваются отдельно, не складываются с предложениями или прогнозом.</p>
 <p>Начальный банковский остаток неизвестен, не принят за ноль. Сальдо ниже — только поступления минус выплаты периода, не остаток счёта.</p>
 <label>ДДС с<input type="date" value={from} onChange={e=>change(setFrom,e.target.value)}/></label>
 <label>ДДС по<input type="date" value={to} onChange={e=>change(setTo,e.target.value)}/></label>
 <button type="button" disabled={busy} onClick={()=>void load()}>Загрузить период ДДС</button>
 {busy&&<p role="status">Загрузка согласованного снимка…</p>}{error&&<p role="alert">{error}</p>}
 {data&&<><p>Снимок: {data.scope.date_from} — {data.scope.date_to}; валюта RUB. Изменения после загрузки появятся только после обновления.</p>
 <nav aria-label="Вид ДДС">{(Object.keys(names) as (keyof typeof names)[]).map(key=><button type="button" key={key} aria-pressed={view===key} onClick={()=>setView(key)}>{names[key]}</button>)}</nav>
 {view==='details'?<table><caption>Детали периода</caption><thead><tr><th>Запись</th><th>Направление / статус</th><th>План: дата и сумма RUB</th><th>Факт: дата и сумма RUB</th><th>Исключения</th></tr></thead><tbody>{data.details.map(row=><tr key={row.id}><th scope="row">#{row.id} {row.title}<small>{row.counterparty??'Контрагент не указан'}</small></th><td>{label(directionLabels,row.direction)} / {label(statusLabels,row.status)}</td><td>{row.planned_date??'нет даты'} · {row.planned_amount??'некорректная сумма'} · {row.plan_in_period?'включён':'не включён'}</td><td>{row.actual_date??'нет даты'} · {row.actual_amount??'некорректная сумма'} · {row.actual_in_period?'включён':'не включён'}</td><td>{row.exclusion_reasons.map(r=>label(reasonLabels,r)).join(', ')||'нет'}</td></tr>)}</tbody></table>
 :<table><caption>{names[view]} — план и факт отдельно, RUB</caption><thead><tr><th>Период</th><th>План поступления</th><th>План выплаты</th><th>Сальдо плана</th><th>Факт поступления</th><th>Факт выплаты</th><th>Сальдо факта</th></tr></thead><tbody>{(view==='summary'?[{label:'Весь период',...data.summary}]:view==='months'?data.months.map(v=>({...v,label:v.month})):data.calendar.map(v=>({...v,label:v.date}))).map(row=><tr key={row.label}><th scope="row">{row.label}</th>{[row.planned.inflow,row.planned.outflow,row.planned.net,row.actual.inflow,row.actual.outflow,row.actual.net].map((amount,index)=><td key={index}>{amount}</td>)}</tr>)}</tbody></table>}
 <p>Исключённые признаки (могут пересекаться): {Object.entries(data.excluded).map(([key,n])=>`${label(reasonLabels,key)}: ${n}`).join('; ')}.</p>
 {data.decision_requirements.map((d,index)=><p key={index}>{d.decision_by==='OWNER'?'Решение владельца':'Юридическая проверка'}: {d.message}</p>)}
 <p>Представления только для чтения: платежи, проводки и конвертация не выполняются.</p></>}
 </section>;
}
