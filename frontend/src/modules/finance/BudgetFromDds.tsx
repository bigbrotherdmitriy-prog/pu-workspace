import { useEffect, useMemo, useState } from "react";
import { api } from "../../api/client";
import { formatMoney } from "../../utils/numberFormat";
import "./budget-from-dds.css";

type MoneyPair = { plan: string; fact: string };

type CounterpartyRow = {
  counterparty: string;
  plan: string;
  fact: string;
};

type ExpenseByCategory = {
  category_id: number | null;
  category: string;
  plan: string;
  fact: string;
  deviation: string;
  counterparties: CounterpartyRow[];
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
    const incomePlan = toNumber(existing?.income_plan);
    const incomeFact = toNumber(existing?.income_fact);
    const expensePlan = toNumber(existing?.expense_plan);
    const expenseFact = toNumber(existing?.expense_fact);
    const netPlan = incomePlan - expensePlan;
    const netFact = incomeFact - expenseFact;
    cumulativePlan += netPlan;
    cumulativeFact += netFact;
    return {
      month: key,
      income_plan: existing?.income_plan ?? "0",
      income_fact: existing?.income_fact ?? "0",
      expense_plan: existing?.expense_plan ?? "0",
      expense_fact: existing?.expense_fact ?? "0",
      net_plan: existing?.net_plan ?? "0",
      net_fact: existing?.net_fact ?? "0",
      cumulative_plan: existing?.cumulative_plan ?? String(cumulativePlan),
      cumulative_fact: existing?.cumulative_fact ?? String(cumulativeFact),
    };
  });
}

export function BudgetFromDds({ projectId, contractId, currency }: Props) {
  const [data, setData] = useState<BudgetFromDdsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [expandedCategories, setExpandedCategories] = useState<Set<string>>(new Set());

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    setExpandedCategories(new Set());
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

  return <div className="budget-from-dds">
    <div className="budget-from-dds-table-wrap">
      <table className="budget-from-dds-table">
        <thead>
          <tr>
            <th>Статья</th>
            <th>План, {currencyLabel}</th>
            <th>Факт, {currencyLabel}</th>
            <th>Отклонение, {currencyLabel}</th>
            <th>% исполнения</th>
          </tr>
        </thead>
        <tbody>
          <tr className="budget-from-dds-income">
            <td>Доходы</td>
            <td className="money positive">{fmt(data.income.plan)}</td>
            <td className="money positive">{fmt(data.income.fact)}</td>
            <td className={`money ${deviation(data.income.fact, data.income.plan) < 0 ? "negative" : ""}`}>{fmt(deviation(data.income.fact, data.income.plan))}</td>
            <td>{completionPct(data.income.fact, data.income.plan) ?? "—"}</td>
          </tr>
          <tr className="budget-from-dds-expense-total">
            <td>Расходы прямые, всего</td>
            <td className="money">{fmt(data.expense_total.plan)}</td>
            <td className="money">{fmt(data.expense_total.fact)}</td>
            <td className={`money ${deviation(data.expense_total.fact, data.expense_total.plan) > 0 ? "negative" : ""}`}>{fmt(deviation(data.expense_total.fact, data.expense_total.plan))}</td>
            <td>{completionPct(data.expense_total.fact, data.expense_total.plan) ?? "—"}</td>
          </tr>
          {data.expense_by_category.map((row) => {
            const key = row.category_id !== null ? `cat-${row.category_id}` : `name-${row.category}`;
            const expanded = expandedCategories.has(key);
            const hasCounterparties = row.counterparties.length > 0;
            return (
              <>
                <tr
                  key={key}
                  className={`budget-from-dds-category ${expanded ? "expanded" : ""}`}
                  onClick={() => hasCounterparties && toggleCategory(key)}
                >
                  <td>
                    {hasCounterparties ? <button type="button" className="budget-from-dds-toggle" aria-expanded={expanded} onClick={(event) => { event.stopPropagation(); toggleCategory(key); }}>{expanded ? "▾" : "▸"}</button> : null}
                    {row.category}
                  </td>
                  <td className="money">{fmt(row.plan)}</td>
                  <td className="money">{fmt(row.fact)}</td>
                  <td className={`money ${deviation(row.fact, row.plan) > 0 ? "negative" : ""}`}>{fmt(deviation(row.fact, row.plan))}</td>
                  <td>{completionPct(row.fact, row.plan) ?? "—"}</td>
                </tr>
                {expanded && row.counterparties.map((cp, index) => (
                  <tr key={`${key}-cp-${index}`} className="budget-from-dds-counterparty">
                    <td>{cp.counterparty}</td>
                    <td className="money">{fmt(cp.plan)}</td>
                    <td className="money">{fmt(cp.fact)}</td>
                    <td className={`money ${deviation(cp.fact, cp.plan) > 0 ? "negative" : ""}`}>{fmt(deviation(cp.fact, cp.plan))}</td>
                    <td>{completionPct(cp.fact, cp.plan) ?? "—"}</td>
                  </tr>
                ))}
              </>
            );
          })}
          <tr className="budget-from-dds-profit">
            <td>Прибыль</td>
            <td className={`money ${toNumber(data.profit.plan) < 0 ? "negative" : "positive"}`}>{fmt(data.profit.plan)}</td>
            <td className={`money ${toNumber(data.profit.fact) < 0 ? "negative" : "positive"}`}>{fmt(data.profit.fact)}</td>
            <td className={`money ${deviation(data.profit.fact, data.profit.plan) < 0 ? "negative" : ""}`}>{fmt(deviation(data.profit.fact, data.profit.plan))}</td>
            <td>—</td>
          </tr>
          <tr className="budget-from-dds-margin">
            <td>Рентабельность, %</td>
            <td>{data.margin_pct.plan !== null ? `${data.margin_pct.plan}%` : "—"}</td>
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
