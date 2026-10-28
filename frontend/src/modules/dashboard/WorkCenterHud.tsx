import { useEffect, useMemo, useState } from "react";
import { api } from "../../api/client";
import type { MoneyCalendarItem } from "./MoneyCalendar";
import "./work-center-hud.css";

// Owner 27.10.2026: the work center is a control-room screen — glass panels over a photo.
// Every number comes from the project's own data; nothing here is hard-coded.

type MonthRow = { month: string; income_plan: string | number; cumulative_plan: string | number; cumulative_fact: string | number };
type Budget = {
  income: { plan: string; fact: string };
  expense_total: { plan: string; fact: string; invoiced?: string };
  expense_by_category?: { category: string; plan: string; fact: string }[];
  by_month?: MonthRow[];
};
type Calendar = {
  items: MoneyCalendarItem[];
  counts: Record<string, number>;
  unmarked?: { count: number; amount: string };
};
type StageState = "ok" | "draft" | "late" | "ahead";
type HudExtra = {
  objects: { name: string; price: string; acted: string; stages: { number: string | number; state: StageState; amount: string; finish: string | null }[] }[];
  id_readiness: { object: string; percent: number; required_total: number; missing: number }[];
  invoices_to_check: number;
};

type Props = {
  projectId: number;
  onOpenFinance: () => void;
  onOpenItem?: (item: MoneyCalendarItem) => void;
  onOpenInvoices?: () => void;
  onOpenDocuments?: () => void;
};

