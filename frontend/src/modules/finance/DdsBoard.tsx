import { useEffect, useMemo, useState } from "react";
import { api } from "../../api/client";
import { formatMoney } from "../../utils/numberFormat";
import type { FinanceOverview } from "./types";
import "./dds-board.css";

// Owner 21.10.2026: the uploaded DDS is the constant baseline. One screen: balance and
// critical point from the plan, articles plan vs paid, what is left to pay/receive,
// additional costs with an editable short description. Paid invoices are not listed.

type MonthRow = { month: string; income_plan: string; income_fact: string; expense_plan: string; expense_fact: string; cumulative_plan: string; cumulative_fact: string };
type ArticleInvoice = { entry_id: number; title: string; counterparty: string | null; invoiced: string; paid: string; planned_date: string | null; status: string };
type Article = { budget_line_id: number; title: string; category: string; plan: string; invoiced: string; paid: string; remaining: string; invoices: ArticleInvoice[] };
type AdditionalItem = { entry_id?: number; note?: string | null; title: string; counterparty: string | null; planned_date: string | null; actual_date: string | null; fact: string; invoiced?: string };
type Response = {
  currency: string;
  income: { plan: string; fact: string };
  expense_total: { plan: string; fact: string; invoiced?: string };
  expense_by_article?: Article[];
  additional_costs?: { plan: string; fact: string; invoiced?: string; items?: AdditionalItem[] } | null;
  by_month: MonthRow[];
};

type CashRow = FinanceOverview["cash_flow"][number];
type Stage = { stage_number: string | number; object_name: string | null; amount: string; planned_finish: string | null };
type ReceiptDraft = { id: number; amount: string; date: string };

// Plan DDS receipts are monthly rows («Этапы Дубна»); name the contract stage(s) behind them.
function stageLabel(row: CashRow, left: number, stages: Stage[]): string | null {
  const text = `${row.title} ${row.object_name || ""}`.toLocaleLowerCase("ru-RU");
  const own = stages.filter((s) => s.object_name && text.includes(s.object_name.toLocaleLowerCase("ru-RU")));
  const object = own[0]?.object_name;
  const nice = object ? object.charAt(0) + object.slice(1).toLocaleLowerCase("ru-RU") : "";
  const exact = own.find((s) => Math.abs(n(s.amount) - left) < 0.01);
  if (exact) return `${exact.stage_number}-й этап${nice ? ` · ${nice}` : ""}`;
  const month = (row.planned_date || "").slice(0, 7);
  const inMonth = own.filter((s) => (s.planned_finish || "").slice(0, 7) === month);
  if (inMonth.length) return `этапы ${inMonth.map((s) => s.stage_number).join(", ")}${nice ? ` · ${nice}` : ""}`;
  return null;
}

type Props = { projectId: number; contractId?: number | null; currency: string; cashFlow?: CashRow[]; onChanged?: () => void };

const n = (value: string | number | null | undefined) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
};
const monthFormat = new Intl.DateTimeFormat("ru-RU", { month: "long", year: "numeric", timeZone: "UTC" });
const shortMonth = new Intl.DateTimeFormat("ru-RU", { month: "short", timeZone: "UTC" });
const dayFormat = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit", year: "2-digit", timeZone: "UTC" });
const monthLabel = (key: string) => monthFormat.format(new Date(`${key}-01T00:00:00Z`)).replace(" г.", "");
const day = (value: string | null | undefined) => (value ? dayFormat.format(new Date(`${value}T00:00:00Z`)) : "—");
const today = () => new Date().toISOString().slice(0, 10);

