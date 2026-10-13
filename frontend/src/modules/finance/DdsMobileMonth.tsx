import { useMemo, useState } from "react";
import { formatMoney, formatNumber } from "../../utils/numberFormat";

export type DdsMobileRow = {
  id: number;
  direction: string;
  planned_date: string;
  planned_amount: number | string;
  status: string;
  object: string;
  category: string;
};

type Line = { key: string; category: string; object: string; value: number; count: number };

const monthLong = new Intl.DateTimeFormat("ru-RU", { month: "long", year: "numeric", timeZone: "UTC" });

function monthTitle(key: string) {
  const text = monthLong.format(new Date(`${key}-01T00:00:00Z`)).replace(/\s*г\.?$/u, "");
  return text.charAt(0).toLocaleUpperCase("ru-RU") + text.slice(1);
}

/** The month a phone opens on: the current one if it has operations, else the next one that has, else the last. */
export function defaultDdsMonth(withOperations: string[], today = new Date().toISOString().slice(0, 7)) {
  if (!withOperations.length) return "";
  if (withOperations.includes(today)) return today;
  return withOperations.find((key) => key > today) || withOperations[withOperations.length - 1];
}

function lines(rows: DdsMobileRow[], direction: string): Line[] {
  const groups = new Map<string, Line>();
  for (const row of rows) {
    if (row.direction !== direction) continue;
    const key = `${row.object}\u0000${row.category}`;
    const current = groups.get(key) || { key, category: row.category, object: row.object, value: 0, count: 0 };
    current.value += Number(row.planned_amount);
    current.count += 1;
    groups.set(key, current);
  }
  return Array.from(groups.values()).sort((a, b) => b.value - a.value || a.category.localeCompare(b.category, "ru"));
}

type Props = {
  rows: DdsMobileRow[];
  currency: string;
  /** "YYYY-MM"; defaults to the current month. */
  today?: string;
};

/**
 * Phone view of the cash-flow plan (≤ 760 px): one month at a time as a
 * two-column list "статья → сумма" with month totals and the running balance,
 * instead of the year-wide calendar table whose sums ran off the screen.
 * Sums are the same planned amounts the calendar adds up (cancelled rows excluded).
 */
export function DdsMobileMonth({ rows, currency, today }: Props) {
  const active = useMemo(() => rows.filter((row) => row.status !== "cancelled"), [rows]);
  const monthsWithOperations = useMemo(() => Array.from(new Set(active.map((row) => row.planned_date.slice(0, 7)))).sort(), [active]);
  const [chosen, setChosen] = useState("");
  const month = monthsWithOperations.includes(chosen) ? chosen : defaultDdsMonth(monthsWithOperations, today);
  const monthRows = active.filter((row) => row.planned_date.slice(0, 7) === month);
  const inflow = lines(monthRows, "inflow");
  const outflow = lines(monthRows, "outflow");
  const inflowTotal = inflow.reduce((sum, line) => sum + line.value, 0);
  const outflowTotal = outflow.reduce((sum, line) => sum + line.value, 0);
  const balance = active.filter((row) => row.planned_date.slice(0, 7) <= month)
    .reduce((sum, row) => sum + (row.direction === "inflow" ? 1 : -1) * Number(row.planned_amount), 0);
  const manyObjects = new Set(active.map((row) => row.object)).size > 1;
  const money = (value: number) => !currency || currency === "RUB" ? formatMoney(value) : `${formatNumber(value)} ${currency}`;
  const section = (title: string, items: Line[], total: number, kind: string) => <section className={`dds-mobile-group ${kind}`} aria-label={title}>
    <h3><span>{title}</span><b>{money(total)}</b></h3>
    {items.length ? <ul>{items.map((line) => <li key={line.key}>
      <span>{line.category}{manyObjects && <small>{line.object}</small>}{line.count > 1 && <small>{line.count} операций</small>}</span>
      <b>{money(line.value)}</b>
    </li>)}</ul> : <p>Нет операций</p>}
  </section>;

  if (!monthsWithOperations.length) return <div className="dds-mobile-month"><p className="dds-empty">Добавьте первую плановую операцию ДДС.</p></div>;
  return <div className="dds-mobile-month">
    <label className="dds-mobile-month-picker"><span>Месяц</span>
      <select aria-label="Месяц ДДС" value={month} onChange={(event) => setChosen(event.target.value)}>
        {monthsWithOperations.map((key) => <option value={key} key={key}>{monthTitle(key)}</option>)}
      </select>
    </label>
    <dl className="dds-mobile-totals" aria-label={`Итоги за ${monthTitle(month)}`}>
      <div><dt>Приход</dt><dd className="positive">{money(inflowTotal)}</dd></div>
      <div><dt>Расход</dt><dd>{money(outflowTotal)}</dd></div>
      <div><dt>Поток за месяц</dt><dd className={inflowTotal - outflowTotal < 0 ? "negative" : "positive"}>{money(inflowTotal - outflowTotal)}</dd></div>
      <div><dt>Остаток на конец месяца</dt><dd className={balance < 0 ? "negative" : ""}>{money(balance)}</dd></div>
    </dl>
    {section("Поступления", inflow, inflowTotal, "inflow")}
    {section("Затраты", outflow, outflowTotal, "outflow")}
  </div>;
}