const num = (value: string | number | null | undefined) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
};
export const mln = (value: number, digits = 1) => (value / 1_000_000).toFixed(digits).replace(".", ",");
const monthKey = (date: Date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
const stateColor: Record<StageState, string> = { ok: "#5dffa0", draft: "#ffb547", late: "#ff4d4d", ahead: "none" };
const stateLabel: Record<StageState, string> = { ok: "акт подписан / оплачен", draft: "акт не подписан", late: "срок прошёл", ahead: "впереди" };

function StageDiagram({ objects }: { objects: HudExtra["objects"] }) {
  const width = 560;
  const cols = Math.max(1, objects.length);
  const colWidth = (width - 20) / cols;
  return (
    <svg className="hud-diagram" viewBox={`0 0 ${width} 400`} role="img"
      aria-label="Этапы по объектам: зелёный — акт подписан или оплачен, янтарный — акт не подписан, красный — срок прошёл, контур — впереди">
      <g stroke="#7fd8ff" strokeWidth="1.4" fill="none">
        <line x1={width / 2} y1="8" x2={width / 2} y2="46" />
        <circle cx={width / 2} cy="64" r="18" /><circle cx={width / 2} cy="86" r="18" />
        <line x1={width / 2} y1="104" x2={width / 2} y2="128" />
        <line x1="14" y1="128" x2={width - 14} y2="128" strokeWidth="3" />
      </g>
      {objects.map((object, index) => {
        const x0 = 10 + index * colWidth;
        const cx = x0 + colWidth / 2;
        const step = Math.min(34, (colWidth - 30) / Math.max(1, object.stages.length));
        const startX = cx - (step * (object.stages.length - 1)) / 2;
        return (
          <g key={object.name}>
            <line x1={cx} y1="128" x2={cx} y2="160" stroke="#7fd8ff" strokeWidth="1.4" />
            <line x1={startX} y1="160" x2={startX + step * (object.stages.length - 1)} y2="160" stroke="#7fd8ff" strokeWidth="2" />
            <text x={cx} y="150" textAnchor="middle" fill="#9ff0ff" fontSize="14">{object.name} · {mln(num(object.price))} млн</text>
            {object.stages.map((stage, i) => {
              const x = startX + i * step;
              const color = stateColor[stage.state];
              return (
                <g key={`${stage.number}-${i}`}>
                  <title>{`${object.name}, этап ${stage.number}: ${stateLabel[stage.state]} · ${mln(num(stage.amount), 2)} млн`}</title>
                  <line x1={x} y1="160" x2={x} y2="196" stroke="#7fd8ff" strokeWidth="1.2" />
                  <rect x={x - Math.min(9, step / 2 - 1)} y="196" width={Math.min(18, step - 2)} height="30"
                    fill={color} stroke={color === "none" ? "#7fd8ff" : color}
                    className={stage.state === "late" ? "hud-blink" : undefined}
                    style={color === "none" ? undefined : { filter: `drop-shadow(0 0 4px ${color})` }} />
                  <line x1={x} y1="226" x2={x} y2="258" stroke="#7fd8ff" strokeWidth="1.2" strokeDasharray={stage.state === "ahead" ? "3 3" : undefined} />
                  <text x={x} y="276" textAnchor="middle" fill="#8fb7d3" fontSize="11">{stage.number}</text>
                </g>
              );
            })}
            <text x={cx} y="312" textAnchor="middle" fill="#d9f1ff" fontSize="13">
              актировано {mln(num(object.acted))} из {mln(num(object.price))} млн
            </text>
          </g>
        );
      })}
      <g fontSize="12" fill="#8fb7d3">
        {(Object.keys(stateLabel) as StageState[]).map((state, i) => (
          <g key={state} transform={`translate(${14 + i * 138}, 360)`}>
            <rect width="12" height="12" fill={stateColor[state]} stroke="#7fd8ff" />
            <text x="18" y="11">{state === "ok" ? "подписан/оплачен" : stateLabel[state]}</text>
          </g>
        ))}
      </g>
    </svg>
  );
}

function BalanceScope({ months, currentKey }: { months: MonthRow[]; currentKey: string }) {
  if (months.length < 2) return null;
  const plan = months.map((m) => num(m.cumulative_plan));
  const factIdx = months.map((m, i) => (m.month <= currentKey ? i : -1)).filter((i) => i >= 0);
  const fact = factIdx.map((i) => num(months[i].cumulative_fact));
  const all = [...plan, ...fact, 0];
  const max = Math.max(...all), min = Math.min(...all), span = max - min || 1;
  const x = (i: number) => (i / (months.length - 1)) * 400;
  const y = (v: number) => 8 + ((max - v) / span) * 118;
  const points = (values: number[], idx: number[]) => values.map((v, k) => `${x(idx[k]).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const first = months[0].month.slice(5, 7), last = months[months.length - 1].month.slice(5, 7);
  return (
    <svg viewBox="0 0 400 150" className="hud-scope" role="img" aria-label="Баланс по плану и по факту">
      <g stroke="rgba(127,216,255,.15)"><path d="M0 25H400M0 50H400M0 75H400M0 100H400M0 125H400" /></g>
      <line x1="0" x2="400" y1={y(0)} y2={y(0)} stroke="#8fb7d3" strokeDasharray="3 3" />
      <polyline fill="none" stroke="#7fd8ff" strokeWidth="2" points={points(plan, plan.map((_, i) => i))} style={{ filter: "drop-shadow(0 0 4px #7fd8ff)" }} />
      {fact.length > 1 && <polyline fill="none" stroke="#5dffa0" strokeWidth="2" strokeDasharray="6 3" points={points(fact, factIdx)} style={{ filter: "drop-shadow(0 0 4px #5dffa0)" }} />}
      <text x="2" y="146" fill="#8fb7d3" fontSize="10">{first}</text>
      <text x="372" y="146" fill="#8fb7d3" fontSize="10">{last}</text>
      <text x="300" y="14" fill="#5dffa0" fontSize="10">— ФАКТ</text><text x="300" y="26" fill="#7fd8ff" fontSize="10">— ПЛАН</text>
    </svg>
  );
}

const levelColor: Record<string, string> = { overdue: "#ff4d4d", critical: "#ff4d4d", soon: "#ffb547", near: "#7fd8ff", ok: "#7fd8ff", none: "#7fd8ff" };
const kindLabel: Record<string, string> = { pay_invoice: "оплатить счёт", pay_plan: "оплата по плану", sign_act: "подписать акт", receive: "ждём приход" };

function whenText(item: MoneyCalendarItem) {
  if (item.days_left === null) return "без срока";
  if (item.days_left < 0) return `просрочено ${Math.abs(item.days_left)} дн.`;
  if (item.days_left === 0) return "сегодня";
  return `через ${item.days_left} дн.`;
}

export function WorkCenterHud({ projectId, onOpenFinance, onOpenItem, onOpenInvoices, onOpenDocuments }: Props) {
  const [budget, setBudget] = useState<Budget | null>(null);
  const [calendar, setCalendar] = useState<Calendar | null>(null);
  const [extra, setExtra] = useState<HudExtra | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!projectId) return;
    let alive = true;
    setBudget(null); setCalendar(null); setExtra(null); setError("");
    const ok = <T,>(setter: (value: T | null) => void) => (value: T) => { if (alive) setter(value ?? null); };
    api<Budget>(`/projects/${projectId}/budget-from-dds`).then(ok(setBudget)).catch((e) => { if (alive) setError(e instanceof Error ? e.message : "Не удалось загрузить данные"); });
    api<Calendar>(`/projects/${projectId}/money-calendar`).then(ok(setCalendar)).catch(() => undefined);
    api<HudExtra>(`/projects/${projectId}/work-center/hud`).then(ok(setExtra)).catch(() => undefined);
    return () => { alive = false; };
  }, [projectId]);

  const view = useMemo(() => {
    if (!budget || !budget.income || !budget.expense_total) return null;
    const incomePlan = num(budget.income.plan), incomeFact = num(budget.income.fact);
    const expensePlan = num(budget.expense_total.plan), expenseFact = num(budget.expense_total.fact);
    const months = Array.isArray(budget.by_month) ? [...budget.by_month].sort((a, b) => a.month.localeCompare(b.month)) : [];
    const currentKey = monthKey(new Date());
    const planToDate = months.filter((m) => m.month < currentKey).reduce((s, m) => s + num(m.income_plan), 0);
    const overdue = Math.max(0, planToDate - incomeFact);
    // The past is already settled by the facts: only months from now on can still become a gap.
    const critical = months.filter((m) => m.month >= currentKey)
      .reduce<MonthRow | null>((acc, m) => (!acc || num(m.cumulative_plan) < num(acc.cumulative_plan) ? m : acc), null);
    const categories = [...(budget.expense_by_category || [])].sort((a, b) => num(b.plan) - num(a.plan)).slice(0, 6);
    return { incomePlan, incomeFact, expensePlan, expenseFact, months, currentKey, overdue, critical, categories };
  }, [budget]);

  if (error && !view) return <section className="hud-stage" aria-label="Щит управления"><p role="alert" className="hud-error">{error}</p></section>;
  if (!view) return <section className="hud-stage" aria-label="Щит управления"><p className="hud-loading">Считаем деньги проекта…</p></section>;

  const segments = 40;
  const receivedSeg = view.incomePlan > 0 ? Math.round((view.incomeFact / view.incomePlan) * segments) : 0;
  const overdueSeg = view.incomePlan > 0 ? Math.min(segments - receivedSeg, Math.max(view.overdue > 0 ? 1 : 0, Math.round((view.overdue / view.incomePlan) * segments))) : 0;
  const pct = (part: number) => (view.incomePlan > 0 ? Math.round((part / view.incomePlan) * 100) : 0);
  const cash = view.incomeFact - view.expenseFact;
  const gap = view.critical !== null && num(view.critical.cumulative_plan) < 0;
  const maxCat = Math.max(1, ...view.categories.map((c) => num(c.plan)));
  const alarms = (calendar?.counts.overdue ?? 0) + (calendar?.counts.critical ?? 0);
  const toCheck = extra?.invoices_to_check ?? 0;
  const events = [...(calendar?.items ?? [])]
    .sort((a, b) => (a.days_left ?? 9999) - (b.days_left ?? 9999)).slice(0, 6);
  const now = new Date();
  const dateText = now.toLocaleDateString("ru-RU") + " · " + now.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });

  return (
    <section className="hud-stage" aria-label="Щит управления">
      <div className="hud-photo" aria-hidden="true" style={{ backgroundImage: `url(${import.meta.env.BASE_URL}assets/control-room/dusk-lines.webp)` }} />
      <div className="hud-grade" aria-hidden="true" />
      <div className="hud-board">
        <div className="hud-title">
          <span className="hud-kicker">PU WORKSPACE · ЩИТ УПРАВЛЕНИЯ · {dateText}</span>
          <div className="hud-leds" aria-label="Состояние">
            <span><i className={`hud-dot red${alarms ? " hud-blink" : ""}`} />Аварий: {alarms}</span>
            <span><i className="hud-dot amber" />Счетов на проверку: {toCheck}</span>
            <span><i className={`hud-dot ${gap ? "red" : "green"}`} />{gap ? "Кассовый разрыв" : "Касса в норме"}</span>
          </div>
        </div>

        <div className="hud-grid">
          <section className="hud-panel hud-tall" aria-label="Схема этапов">
            <div className="hud-ptitle">Однолинейная схема · этапы <span className="hud-tag">ВВОД · {mln(view.incomePlan)} МЛН</span></div>
            {extra && extra.objects.length > 0
              ? <>
                <StageDiagram objects={extra.objects} />
                <div className="hud-readouts" style={{ marginTop: 14 }}>
                  <div className="hud-readout"><span>Актировано · всего</span><b className="led green">{mln(extra.objects.reduce((t, o) => t + num(o.acted), 0))}</b><div className="hud-cap">млн ₽ по подписанным и оплаченным этапам</div></div>
                  <div className="hud-readout"><span>Осталось актировать</span><b className="led amber">{mln(extra.objects.reduce((t, o) => t + num(o.price) - num(o.acted), 0))}</b><div className="hud-cap">млн ₽ по этапам договора</div></div>
                </div>
              </>
              : extra
                ? <>
                  <p className="hud-empty">Этапы по объектам ещё не заведены</p>
                  {onOpenDocuments && <div className="hud-readouts" style={{ marginTop: 14 }}><button type="button" className="hud-readout hud-click" onClick={onOpenDocuments}><span>Документы</span><b className="led cyan">→</b><div className="hud-cap">Открыть договоры</div></button></div>}
                </>
                : <p className="hud-muted">Загружаем этапы…</p>}
          </section>

          <section className="hud-panel" aria-label="Деньги проекта">
            <div className="hud-ptitle">Деньги проекта <span className="hud-tag">МЛН ₽</span></div>
            <div className="hud-readouts">
              <div className="hud-readout"><span>Приход · факт</span><b className="led green">{mln(view.incomeFact)}</b></div>
              <div className="hud-readout"><span>Расход · факт</span><b className="led cyan">{mln(view.expenseFact)}</b></div>
              <div className="hud-readout"><span>Деньги сейчас</span><b className={`led ${cash >= 0 ? "green" : "red"}`}>{mln(cash)}</b></div>
              <div className="hud-readout"><span>Крит. точка{view.critical ? ` · ${view.critical.month.slice(5, 7)}.${view.critical.month.slice(2, 4)}` : ""}</span>
                <b className={`led ${gap ? "red" : "amber"}`}>{view.critical ? mln(num(view.critical.cumulative_plan)) : "—"}</b></div>
            </div>
            <BalanceScope months={view.months} currentKey={view.currentKey} />
            <div className="hud-cap">{gap ? "Ожидается кассовый разрыв по плану" : "Кассового разрыва по плану нет"}</div>
          </section>

          <section className="hud-panel" aria-label="Журнал событий">
            <div className="hud-ptitle">Журнал событий {alarms > 0 && <span className="hud-tag hud-tag-alarm hud-blink">{alarms} АВАР.</span>}</div>
            {events.length === 0 && toCheck === 0 && <p className="hud-empty ok">Событий, требующих внимания, нет — всё в порядке</p>}
            {events.map((item, index) => (
              <button type="button" className="hud-alarm" key={`${item.kind}-${index}`} onClick={() => onOpenItem ? onOpenItem(item) : onOpenFinance()}
                aria-label={`${kindLabel[item.kind] ?? item.kind}: ${item.title}, ${whenText(item)}`}>
                <i className={item.level === "overdue" ? "hud-blink" : undefined} style={{ background: levelColor[item.level] ?? "#7fd8ff", boxShadow: `0 0 8px ${levelColor[item.level] ?? "#7fd8ff"}` }} />
                <span><strong>{item.title}</strong><small>{kindLabel[item.kind] ?? item.kind} · {whenText(item)}</small></span>
                <b className={`led ${item.level === "overdue" || item.level === "critical" ? "red" : item.level === "soon" ? "amber" : "cyan"}`}>{mln(num(item.amount))}</b>
              </button>
            ))}
            {toCheck > 0 && (
              <button type="button" className="hud-alarm" onClick={() => (onOpenInvoices ?? onOpenFinance)()}>
                <i style={{ background: "#ffb547", boxShadow: "0 0 8px #ffb547" }} />
                <span><strong>Счета на проверку</strong><small>загружены, не подтверждены</small></span>
                <b className="led amber">{toCheck}</b>
              </button>
            )}
            {calendar?.unmarked && calendar.unmarked.count > 0 && <div className="hud-cap">Без отметки об оплате: {calendar.unmarked.count} плановых строк на {mln(num(calendar.unmarked.amount))} млн</div>}
          </section>

          <section className="hud-panel" aria-label="Приход и структура расходов">
            <div className="hud-ptitle">Приход от заказчика <span className="hud-tag">ПЛАН {mln(view.incomePlan)}</span></div>
            <div className="hud-inflow" role="img" aria-label={`Получено ${pct(view.incomeFact)} процентов плана, просрочено ${pct(view.overdue)}`}>
              {Array.from({ length: segments }, (_, i) => {
                const kind = i < receivedSeg ? "ok" : i < receivedSeg + overdueSeg ? "late" : "rest";
                return <i key={i} className={`seg ${kind}${kind === "late" ? " hud-blink" : ""}`} />;
              })}
            </div>
            <div className="hud-inflow-cap"><span className="g">ПОЛУЧЕНО {pct(view.incomeFact)}%</span><span className="a">ПРОСРОЧЕНО {pct(view.overdue)}%</span><span>ВПЕРЕДИ {Math.max(0, 100 - pct(view.incomeFact) - pct(view.overdue))}%</span></div>
            <div className="hud-ptitle" style={{ marginTop: 14 }}>Расходы по статьям <span className="hud-tag">ПЛАН {mln(view.expensePlan)}</span></div>
            <div className="hud-columns" role="img" aria-label="Расходы по статьям, млн ₽">
              {view.categories.map((c, i) => (
                <div key={c.category} className="col" title={`${c.category}: план ${mln(num(c.plan))} млн, оплачено ${mln(num(c.fact), 2)} млн`}>
                  <b>{mln(num(c.plan))}</b>
                  <span className={`bar${i === 0 ? " top" : ""}`} style={{ height: `${Math.max(4, (num(c.plan) / maxCat) * 100)}%` }} />
                  <small>{c.category}</small>
                </div>
              ))}
            </div>
            <div className="hud-cap">Оплачено {mln(view.expenseFact, 2)} млн из {mln(view.expensePlan)} — счета разносятся по мере загрузки</div>
          </section>

          <section className="hud-panel" aria-label="Исполнительная документация">
            <div className="hud-ptitle">Исполнительная документация</div>
            {extra && extra.id_readiness.length === 0 && <p className="hud-empty">Данных по исполнительной документации пока нет</p>}
            <div className="hud-readouts">
              {(extra?.id_readiness ?? []).map((r) => (
                <button type="button" className="hud-readout hud-click" key={r.object} onClick={onOpenDocuments}>
                  <span>ИД · {r.object}</span>
                  <b className={`led ${r.percent >= 80 ? "green" : "amber"}`}>{r.percent}%</b>
                  <div className="hud-cap">нет {r.missing} из {r.required_total}</div>
                </button>
              ))}
              <div className="hud-readout"><span>Счета</span><b className={`led ${toCheck ? "amber" : "green"}`}>{toCheck}</b><div className="hud-cap">на проверке</div></div>
            </div>
          </section>
        </div>
        <p className="hud-credit">
          Фон: «Transmission Lines at Sunset», Kkiefuik, <a href="https://commons.wikimedia.org/wiki/File:Transmission_Lines_at_Sunset.jpg" target="_blank" rel="noreferrer">Wikimedia Commons</a>, CC BY 4.0
        </p>
      </div>
    </section>
  );
}
