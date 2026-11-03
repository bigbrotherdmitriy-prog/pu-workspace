import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, csrfHeaders } from "../../api/client";
import { CustomerDecisionHistory } from "./CustomerDecisionHistory";
import "./customer.css";

type Project = { id: number; name: string };
type Act = { id: number; number: string; period_from: string; period_to: string; status: string; total_ex_vat: string; customer_status: string; customer_comment: string | null; decision_revision: number };
type EstimateRow = { id: number; name: string; total: string | null; done_amount: string; percent: number | null };
type Summary = { project: Project; estimates: EstimateRow[]; acts: Act[] };
type Entry = { work_date: string; work_name: string; unit: string | null; qty: string | null; location: string | null; object_name: string | null };

const money = (v: string | null | undefined) => (v == null ? "—" : Number(v).toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const dateRu = (v: string) => v.split("-").reverse().join(".");
const validRevision = (value: number) => Number.isInteger(value) && value >= 0 && value <= 2147483647;
const statusText: Record<string, string> = { pending: "Ждёт вашего решения", approved: "Согласован вами", rejected: "Отклонён вами" };

export function CustomerPortal({ projects }: { projects: Project[] }) {
  const [projectId, setProjectId] = useState(projects[0]?.id ?? 0);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [comments, setComments] = useState<Record<number, string>>({});
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);
  const pendingDecisions = useRef(new Set<string>());
  const [pendingKeys, setPendingKeys] = useState(new Set<string>());
  const liveProject = useRef(projectId);
  liveProject.current = projectId;
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);


  const reload = useCallback(() => setTick((v) => v + 1), []);

  useEffect(() => {
    if (!projectId) return;
    let active = true;
    setEntries([]);
    api<Summary>(`/customer/projects/${projectId}/summary`).then((value) => { if (active) setSummary(value); }).catch((e) => { if (active) setError(e instanceof Error ? e.message : "Не удалось загрузить проект"); });
    api<{ entries: Entry[] }>(`/customer/projects/${projectId}/progress`).then((r) => { if (active) setEntries(r?.entries ?? []); }).catch(() => undefined);
    return () => { active = false; };
  }, [projectId, tick]);

  async function decide(act: Act, decision: "approved" | "rejected") {
    const decisionProject = projectId;
    const key = `${decisionProject}:${act.id}`;
    if (pendingDecisions.current.has(key)) return;
    setError("");
    if (!validRevision(act.decision_revision)) {
      setError("Обновите акт: версия для принятия решения не получена.");
      return;
    }
    pendingDecisions.current.add(key);
    setPendingKeys(new Set(pendingDecisions.current));
    try {
      await api(`/customer/projects/${decisionProject}/ks-acts/${act.id}/decision`, {
        method: "POST", body: JSON.stringify({ decision, comment: comments[act.id] ?? "", expected_revision: act.decision_revision }),
      });
      if (!mounted.current || liveProject.current !== decisionProject) return;
      reload();
    } catch (e) {
      if (!mounted.current || liveProject.current !== decisionProject) return;
      if (e instanceof ApiError && e.status === 409) {
        setError("Акт изменился. Проверьте обновлённые данные и примите решение заново.");
        reload();
      } else {
        setError(e instanceof Error ? e.message : "Не удалось сохранить решение");
      }
    } finally {
      pendingDecisions.current.delete(key);
      if (mounted.current) setPendingKeys(new Set(pendingDecisions.current));
    }
  }

  async function download(act: Act, kind: "ks2.docx" | "ks3.docx", filename: string) {
    setError("");
    try {
      const response = await fetch(`/customer/projects/${projectId}/ks-acts/${act.id}/${kind}`, { credentials: "same-origin" });
      if (!response.ok) throw new Error(`Не удалось скачать (${response.status})`);
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось скачать");
    }
  }

  async function logout() {
    try { await fetch("/auth/logout", { method: "POST", credentials: "same-origin", headers: csrfHeaders() }); } finally { window.location.reload(); }
  }

  return (
    <div className="cust">
      <header className="cust-head">
        <h1 className="cust-title">Кабинет заказчика</h1>
        {projects.length > 1 && (
          <select aria-label="Проект" value={projectId} onChange={(e) => setProjectId(Number(e.target.value))}>
            {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        )}
        <button type="button" onClick={() => void logout()}>Выйти</button>
      </header>
      {error && <p className="cust-error" role="alert">{error}</p>}
      {summary && summary.project.id === projectId && (
        <>
          <h2 className="cust-sub">{summary.project.name}</h2>
          <section aria-label="Ход выполнения">
            <h3>Ход выполнения по смете</h3>
            {summary.estimates.length === 0 && <p className="cust-note">Смета пока не загружена.</p>}
            {summary.estimates.map((e) => (
              <div className="cust-card" key={e.id}>
                <b>{e.name}</b>
                <div className="cust-bar" role="img" aria-label={`Выполнено ${e.percent ?? 0}%`}><div className="cust-bar-fill" style={{ width: `${Math.min(100, e.percent ?? 0)}%` }} /></div>
                <span>Выполнено {e.percent ?? 0}% · {money(e.done_amount)} из {money(e.total)} ₽ без НДС</span>
              </div>
            ))}
          </section>
          <section aria-label="Акты на согласование">
            <h3>Акты КС-2 и КС-3</h3>
            {summary.acts.length === 0 && <p className="cust-note">Актов, направленных вам, пока нет.</p>}
            {summary.acts.map((a) => (
              <div className="cust-card" key={a.id}>
                <b>Акт № {a.number}</b>
                <span>{dateRu(a.period_from)} – {dateRu(a.period_to)} · {money(a.total_ex_vat)} ₽ без НДС</span>
                <span className={`cust-status cust-${a.customer_status}`}>{a.status === "signed" ? "Подписан" : statusText[a.customer_status] ?? a.customer_status}</span>
                {a.customer_comment && <span className="cust-note">Ваш комментарий: {a.customer_comment}</span>}
                <div className="cust-actions">
                  <button type="button" onClick={() => void download(a, "ks2.docx", `КС-2 № ${a.number}.docx`)}>КС-2 (Word)</button>
                  <button type="button" onClick={() => void download(a, "ks3.docx", `КС-3 № ${a.number}.docx`)}>КС-3 (Word)</button>
                </div>
                <CustomerDecisionHistory key={`${projectId}:${a.id}:${tick}`} projectId={projectId} actId={a.id} />
                {a.status === "sent" && !["approved", "rejected"].includes(a.customer_status) && (
                  <div className="cust-decide">
                    {!validRevision(a.decision_revision) && <p className="cust-note">Версия акта не получена. <button type="button" onClick={reload}>Обновить акт</button></p>}
                    <input aria-label={`Комментарий к акту ${a.number}`} placeholder="Комментарий (обязателен при отклонении)" value={comments[a.id] ?? ""} onChange={(e) => setComments((c) => ({ ...c, [a.id]: e.target.value }))} />
                    <button type="button" className="cust-main" disabled={!validRevision(a.decision_revision) || pendingKeys.has(`${projectId}:${a.id}`)} onClick={() => void decide(a, "approved")}>Согласовать</button>
                    <button type="button" disabled={!validRevision(a.decision_revision) || pendingKeys.has(`${projectId}:${a.id}`)} onClick={() => void decide(a, "rejected")}>Отклонить</button>
                  </div>
                )}
              </div>
            ))}
          </section>
          <section aria-label="Ход работ">
            <h3>Последние записи о выполненных работах</h3>
            {entries.length === 0 && <p className="cust-note">Записей пока нет.</p>}
            {entries.length > 0 && (
              <div className="cust-wrap"><table className="cust-table"><thead><tr><th>Дата</th><th>Работа</th><th>Объём</th><th>Место</th></tr></thead>
                <tbody>{entries.map((e, i) => <tr key={i}><td>{dateRu(e.work_date)}</td><td>{e.work_name}</td><td>{e.qty ? `${Number(e.qty).toLocaleString("ru-RU")} ${e.unit ?? ""}`.trim() : "—"}</td><td>{[e.object_name, e.location].filter(Boolean).join(", ") || "—"}</td></tr>)}</tbody></table></div>
            )}
          </section>
        </>
      )}
    </div>
  );
}