function BalanceChart({ months, currentKey }: { months: MonthRow[]; currentKey: string }) {
  const width = 1200, height = 240, left = 8, right = 8, top = 22, bottom = 26;
  const plan = months.map((m) => n(m.cumulative_plan));
  const factIdx = months.map((m, i) => (m.month <= currentKey ? i : -1)).filter((i) => i >= 0);
  const fact = factIdx.map((i) => n(months[i].cumulative_fact));
  const all = [...plan, ...fact, 0];
  const max = Math.max(...all), min = Math.min(...all);
  const span = max - min || 1;
  const step = months.length > 1 ? (width - left - right) / (months.length - 1) : 0;
  const x = (i: number) => left + i * step;
  const y = (v: number) => top + (max - v) / span * (height - top - bottom);
  const line = (values: number[], idx: number[]) => values.map((v, k) => `${k ? "L" : "M"} ${x(idx[k]).toFixed(1)} ${y(v).toFixed(1)}`).join(" ");
  const planIdx = plan.map((_, i) => i);
  const zero = y(0);
  const minIdx = plan.indexOf(Math.min(...plan));
  const area = `${line(plan.map((v) => Math.min(v, 0)), planIdx)} L ${x(plan.length - 1)} ${zero} L ${x(0)} ${zero} Z`;
  const labelEvery = Math.max(1, Math.ceil(months.length / 12));
  return (
    <svg className="dds-board-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="График баланса по ДДС">
      <line x1={left} x2={width - right} y1={zero} y2={zero} className="dds-board-zero" />
      {plan.some((v) => v < 0) && <path d={area} className="dds-board-gap" />}
      <path d={line(plan, planIdx)} className="dds-board-plan" />
      {fact.length > 0 && <path d={line(fact, factIdx)} className="dds-board-fact" />}
      {minIdx >= 0 && <g>
        <circle cx={x(minIdx)} cy={y(plan[minIdx])} r={5} className={plan[minIdx] < 0 ? "dds-board-point negative" : "dds-board-point"} />
        <text x={Math.min(Math.max(x(minIdx), 60), width - 60)} y={Math.max(y(plan[minIdx]) - 10, 12)} textAnchor="middle" className="dds-board-point-label">{formatMoney(Math.round(plan[minIdx]))}</text>
      </g>}
      {months.map((m, i) => (i % labelEvery === 0 ? <text key={m.month} x={x(i)} y={height - 6} textAnchor="middle" className="dds-board-axis">{`${shortMonth.format(new Date(`${m.month}-01T00:00:00Z`)).replace(".", "")} ${m.month.slice(2, 4)}`}</text> : null))}
    </svg>
  );
}

const FOLD_KEY = "pu-dds-board-folded";
const DEFAULT_FOLDED: Record<string, boolean> = { articles: true, received: true, extra: true };

