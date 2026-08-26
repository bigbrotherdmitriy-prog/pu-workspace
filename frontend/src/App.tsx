import { useEffect, useState } from "react";
import { AlertTriangle, Bot, CalendarDays, ChevronLeft, FileText, FolderKanban, FolderTree, LayoutDashboard, ListTodo, LogOut, Menu, RefreshCw, Search, Settings, ShieldCheck } from "lucide-react";

type Project = { id: number; name: string };
type Summary = { attention:number; open_tasks:number; overdue_tasks:number; open_risks:number; pending_decisions:number; drafts:number; documents:number };
type DocumentCard = { document_id?:number; name:string; tasks:number; risks:number; decisions:number; drafts:number; attention:number };
type Snapshot = { id:number; status:string; item_count:number; source_folder:string; source_external_id:string; created_at:string; completed_at?:string };

const items = [
  [LayoutDashboard,"Рабочий центр"], [FolderKanban,"Проекты"], [FileText,"Документы"],
  [ListTodo,"Задачи"], [AlertTriangle,"Риски и решения"], [Bot,"AI Secretary"],
  [CalendarDays,"Интеграции"], [ShieldCheck,"Журнал"], [Settings,"Настройки"],
] as const;

async function api(path:string, options:RequestInit={}) {
  const token=sessionStorage.getItem("pu_token");
  const response=await fetch(path,{...options,headers:{"Content-Type":"application/json",...(token?{Authorization:`Bearer ${token}`}:{})}});
  const body=await response.json().catch(()=>({}));
  if(!response.ok) throw new Error(body.detail||`HTTP ${response.status}`);
  return body;
}

function Login({onDone}:{onDone:()=>void}){
  const [email,setEmail]=useState(""); const [password,setPassword]=useState(""); const [error,setError]=useState("");
  async function submit(){try{const d=await api("/auth/login",{method:"POST",body:JSON.stringify({email,password})});sessionStorage.setItem("pu_token",d.access_token);onDone()}catch(e){setError((e as Error).message)}}
  return <div className="login-page"><div className="login-card"><div className="brand-mark">PU</div><h1>Вход в PU Workspace</h1><p>Единое рабочее пространство проектов и документов</p><label>Email<input value={email} onChange={e=>setEmail(e.target.value)} type="email"/></label><label>Пароль<input value={password} onChange={e=>setPassword(e.target.value)} type="password" onKeyDown={e=>e.key==="Enter"&&submit()}/></label><button onClick={submit}>Войти</button>{error&&<div className="error">{error}</div>}<a href="/">Открыть прежний интерфейс</a></div></div>
}

