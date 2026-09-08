import { useEffect, useRef, useState } from 'react';
import { constraints, type Constraint, type Graph } from './graphReadModel';
import { rowDraft, rowsPayload, rowsResponse, type Row } from './scheduleRows';
import type { GraphApi } from './ScheduleGraphEditor';
type Props={graph:Graph;api:GraphApi;disabled:boolean;onDirty:(dirty:boolean)=>void;onBusy:(busy:boolean)=>void;onSaved:(graph:Graph)=>void};
export function ScheduleRowsEditor({graph,api,disabled,onDirty,onBusy,onSaved}:Props){
 const [rows,setRows]=useState(()=>rowDraft(graph)); const [anchor,setAnchor]=useState(graph.project_start??'');
 const [error,setError]=useState('');const [blocked,setBlocked]=useState(false); const [busy,setBusy]=useState(false);
 const alive=useRef(true);const lock=useRef(false);const counter=useRef(0);
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
 const dirty=JSON.stringify(rows)!==JSON.stringify(rowDraft(graph))||anchor!==(graph.project_start??'');
 useEffect(()=>{onDirty(dirty||busy||blocked);},[dirty,busy,blocked,onDirty]);
 const editable=!disabled&&!busy&&graph.status==='draft';
 function change(key:string,patch:Partial<Row>){setRows(current=>current.map(r=>r.key===key?{...r,...patch}:r));}
 async function save(){
  if(!editable||blocked||lock.current)return;
  let payload;try{payload=rowsPayload(graph,rows,anchor);}catch{setError('Проверьте названия, даты и ссылки: удалённые этапы нельзя оставлять в зависимостях.');return;}
  lock.current=true;setBusy(true);onBusy(true);setError('');
  try{const raw=await api(`/execution/baselines/${graph.baseline_id}/graph/rows`,{method:'PUT',body:JSON.stringify(payload)});
   if(!alive.current)return;const result=rowsResponse(raw,graph,payload);onSaved(result);
  }catch{if(alive.current){setBlocked(true);setError('Сохранение не подтверждено. Правки сохранены локально, автоматический повтор запрещён. Удаление строк с финансовыми связями, источниками или фактом сервер блокирует.');}}
  finally{lock.current=false;if(alive.current){setBusy(false);onBusy(false);}}
 }
 return <section aria-label="Состав этапов ГПР"><h3>Добавление, название и удаление этапов</h3>
 <p>Изменения применяются только вместе кнопкой сохранения состава. Финансовые связи не удаляются: защищённую строку сервер отклонит.</p>
 {disabled&&<p>Сначала завершите редактирование параметров графа или обновление версии.</p>}
 {error&&<p role="alert">{error}</p>}
 <fieldset disabled={!editable}><legend>Черновик состава</legend>
 <label>Начало для состава<input type="date" value={anchor} onChange={e=>setAnchor(e.target.value)}/></label>
 {rows.map(row=><div key={row.key} role="group" aria-label={`Строка ${row.key}`}>
 <strong>{row.key}</strong>
 <label>Название {row.key}<input value={row.title} maxLength={500} onChange={e=>change(row.key,{title:e.target.value})}/></label>
 <label>Дней {row.key}<input type="number" min={row.milestone?0:1} max={10000} value={row.duration} disabled={row.milestone} onChange={e=>change(row.key,{duration:e.target.value})}/></label>
 <label>Веха {row.key}<input type="checkbox" checked={row.milestone} onChange={e=>change(row.key,{milestone:e.target.checked,duration:e.target.checked?'0':'1'})}/></label>
 <label>Связи {row.key}<input value={row.dependencies} maxLength={2000} placeholder="12FS; new1SS+2d" onChange={e=>change(row.key,{dependencies:e.target.value})}/></label>
 <label>Условие {row.key}<select value={row.constraint} onChange={e=>change(row.key,{constraint:e.target.value as Constraint,constraintDate:e.target.value==='asap'?'':row.constraintDate})}>{constraints.map(v=><option key={v}>{v}</option>)}</select></label>
 {row.constraint!=='asap'&&<label>Дата условия {row.key}<input type="date" value={row.constraintDate} onChange={e=>change(row.key,{constraintDate:e.target.value})}/></label>}
 <label>Не ранее {row.key}<input type="date" value={row.notBefore} onChange={e=>change(row.key,{notBefore:e.target.value})}/></label>
 <button type="button" onClick={()=>setRows(current=>current.filter(r=>r.key!==row.key))}>Пометить удаление {row.key}</button>
 </div>)}
 <button type="button" disabled={rows.length>=500} onClick={()=>{const key=`new${++counter.current}`;setRows(current=>[...current,{key,id:0,title:'Новый этап',duration:'1',milestone:false,dependencies:'',constraint:'asap',constraintDate:'',notBefore:''}]);}}>Добавить этап</button>
 </fieldset>
 <p>Новые локальные ссылки: new1FS, new2SS+2d. Удалённые строки: {graph.items.filter(i=>!rows.some(r=>r.key===String(i.id))).map(i=>`#${i.id}`).join(', ')||'нет'}. Зависимости не удаляются автоматически.</p>
 <button type="button" disabled={!editable||blocked||!dirty} onClick={()=>void save()}>Сохранить состав и рассчитать</button>
 <button type="button" disabled={busy} onClick={()=>{setRows(rowDraft(graph));setAnchor(graph.project_start??'');setError('');if(blocked)setError('Локальные правки отброшены. Обновите серверную версию перед дальнейшим сохранением.');onDirty(blocked);}}>Отбросить изменения состава</button>
 </section>;
}