export function DdsBoard({ projectId, contractId, currency, cashFlow = [], onChanged }: Props) {
  const [data, setData] = useState<Response | null>(null);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [busy, setBusy] = useState<number | null>(null);
  const [reload, setReload] = useState(0);
  const [notes, setNotes] = useState<Record<number, string>>({});
  // Owner 21.10.2026: every block folds so the page is not one long scroll; long lists start folded.
  const [folded, setFolded] = useState<Record<string, boolean>>(() => {
    try { return JSON.parse(localStorage.getItem(FOLD_KEY) || "null") || DEFAULT_FOLDED; } catch { return DEFAULT_FOLDED; }
  });
  const toggleFold = (key: string) => setFolded((current) => {
    const next = { ...current, [key]: !current[key] };
    try { localStorage.setItem(FOLD_KEY, JSON.stringify(next)); } catch { /* per-viewer convenience only */ }
    return next;
  });
  const cardClass = (key: string) => `dds-board-card${folded[key] ? " folded" : ""}`;
  const foldTitle = (key: string, text: string) => <h3><button type="button" className="dds-board-toggle" aria-expanded={!folded[key]} onClick={() => toggleFold(key)}>{folded[key] ? "▸" : "▾"} {text}</button></h3>;
  const [stages, setStages] = useState<Stage[]>([]);
  const [draft, setDraft] = useState<ReceiptDraft | null>(null);

  useEffect(() => {
    if (projectId <= 0 || !contractId) { setStages([]); return; }
    let alive = true;
    api<{ items?: Stage[] }>(`/projects/${projectId}/contracts/${contractId}/stage-register`)
      .then((res) => { if (alive) setStages(Array.isArray(res?.items) ? res.items : []); })
      .catch(() => { if (alive) setStages([]); });
    return () => { alive = false; };
  }, [projectId, contractId, reload]);

  useEffect(() => {
    if (projectId <= 0) return;
    const controller = new AbortController();
    const query = contractId ? `?contract_id=${contractId}` : "";
    api<Response>(`/projects/${projectId}/budget-from-dds${query}`, { signal: controller.signal })
      .then((response) => {
        // An unexpected payload must not take the DDS screen down.
        if (response && response.income && response.expense_total) { setData(response); setError(""); }
        else setError("ДДС пока не загружен — загрузите плановый ДДС");
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setError(err instanceof Error ? err.message : "Не удалось загрузить ДДС");
      });
    return () => controller.abort();
  }, [projectId, contractId, reload]);

  const currentKey = today().slice(0, 7);

  // Plan inflow lines still waiting for money: plan minus facts recorded against the line.
  const receipts = useMemo(() => {
    const factByPlan = new Map<number, number>();
    cashFlow.forEach((row) => {
      if (row.plan_entry_id && row.actual_date) factByPlan.set(row.plan_entry_id, (factByPlan.get(row.plan_entry_id) || 0) + n(row.actual_amount));
    });
    // Our acts / invoices / КС-3 to the customer that are not paid yet (owner 21.10.2026).
    const docs = cashFlow.filter((row) => row.direction === "inflow" && n(row.invoiced_amount) > 0 && n(row.planned_amount) === 0
      && !["cancelled", "deleted", "rejected", "received", "paid"].includes(row.status));
    const docsByPlan = new Map<number, number>();
    docs.forEach((row) => { if (row.plan_entry_id) docsByPlan.set(row.plan_entry_id, (docsByPlan.get(row.plan_entry_id) || 0) + n(row.invoiced_amount)); });
    const plan = cashFlow
      .filter((row) => row.direction === "inflow" && n(row.planned_amount) > 0 && !row.plan_entry_id && !["cancelled", "deleted", "rejected", "received", "paid"].includes(row.status))
      .map((row) => ({ row, left: n(row.planned_amount) - (factByPlan.get(row.id) || 0) - (docsByPlan.get(row.id) || 0), planId: row.id, doc: false }));
    return [...docs.map((row) => ({ row, left: n(row.invoiced_amount), planId: row.plan_entry_id || 0, doc: true })), ...plan]
      .filter((item) => item.left > 0.009)
      .sort((a, b) => Number(b.doc) - Number(a.doc) || (a.row.planned_date || "").localeCompare(b.row.planned_date || ""));
  }, [cashFlow]);

  // Money already received from the customer, newest first.
  const received = cashFlow
    .filter((row) => row.direction === "inflow" && row.actual_date && n(row.actual_amount) > 0 && !["cancelled", "deleted", "rejected"].includes(row.status))
    .sort((a, b) => (b.actual_date || "").localeCompare(a.actual_date || ""));

  if (error) return <p className="dds-board-status" role="alert">{error}</p>;
  if (!data) return <p className="dds-board-status">Загружаем ДДС…</p>;

  const months = [...(data.by_month || [])].sort((a, b) => a.month.localeCompare(b.month));
  const critical = months.reduce<MonthRow | null>((acc, m) => (!acc || n(m.cumulative_plan) < n(acc.cumulative_plan) ? m : acc), null);
  const last = months[months.length - 1];
  const gaps = months.filter((m) => n(m.cumulative_plan) < 0);
  const articles = data.expense_by_article ?? [];
  const extra = data.additional_costs?.items ?? [];
  // Owner 22.10.2026: additional-cost invoices are paid from the same «К оплате» list.
  const unpaid = [
    ...articles.flatMap((article) => (article.invoices || [])
      .filter((inv) => n(inv.invoiced) - n(inv.paid) > 0.009)
      .map((inv) => ({ entry_id: inv.entry_id, title: inv.title, counterparty: inv.counterparty, planned_date: inv.planned_date, article: article.title, left: n(inv.invoiced) - n(inv.paid) }))),
    ...extra.filter((item) => item.entry_id && n(item.invoiced) - n(item.fact) > 0.009)
      .map((item) => ({ entry_id: item.entry_id as number, title: item.title, counterparty: item.counterparty, planned_date: item.planned_date, article: "Дополнительные затраты", left: n(item.invoiced) - n(item.fact) })),
  ];
  const cur = currency || data.currency;

  const refresh = () => { setReload((v) => v + 1); onChanged?.(); };

  async function act(id: number, path: string, body: object): Promise<boolean> {
    setBusy(id);
    setActionError("");
    try {
      await api(path, { method: "POST", body: JSON.stringify(body) });
      refresh();
      return true;
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Не удалось сохранить");
      return false;
    } finally {
      setBusy(null);
    }
  }

  async function saveNote(item: AdditionalItem) {
    if (!item.entry_id) return;
    const value = (notes[item.entry_id] ?? "").trim();
    if (!value || value === (item.note ?? item.title)) return;
    setActionError("");
    try {
      await api(`/projects/${projectId}/cash-flow/${item.entry_id}/note`, { method: "PATCH", body: JSON.stringify({ note: value }) });
      setReload((v) => v + 1);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Не удалось сохранить описание");
    }
  }

  return (
    <div className="dds-board">
      {actionError && <p className="dds-board-status dds-board-error" role="alert">{actionError}</p>}
      <section className="dds-board-kpis" aria-label="Итоги ДДС">
        <article><span>Приходы</span><strong>{formatMoney(n(data.income.fact))}</strong><small>из {formatMoney(n(data.income.plan))} {cur} по плану</small></article>
        <article><span>Расходы</span><strong>{formatMoney(n(data.expense_total.fact))}</strong><small>из {formatMoney(n(data.expense_total.plan))} {cur} по плану</small></article>
        <article><span>Баланс на конец</span><strong>{formatMoney(n(last?.cumulative_plan))}</strong><small>сейчас по факту {formatMoney(n(data.income.fact) - n(data.expense_total.fact))}</small></article>
        <article className={critical && n(critical.cumulative_plan) < 0 ? "alert" : "ok"}>
          <span>Критическая точка</span>
          <strong>{critical ? formatMoney(n(critical.cumulative_plan)) : "—"}</strong>
          <small>{critical ? `${monthLabel(critical.month)} · ${n(critical.cumulative_plan) < 0 ? "кассовый разрыв" : "разрыва нет"}` : "нет данных"}</small>
        </article>
      </section>

      {months.length > 0 && <section className={cardClass("balance")} aria-label="Баланс по ДДС">
        <div className="dds-board-card-head">{foldTitle("balance", "Баланс по начальному ДДС")}<span className="dds-board-legend"><i className="plan" /> план <i className="fact" /> факт</span></div>
        <BalanceChart months={months} currentKey={currentKey} />
        {gaps.length > 0 && <ul className="dds-board-gaps">{gaps.map((m) => <li key={m.month}>{monthLabel(m.month)} — {formatMoney(n(m.cumulative_plan))}</li>)}</ul>}
      </section>}

      <section className={cardClass("articles")} aria-label="Статьи ДДС">
        <div className="dds-board-card-head">{foldTitle("articles", "Статьи расходов")}<span>план · оплачено · остаток</span></div>
        <div className="dds-board-articles">
          {articles.map((a) => {
            const plan = n(a.plan), paid = n(a.paid), over = paid > plan;
            const pct = plan > 0 ? Math.min(100, paid / plan * 100) : paid > 0 ? 100 : 0;
            return <div className="dds-board-article" key={a.budget_line_id}>
              <div className="dds-board-article-head">
                <div><strong>{a.title}</strong><small>{a.category}</small></div>
                <dl><div><dt>План</dt><dd>{formatMoney(plan)}</dd></div><div><dt>Оплачено</dt><dd>{formatMoney(paid)}</dd></div><div><dt>{over ? "Перерасход" : "Остаток"}</dt><dd className={over ? "negative" : ""}>{formatMoney(Math.abs(plan - paid))}</dd></div></dl>
              </div>
              <div className="dds-board-bar" aria-hidden="true"><span className={over ? "over" : ""} style={{ width: `${pct}%` }} /></div>
            </div>;
          })}
          {!articles.length && <p className="dds-board-empty">Загрузите плановый ДДС — статьи появятся здесь.</p>}
        </div>
      </section>

      <div className="dds-board-columns">
        <section className={cardClass("topay")} aria-label="К оплате">
          <div className="dds-board-card-head">{foldTitle("topay", "К оплате")}<span>{unpaid.length ? formatMoney(unpaid.reduce((s, i) => s + i.left, 0)) : ""}</span></div>
          {unpaid.length ? <ul className="dds-board-list">{unpaid.map((inv) => <li key={inv.entry_id}>
            <div><strong>{inv.counterparty || inv.title}</strong><small>{inv.article} · {day(inv.planned_date)}</small></div>
            <b>{formatMoney(inv.left)}</b>
            <button type="button" disabled={busy === inv.entry_id} onClick={() => void act(inv.entry_id, `/projects/${projectId}/cash-flow/${inv.entry_id}/record-payment`, { amount: inv.left.toFixed(2), actual_date: today() })}>Оплачен</button>
          </li>)}</ul> : <p className="dds-board-empty">Неоплаченных счетов нет</p>}
        </section>

        <section className={cardClass("expected")} aria-label="Ожидаемые приходы">
          <div className="dds-board-card-head">{foldTitle("expected", "Ожидаемые приходы")}<span>{receipts.length ? formatMoney(receipts.reduce((s, i) => s + i.left, 0)) : ""}</span></div>
          {receipts.length ? <ul className="dds-board-list">{receipts.map(({ row, left, planId, doc }) => <li key={row.id} className={row.planned_date && row.planned_date < today() ? "late" : ""}>
            <div><strong>{doc ? row.title : stageLabel(row, left, stages) || row.title}</strong><small>{doc ? `документ · ${row.counterparty || "заказчик"} · ` : stageLabel(row, left, stages) ? `${row.title} · ` : "по плану · "}срок {day(row.planned_date)}</small></div>
            <b>{formatMoney(left)}</b>
            {draft?.id === row.id
              ? <span className="dds-board-receipt-form">
                  <input id={`dds-receipt-amount-${row.id}`} aria-label="Сумма поступления" inputMode="decimal" value={draft.amount} onChange={(e) => setDraft({ ...draft, amount: e.target.value })} />
                  <input id={`dds-receipt-date-${row.id}`} aria-label="Дата поступления" type="date" value={draft.date} onChange={(e) => setDraft({ ...draft, date: e.target.value })} />
                  <button type="button" disabled={busy === row.id || !draft.date || !(Number(draft.amount.replace(",", ".")) > 0)}
                    onClick={() => void act(row.id, `/projects/${projectId}/cash-flow/${planId}/record-fact`, { amount: Number(draft.amount.replace(/\s/g, "").replace(",", ".")).toFixed(2), actual_date: draft.date, ...(doc ? { basis_entry_id: row.id } : {}) }).then((ok) => { if (ok) setDraft(null); })}>Сохранить</button>
                  <button type="button" className="secondary" onClick={() => setDraft(null)}>Отмена</button>
                </span>
              : <button type="button" disabled={busy === row.id || !planId} title={planId ? undefined : "Нет строки прихода в плановом ДДС"} onClick={() => setDraft({ id: row.id, amount: left.toFixed(2), date: today() })}>Получено</button>}
          </li>)}</ul> : <p className="dds-board-empty">Все плановые приходы получены</p>}
        </section>
      </div>

      <section className={cardClass("received")} aria-label="Поступило от заказчика">
        <div className="dds-board-card-head">{foldTitle("received", "Поступило от заказчика")}<span>получено {formatMoney(n(data.income.fact))} из {formatMoney(n(data.income.plan))} по плану</span></div>
        {received.length ? <ul className="dds-board-list dds-board-received">{received.map((row) => {
          const label = stageLabel(row, n(row.actual_amount), stages);
          return <li key={row.id}>
            <div><strong>{label || row.title}</strong><small>{label ? `${row.title} · ` : ""}поступило {day(row.actual_date)}</small></div>
            <b>{formatMoney(n(row.actual_amount))}</b>
          </li>;
        })}</ul> : <p className="dds-board-empty">Поступлений пока нет</p>}
      </section>

      <section className={cardClass("extra")} aria-label="Дополнительные расходы">
        <div className="dds-board-card-head">{foldTitle("extra", "Дополнительные расходы")}<span>{formatMoney(n(data.additional_costs?.fact))} оплачено · {formatMoney(n(data.additional_costs?.invoiced))} выставлено</span></div>
        {extra.length ? <div className="dds-board-table"><table><thead><tr><th>Дата</th><th>Контрагент</th><th>Сумма</th><th>Описание</th><th>Оплата</th></tr></thead><tbody>{extra.map((item, index) => {
          const id = item.entry_id;
          const value = id ? notes[id] ?? item.note ?? item.title : item.note ?? item.title;
          return <tr key={id ?? index}>
            <td>{day(item.actual_date || item.planned_date)}</td>
            <td>{item.counterparty || "—"}</td>
            <td className="money">{formatMoney(Math.max(n(item.fact), n(item.invoiced)))}</td>
            <td>{id ? <input id={`dds-extra-note-${id}`} aria-label={`Описание ${item.title}`} value={value} maxLength={300}
              onChange={(event) => setNotes((cur) => ({ ...cur, [id]: event.target.value }))}
              onBlur={() => void saveNote(item)}
              onKeyDown={(event) => { if (event.key === "Enter") event.currentTarget.blur(); }} /> : value}</td>
            <td>{n(item.invoiced) - n(item.fact) > 0.009 && id
              ? <button type="button" disabled={busy === id} onClick={() => void act(id, `/projects/${projectId}/cash-flow/${id}/record-payment`, { amount: (n(item.invoiced) - n(item.fact)).toFixed(2), actual_date: today() })}>Оплачен</button>
              : item.actual_date ? `оплачено ${day(item.actual_date)}` : "—"}</td>
          </tr>;
        })}</tbody></table></div> : <p className="dds-board-empty">Дополнительных расходов нет</p>}
      </section>
    </div>
  );
}
