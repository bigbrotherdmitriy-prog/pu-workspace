import { Fragment, useEffect, useMemo, useState } from "react";
import { api } from "../../api/client";
import { formatMoney } from "../../utils/numberFormat";
import "./budget-from-dds.css";

type MoneyPair = { plan: string; fact: string; invoiced?: string };

type CounterpartyRow = {
  counterparty: string;
  plan: string;
  fact: string;
};

type ExpenseByCategory = {
  invoiced?: string;
  category_id: number | null;
  category: string;
  plan: string;
  fact: string;
  deviation: string;
  remaining?: string | null;
  counterparties: CounterpartyRow[];
};

type AdditionalCostItem = {
  invoiced?: string;
  title: string;
  counterparty: string | null;
  planned_date: string | null;
  actual_date: string | null;
  fact: string;
};

type AdditionalCosts = {
  invoiced?: string;
  plan: string;
  fact: string;
  items?: AdditionalCostItem[];
};

type MonthRow = {
  month: string;
  income_plan: string;
  income_fact: string;
  expense_plan: string;
  expense_fact: string;
  net_plan: string;
  net_fact: string;
  cumulative_plan: string;
  cumulative_fact: string;
};

type BudgetFromDdsResponse = {
  currency: string;
  income: MoneyPair;
  expense_total: MoneyPair;
  direct_expense_total?: MoneyPair | null;
  additional_costs?: AdditionalCosts | null;
  expense_by_category: ExpenseByCategory[];
  profit: MoneyPair;
  margin_pct: { plan: string | null; fact: string | null };
  by_month: MonthRow[];
  other_currency_entries_count: number;
};

type Props = {
  projectId: number;
  contractId?: number | null;
  currency: string;
};

const monthFormat = new Intl.DateTimeFormat("ru-RU", { month: "long", year: "numeric", timeZone: "UTC" });

function monthLabel(key: string) {
  return monthFormat.format(new Date(`${key}-01T00:00:00Z`));
}

function toNumber(value: string | number | null | undefined): number {
  if (value === null || value === undefined) return 0;
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function fmt(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return formatMoney(toNumber(value));
}

function deviation(fact: string | number, plan: string | number): number {
  return toNumber(fact) - toNumber(plan);
}

function completionPct(fact: string | number, plan: string | number): string | null {
  const planValue = toNumber(plan);
  if (planValue === 0) return null;
  return `${(toNumber(fact) / planValue * 100).toFixed(1)}%`;
}

function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  return value;
}

function monthDiff(start: string, end: string): number {
  const startYear = Number(start.slice(0, 4));
  const startMonth = Number(start.slice(5, 7));
  const endYear = Number(end.slice(0, 4));
  const endMonth = Number(end.slice(5, 7));
  return (endYear - startYear) * 12 + (endMonth - startMonth);
}

function monthSequence(start: string, end: string): string[] {
  const total = monthDiff(start, end);
  if (total < 0) return [start];
  const result: string[] = [];
  let year = Number(start.slice(0, 4));
  let month = Number(start.slice(5, 7));
  for (let i = 0; i <= total; i += 1) {
    result.push(`${String(year).padStart(4, "0")}-${String(month).padStart(2, "0")}`);
    month += 1;
    if (month > 12) { month = 1; year += 1; }
  }
  return result;
}

function fillMissingMonths(rows: MonthRow[]): MonthRow[] {
  if (rows.length < 2) return rows;
  const sorted = [...rows].sort((a, b) => a.month.localeCompare(b.month));
  const first = sorted[0].month;
  const last = sorted[sorted.length - 1].month;
  const byKey = new Map(sorted.map((row) => [row.month, row]));
  const sequence = monthSequence(first, last);
  let cumulativePlan = 0;
  let cumulativeFact = 0;
  return sequence.map((key) => {
    const existing = byKey.get(key);
    if (existing) {
      cumulativePlan += toNumber(existing.net_plan);
      cumulativeFact += toNumber(existing.net_fact);
      return existing;
    }
    return {
      month: key,
      income_plan: "0",
      income_fact: "0",
      expense_plan: "0",
      expense_fact: "0",
      net_plan: "0",
      net_fact: "0",
      cumulative_plan: String(cumulativePlan),
      cumulative_fact: String(cumulativeFact),
    };
  });
}