export function App(){
  const [ready,setReady]=useState(false),[collapsed,setCollapsed]=useState(false),[mobile,setMobile]=useState(false);
  const [projects,setProjects]=useState<Project[]>([]),[projectId,setProjectId]=useState(0),[summary,setSummary]=useState<Summary|null>(null),[documents,setDocuments]=useState<DocumentCard[]>([]),[snapshots,setSnapshots]=useState<Snapshot[]>([]),[error,setError]=useState("");
  async function load(){try{setError("");const p=await api("/projects/");setProjects(p.projects);const id=projectId||p.projects[0]?.id||0;if(id){setProjectId(id);const [d,s]=await Promise.all([api(`/dashboard/project?project_id=${id}`),api(`/projects/${id}/snapshots`)]);setSummary(d.summary);setDocuments(d.documents);setSnapshots(s.snapshots)}}catch(e){setError((e as Error).message)}}
  useEffect(()=>{api("/auth/me").then(()=>setReady(true)).catch(()=>setReady(false))},[]);
  useEffect(()=>{if(ready)load()},[ready,projectId]);
  if(!ready)return <Login onDone={()=>setReady(true)}/>;
  const metrics=[["Требуют внимания",summary?.attention||0,"warn"],["Открытые задачи",summary?.open_tasks||0,""],["Просрочено",summary?.overdue_tasks||0,"danger"],["Риски",summary?.open_risks||0,"warn"],["Ждут решения",summary?.pending_decisions||0,""],["Документы",summary?.documents||0,""]];
  const latestSnapshot=snapshots[0];
  return <div className="shell">
    <aside className={`${collapsed?"collapsed":""} ${mobile?"mobile-open":""}`}><div className="sidebar-head"><div className="brand-mark">PU</div>{!collapsed&&<strong>PU Workspace</strong>}<button className="icon" onClick={()=>setCollapsed(!collapsed)}><ChevronLeft/></button></div><nav>{items.map(([Icon,label],i)=><button className={i===0?"active":""} key={label} title={label}><Icon/><span>{label}</span></button>)}</nav><div className="profile"><div className="avatar">D</div>{!collapsed&&<div><strong>Администратор</strong><small>Владелец</small></div>}<button className="icon" onClick={()=>{sessionStorage.removeItem("pu_token");setReady(false)}}><LogOut/></button></div></aside>
    <main><header><button className="mobile-menu icon" onClick={()=>setMobile(!mobile)}><Menu/></button><div><h1>Рабочий центр</h1><p>Главное по проекту на сегодня</p></div><div className="header-actions"><div className="search"><Search/><input placeholder="Поиск по проекту"/></div><select value={projectId} onChange={e=>setProjectId(Number(e.target.value))}>{projects.map(p=><option value={p.id} key={p.id}>{p.name}</option>)}</select><button className="icon" onClick={load}><RefreshCw/></button></div></header>
      <section className="content">{error&&<div className="error">{error}</div>}<div className="metrics">{metrics.map(([label,value,tone])=><article className={String(tone)} key={String(label)}><span>{label}</span><strong>{value}</strong></article>)}</div>
      <div className="grid"><section className="card span-2"><div className="card-head"><div><h2>Что требует внимания</h2><p>Задачи, риски и решения из подтверждённых источников</p></div><button>Открыть реестр</button></div>{summary?.attention?<div className="attention"><AlertTriangle/><div><strong>{summary.attention} пунктов требуют проверки</strong><p>Просрочено задач: {summary.overdue_tasks}; открытых рисков: {summary.open_risks}; решений: {summary.pending_decisions}.</p></div></div>:<div className="empty"><ShieldCheck/><p>Критичных пунктов нет</p></div>}</section>
      <section className="card"><div className="card-head"><div><h2>Быстрые действия</h2><p>Без изменения оригиналов</p></div></div><div className="quick"><a href="/#folderId">Выбрать папку Drive</a><a href="/#chooseLocalFolder">Загрузить папку</a><a href="/#tasksPanel">Открыть задачи</a></div></section>
      {latestSnapshot&&<section className="card span-3 source-card"><div className="source-icon"><FolderTree/></div><div><span className="eyebrow">РАБОЧИЙ ИСТОЧНИК</span><h2>{latestSnapshot.source_folder}</h2><p>Виртуальный снимок №{latestSnapshot.id} · {latestSnapshot.item_count.toLocaleString("ru-RU")} объектов · оригиналы не изменяются</p></div><span className={`source-status ${latestSnapshot.status}`}>{latestSnapshot.status==="ready"?"Снимок готов":latestSnapshot.status}</span><a className="source-link" href={`https://drive.google.com/drive/folders/${latestSnapshot.source_external_id}`} target="_blank" rel="noreferrer">Открыть в Google Drive</a></section>}
      <section className="card span-3"><div className="card-head"><div><h2>Последние документы</h2><p>Связанные задачи, риски и решения</p></div><button>Все документы</button></div><div className="doc-list">{documents.slice(0,8).map(d=><article key={d.name}><div className="file-icon"><FileText/></div><div><strong>{d.name}</strong><p>Задач {d.tasks} · рисков {d.risks} · решений {d.decisions}</p></div>{d.attention>0&&<span className="pill">Внимание: {d.attention}</span>}</article>)}</div></section></div></section>
    </main>
  </div>
}