export function BudgetFromDds({ projectId, contractId, currency }: Props) {
  const [data, setData] = useState<BudgetFromDdsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [expandedCategories, setExpandedCategories] = useState<Set<string>>(new Set());
  const [extraExpanded, setExtraExpanded] = useState(false);

  useEffect(() => {
    if (projectId <= 0) {
      setData(null);
      setError("");
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError("");
    setExpandedCategories(new Set());
    setExtraExpanded(false);
    const params = new URLSearchParams();
    if (contractId) params.set("contract_id", String(contractId));
    const query = params.toString();
    const path = `/projects/${projectId}/budget-from-dds${query ? `?${query}` : ""}`;
    api<BudgetFromDdsResponse>(path, { signal: controller.signal })
      .then((response) => {
        setData(response);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        const message = err instanceof Error ? err.message : "Не удалось загрузить бюджет";
        setError(message);
        setData(null);
        setLoading(false);
      });
    return () => controller.abort();
  }, [projectId, contractId]);

  const months = useMemo(() => (data ? fillMissingMonths(data.by_month) : []), [data]);

  const toggleCategory = (key: string) => {
    setExpandedCategories((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  if (loading) return <p className="budget-from-dds-status">Загружаем бюджет…</p>;
  if (error) return <p className="budget-from-dds-status budget-from-dds-error" role="alert">{error}</p>;
  if (!data) return <p className="budget-from-dds-status">В ДДС пока нет операций — бюджет появится автоматически</p>;

  const isEmpty = !data.by_month.length && !data.expense_by_category.length
    && toNumber(data.income.plan) === 0 && toNumber(data.income.fact) === 0
    && toNumber(data.expense_total.plan) === 0 && toNumber(data.expense_total.fact) === 0;

  if (isEmpty) return <p className="budget-from-dds-status">В ДДС пока нет операций — бюджет появится автоматически</p>;

  const currencyLabel = currency || data.currency;

  const direct = data.direct_expense_total ?? data.expense_total;
  const additional: AdditionalCosts = data.additional_costs ?? { plan: "0", fact: "0", items: [] };
  const additionalItems = additional.items ?? [];

  const remainingCell = (plan: string | number, fact: string | number, remainingOverride?: string | null) => {
    const value = remainingOverride !== undefined && remainingOverride !== null
      ? toNumber(remainingOverride)
      : toNumber(plan) - toNumber(fact);
    const overrun = value < 0;
    return <td className={`money${overrun ? " budget-overrun" : ""}`}>{fmt(value)}</td>;
  };

  // Remaining = plan − max(invoiced, paid): an invoice already commits money (owner, 15.10.2026).
  const remainingTotalCell = (plan: string | number, fact: string | number, invoiced?: string | number) => {
    const value = toNumber(plan) - Math.max(toNumber(fact), toNumber(invoiced ?? 0));
    const overrun = value < 0;
    return <td className={`money${overrun ? " budget-overrun" : ""}`}>{fmt(value)}</td>;
  };

  return <div className="budget-from-dds">
    <p className="budget-from-dds-caption">Бюджет собирается автоматически из планового ДДС. Счета и оплаты вне плана попадают в «Дополнительные затраты».</p>
    <div className="budget-from-dds-table-wrap">
      <table className="budget-from-dds-table">
        <thead>
          <tr>
            <th>Статья</th>
            <th>План, {currencyLabel}</th>
            <th>Выставлено, {currencyLabel}</th>
            <th>Оплачено, {currencyLabel}</th>
            <th>Остаток, {currencyLabel}</th>
            <th>% исполнения</th>
          </tr>
        </thead>
        <tbody>
          <tr className="budget-from-dds-income">
            <td>Доходы</td>
            <td className="money positive">{fmt(data.income.plan)}</td>
            <td className="money">—</td>
            <td className="money positive">{fmt(data.income.fact)}</td>
            {remainingTotalCell(data.income.plan, data.income.fact)}
            <td>{completionPct(data.income.fact, data.income.plan) ?? "—"}</td>
          </tr>
          <tr className="budget-from-dds-expense-total">
            <td>Прямые расходы (по плановому ДДС)</td>
            <td className="money">{fmt(direct.plan)}</td>
            <td className="money">{fmt(direct.invoiced)}</td>
            <td className="money">{fmt(direct.fact)}</td>
            {remainingTotalCell(direct.plan, direct.fact, direct.invoiced)}
            <td>{completionPct(direct.fact, direct.plan) ?? "—"}</td>
          </tr>
          {data.expense_by_category.map((row) => {
            const key = row.category_id !== null ? `cat-${row.category_id}` : `name-${row.category}`;
            const expanded = expandedCategories.has(key);
            const hasCounterparties = row.counterparties.length > 0;
            return (
              <Fragment key={key}>
                <tr
                  className={`budget-from-dds-category ${expanded ? "expanded" : ""}`}
                  onClick={() => hasCounterparties && toggleCategory(key)}
                >
                  <td>
                    {hasCounterparties ? <button type="button" className="budget-from-dds-toggle" aria-expanded={expanded} onClick={(event) => { event.stopPropagation(); toggleCategory(key); }}>{expanded ? "▾" : "▸"}</button> : null}
                    {row.category}
                  </td>
                  <td className="money">{fmt(row.plan)}</td>
                  <td className="money">{fmt(row.invoiced)}</td>
                  <td className="money">{fmt(row.fact)}</td>
                  {remainingCell(row.plan, row.fact, row.remaining)}
                  <td>{completionPct(row.fact, row.plan) ?? "—"}</td>
                </tr>
                {expanded && row.counterparties.map((cp, index) => (
                  <tr key={`${key}-cp-${index}`} className="budget-from-dds-counterparty">
                    <td>{cp.counterparty}</td>
                    <td className="money">{fmt(cp.plan)}</td>
                    <td className="money">—</td>
                    <td className="money">{fmt(cp.fact)}</td>
                    {remainingCell(cp.plan, cp.fact)}
                    <td>{completionPct(cp.fact, cp.plan) ?? "—"}</td>
                  </tr>
                ))}
              </Fragment>
            );
          })}
          <tr
            className={`budget-from-dds-extra budget-extra ${extraExpanded ? "expanded" : ""}`}
            onClick={() => additionalItems.length > 0 && setExtraExpanded((v) => !v)}
          >
            <td>
              {additionalItems.length > 0 ? <button type="button" className="budget-from-dds-toggle" aria-expanded={extraExpanded} onClick={(event) => { event.stopPropagation(); setExtraExpanded((v) => !v); }}>{extraExpanded ? "▾" : "▸"}</button> : null}
              Дополнительные затраты (вне плана)
            </td>
            <td className="money">{fmt(additional.plan)}</td>
            <td className="money">{fmt(additional.invoiced)}</td>
            <td className="money">{fmt(additional.fact)}</td>
            {remainingTotalCell(additional.plan, additional.fact, additional.invoiced)}
            <td>{completionPct(additional.fact, additional.plan) ?? "—"}</td>
          </tr>
          {extraExpanded && additionalItems.map((item, index) => (
            <tr key={`extra-${index}`} className="budget-from-dds-extra-item">
              <td>
                {item.title}
                <small>{[item.counterparty, formatDate(item.actual_date || item.planned_date)].filter((part) => part && part !== "—").join(" · ")}</small>
              </td>
              <td className="money">—</td>
              <td className="money">{fmt(item.invoiced)}</td>
              <td className="money">{fmt(item.fact)}</td>
              <td className="money">—</td>
              <td>—</td>
            </tr>
          ))}
          <tr className="budget-from-dds-expense-total">
            <td>Расходы всего</td>
            <td className="money">{fmt(data.expense_total.plan)}</td>
            <td className="money">{fmt(data.expense_total.invoiced)}</td>
            <td className="money">{fmt(data.expense_total.fact)}</td>
            {remainingTotalCell(data.expense_total.plan, data.expense_total.fact, data.expense_total.invoiced)}
            <td>{completionPct(data.expense_total.fact, data.expense_total.plan) ?? "—"}</td>
          </tr>
          <tr className="budget-from-dds-profit">
            <td>Прибыль</td>
            <td className={`money ${toNumber(data.profit.plan) < 0 ? "negative" : "positive"}`}>{fmt(data.profit.plan)}</td>
            <td className="money">—</td>
            <td className={`money ${toNumber(data.profit.fact) < 0 ? "negative" : "positive"}`}>{fmt(data.profit.fact)}</td>
            {remainingTotalCell(data.profit.plan, data.profit.fact)}
            <td>—</td>
          </tr>
          <tr className="budget-from-dds-margin">
            <td>Рентабельность, %</td>
            <td>{data.margin_pct.plan !== null ? `${data.margin_pct.plan}%` : "—"}</td>
            <td>—</td>
            <td>{data.margin_pct.fact !== null ? `${data.margin_pct.fact}%` : "—"}</td>
            <td>—</td>
            <td>—</td>
          </tr>
        </tbody>
      </table>
    </div>

    {months.length > 0 && (
      <div className="budget-from-dds-table-wrap budget-from-dds-months-wrap">
        <table className="budget-from-dds-table budget-from-dds-months">
          <thead>
            <tr>
              <th>Месяц</th>
              <th>Приход план, {currencyLabel}</th>
              <th>Приход факт, {currencyLabel}</th>
              <th>Расход план, {currencyLabel}</th>
              <th>Расход факт, {currencyLabel}</th>
              <th>Сальдо план, {currencyLabel}</th>
              <th>Сальдо факт, {currencyLabel}</th>
              <th>Накопительный итог план, {currencyLabel}</th>
              <th>Накопительный итог факт, {currencyLabel}</th>
            </tr>
          </thead>
          <tbody>
            {months.map((row) => (
              <tr key={row.month}>
                <td>{monthLabel(row.month)}</td>
                <td className="money positive">{fmt(row.income_plan)}</td>
                <td className="money positive">{fmt(row.income_fact)}</td>
                <td className="money">{fmt(row.expense_plan)}</td>
                <td className="money">{fmt(row.expense_fact)}</td>
                <td className={`money ${toNumber(row.net_plan) < 0 ? "negative" : ""}`}>{fmt(row.net_plan)}</td>
                <td className={`money ${toNumber(row.net_fact) < 0 ? "negative" : ""}`}>{fmt(row.net_fact)}</td>
                <td className={`money ${toNumber(row.cumulative_plan) < 0 ? "negative budget-gap" : ""}`}>{fmt(row.cumulative_plan)}</td>
                <td className={`money ${toNumber(row.cumulative_fact) < 0 ? "negative budget-gap" : ""}`}>{fmt(row.cumulative_fact)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )}

    {data.other_currency_entries_count > 0 && (
      <p className="budget-from-dds-note">Операций в другой валюте: {data.other_currency_entries_count}</p>
    )}
  </div>;
}
