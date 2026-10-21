import { useEffect, useState } from "react";
import { api } from "../../api/client";
import "./project-money-rings.css";

type Pair = { plan: string; fact: string };
type ExpenseTotal = { plan: string; fact: string; invoiced: string };
type MarginPair = { plan: string; fact: string };
type MonthRow = {
  month: string;
  income_plan: string | number;
  income_fact: string | number;
  expense_plan: string | number;
  expense_fact: string | number;
  cumulative_plan: string | number;
  cumulative_fact: string | number;
};
type CategoryRow = { category: string; plan: string | number; fact: string | number };

export type BudgetFromDds = {
  income: Pair;
  expense_total: ExpenseTotal;
  profit: Pair;
  margin_pct: MarginPair;
  by_month?: MonthRow[];
  expense_by_category?: CategoryRow[];
};

function num(x: string | number | null | undefined): number {
  const n = Number(x);
  return Number.isFinite(n) ? n : 0;
}

export function shortMoney(value: number): string {
  const abs = Math.abs(value);
  const sign = value < 0 ? "−" : "";
  if (abs >= 1_000_000) return `${sign}${(abs / 1_000_000).toFixed(1).replace(".", ",")} млн ₽`;
  if (abs >= 1_000) return `${sign}${Math.round(abs / 1_000)} тыс ₽`;
  return `${sign}${Math.round(abs)} ₽`;
}

const monthFormat = new Intl.DateTimeFormat("ru-RU", { month: "long", year: "numeric", timeZone: "UTC" });

function monthName(key: string): string {
  const date = new Date(`${key}-01T00:00:00Z`);
  return Number.isNaN(date.getTime()) ? key : monthFormat.format(date).replace(" г.", "");
}

function percent(part: number, whole: number): number {
  if (whole <= 0) return 0;
  return Math.max(0, Math.min(100, (part / whole) * 100));
}

const CATEGORY_COLORS = ["#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#06b6d4"];
const OTHER_COLOR = "#94a3b8";

type Slice = { label: string; value: number; color: string; extra?: string };

function PieChart({ slices, title, size = 150 }: { slices: Slice[]; title: string; size?: number }) {
  const total = slices.reduce((sum, s) => sum + Math.max(0, s.value), 0);
  const r = size / 2 - 6;
  const cx = size / 2;
  const cy = size / 2;
  const gap = 1.5;
  const bg = "var(--read-surface, #ffffff)";

  let angle = -Math.PI / 2;
  const paths: { d: string; color: string; label: string; value: number }[] = [];

  const nonZero = slices.filter((s) => s.value > 0);
  if (nonZero.length === 1 && total > 0) {
    paths.push({ d: `M ${cx - r} ${cy} A ${r} ${r} 0 1 1 ${cx + r} ${cy} A ${r} ${r} 0 1 1 ${cx - r} ${cy} Z`, color: nonZero[0].color, label: nonZero[0].label, value: nonZero[0].value });
  }
  if (nonZero.length > 1) slices.forEach((slice) => {
    const value = Math.max(0, slice.value);
    if (value <= 0 || total <= 0) return;
    const sweep = (value / total) * Math.PI * 2;
    const gapAngle = Math.min(gap / r, sweep * 0.3);
    const start = angle + gapAngle / 2;
    const end = angle + sweep - gapAngle / 2;
    angle += sweep;
    if (end <= start) return;
    const x1 = cx + r * Math.cos(start);
    const y1 = cy + r * Math.sin(start);
    const x2 = cx + r * Math.cos(end);
    const y2 = cy + r * Math.sin(end);
    const large = sweep > Math.PI ? 1 : 0;
    const d = `M ${cx} ${cy} L ${x1} ${y1} A ${r} ${r} 0 ${large} 1 ${x2} ${y2} Z`;
    paths.push({ d, color: slice.color, label: slice.label, value });
  });

  return (
    <svg
      className="project-money-rings__pie"
      viewBox={`0 0 ${size} ${size}`}
      width={size}
      height={size}
      role="img"
      aria-label={title}
    >
      <circle cx={cx} cy={cy} r={r} fill={bg} />
      {paths.map((p, idx) => (
        <path
          key={idx}
          d={p.d}
          fill={p.color}
          className="project-money-rings__slice"
          style={{ animationDelay: `${idx * 60}ms` }}
        >
          <title>{`${p.label}: ${shortMoney(p.value)}`}</title>
        </path>
      ))}
    </svg>
  );
}

export function ProjectMoneyRings({ projectId, onOpenFinance }: { projectId: number; onOpenFinance?: () => void }) {
  const [data, setData] = useState<BudgetFromDds | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId) return;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    setData(null);
    (async () => {
      try {
        const res = (await api(`/projects/${projectId}/budget-from-dds`, { signal: controller.signal })) as BudgetFromDds;
        if (!controller.signal.aborted) {
          setData(res && res.income && res.expense_total ? res : null);
        }
      } catch (e) {
        if (!controller.signal.aborted) {
          setError(e instanceof Error ? e.message : "Не удалось загрузить данные");
        }
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
        }
      }
    })();
    return () => controller.abort();
  }, [projectId]);

  const header = (
    <div className="project-money-rings__header">
      <h3 className="project-money-rings__title">Деньги проекта</h3>
      {onOpenFinance && (
        <button type="button" className="project-money-rings__link" onClick={onOpenFinance}>
          Открыть ДДС
        </button>
      )}
    </div>
  );

  if (!projectId) return null;

  if (loading && !data) {
    return (
      <section className="project-money-rings">
        {header}
        <p className="project-money-rings__loading">Считаем деньги проекта…</p>
      </section>
    );
  }

  if (error) {
    return (
      <section className="project-money-rings">
        {header}
        <p role="alert" className="project-money-rings__error">{error}</p>
      </section>
    );
  }

  if (!data) return null;

  const incomeFact = num(data.income.fact);
  const incomePlan = num(data.income.plan);
  const expenseFact = num(data.expense_total.fact);
  const expensePlan = num(data.expense_total.plan);
  const expenseInvoiced = num(data.expense_total.invoiced);

  const months = Array.isArray(data.by_month) ? data.by_month : [];
  const now = new Date();
  const currentMonthKey = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;

  const incomePlanToDate = months
    // Only months that are over: money due later this month is not late yet (owner 21.10.2026).
    .filter((m) => m.month < currentMonthKey)
    .reduce((sum, m) => sum + num(m.income_plan), 0);

  const incomeOverdue = Math.max(0, incomePlanToDate - incomeFact);
  const incomeRemaining = Math.max(0, incomePlan - incomeFact - incomeOverdue);

  const incomeSlices: Slice[] = [
    { label: "Получено", value: incomeFact, color: "#22c55e" },
    { label: "Просрочено по графику", value: incomeOverdue, color: "#ef4444" },
    { label: "Ещё впереди", value: incomeRemaining, color: "#64748b" },
  ];

  const expenseInvoicedNotPaid = Math.max(0, expenseInvoiced - expenseFact);
  const expenseRemaining = Math.max(0, expensePlan - expenseFact - expenseInvoicedNotPaid);
  const expenseSlices: Slice[] = [
    { label: "Оплачено", value: expenseFact, color: "#3b82f6" },
    { label: "Выставлено, не оплачено", value: expenseInvoicedNotPaid, color: "#f59e0b" },
    { label: "Остаток плана", value: expenseRemaining, color: "#94a3b8" },
  ];

  const categoriesRaw = Array.isArray(data.expense_by_category) ? data.expense_by_category : [];
  const sortedCategories = [...categoriesRaw].sort((a, b) => num(b.plan) - num(a.plan));
  const top = sortedCategories.slice(0, 6);
  const tail = sortedCategories.slice(6);
  const tailPlan = tail.reduce((sum, row) => sum + num(row.plan), 0);
  const tailFact = tail.reduce((sum, row) => sum + num(row.fact), 0);
  const categoryRows: { category: string; plan: number; fact: number; color: string }[] = top.map((row, idx) => ({
    category: row.category,
    plan: num(row.plan),
    fact: num(row.fact),
    color: CATEGORY_COLORS[idx] ?? OTHER_COLOR,
  }));
  if (tail.length > 0) {
    categoryRows.push({ category: "Прочее", plan: tailPlan, fact: tailFact, color: OTHER_COLOR });
  }
  const categoryTotalPlan = categoryRows.reduce((sum, row) => sum + row.plan, 0);
  const categorySlices: Slice[] = categoryRows.map((row) => ({
    label: row.category,
    value: row.plan,
    color: row.color,
    extra: `${shortMoney(row.fact)} оплачено`,
  }));

  const incomePercent = percent(incomeFact, incomePlan > 0 ? incomePlan : incomeFact || 1);
  const expensePercent = expensePlan > 0 ? Math.round(percent(expenseFact, expensePlan)) : 0;

  const incomeStatusText = incomeOverdue > 0 ? `отстаём на ${shortMoney(incomeOverdue)}` : "по графику";
  const incomeStatusTone = incomeOverdue > 0 ? "negative" : "positive";

  const largest = categoryRows.reduce<{ row: (typeof categoryRows)[number] | null }>(
    (acc, row) => (acc.row === null || row.plan > acc.row.plan ? { row } : acc),
    { row: null },
  ).row;
  const categoryStatusText = largest && categoryTotalPlan > 0
    ? `крупнейшая статья: ${largest.category} ${Math.round((largest.plan / categoryTotalPlan) * 100)}%`
    : "нет данных по статьям";

  const futureMonths = months.filter((m) => m.month >= currentMonthKey);
  const critical = futureMonths.reduce<{ month: string; value: number } | null>((acc, m) => {
    const v = num(m.cumulative_plan);
    if (acc === null || v < acc.value) return { month: m.month, value: v };
    return acc;
  }, null);

  const cash = incomeFact - expenseFact;
  const cashTone = cash >= 0 ? "positive" : "negative";

  return (
    <section className="project-money-rings">
      {header}
      <div className="project-money-rings__grid">
        <div className="project-money-rings__card">
          <div className="project-money-rings__card-title">Приходы от заказчика</div>
          <div className="project-money-rings__card-body">
            <PieChart slices={incomeSlices} title="Приходы от заказчика" />
            <div className="project-money-rings__legend">
              {incomeSlices.map((slice) => (
                <div key={slice.label} className="project-money-rings__legend-row">
                  <span className="project-money-rings__legend-swatch" style={{ background: slice.color }} aria-hidden="true" />
                  <span className="project-money-rings__legend-label">{slice.label}</span>
                  <span className="project-money-rings__legend-value">
                    {shortMoney(slice.value)}
                    <small>{Math.round(percent(slice.value, incomePlan || incomeFact || 1))}%</small>
                  </span>
                </div>
              ))}
            </div>
          </div>
          <div className="project-money-rings__card-note">
            План: {shortMoney(incomePlan)} · получено {Math.round(incomePercent)}%
          </div>
          <div className={`project-money-rings__card-status project-money-rings__card-status--${incomeStatusTone}`}>
            {incomeStatusText}
          </div>
        </div>

        <div className="project-money-rings__card">
          <div className="project-money-rings__card-title">Расходы по ДДС</div>
          <div className="project-money-rings__card-body">
            <PieChart slices={expenseSlices} title="Расходы по ДДС" />
            <div className="project-money-rings__legend">
              {expenseSlices.map((slice) => (
                <div key={slice.label} className="project-money-rings__legend-row">
                  <span className="project-money-rings__legend-swatch" style={{ background: slice.color }} aria-hidden="true" />
                  <span className="project-money-rings__legend-label">{slice.label}</span>
                  <span className="project-money-rings__legend-value">
                    {shortMoney(slice.value)}
                    <small>{Math.round(percent(slice.value, expensePlan || expenseFact || 1))}%</small>
                  </span>
                </div>
              ))}
            </div>
          </div>
          <div className="project-money-rings__card-note">
            План: {shortMoney(expensePlan)} · оплачено {expensePercent}% плана
          </div>
          {expenseFact > expensePlan && expensePlan > 0 && (
            <div className="project-money-rings__overrun">перерасход {shortMoney(expenseFact - expensePlan)}</div>
          )}
        </div>

        <div className="project-money-rings__card">
          <div className="project-money-rings__card-title">Структура расходов</div>
          <div className="project-money-rings__card-body">
            <PieChart slices={categorySlices} title="Структура расходов" />
            <div className="project-money-rings__legend">
              {categoryRows.map((row) => (
                <div key={row.category} className="project-money-rings__legend-row">
                  <span className="project-money-rings__legend-swatch" style={{ background: row.color }} aria-hidden="true" />
                  <span className="project-money-rings__legend-label">{row.category}</span>
                  <span className="project-money-rings__legend-value">
                    {shortMoney(row.plan)}
                    <small>{Math.round(percent(row.plan, categoryTotalPlan || 1))}% · {shortMoney(row.fact)}</small>
                  </span>
                </div>
              ))}
            </div>
          </div>
          <div className="project-money-rings__card-status project-money-rings__card-status--neutral">
            {categoryStatusText}
          </div>
        </div>

        <div className="project-money-rings__card project-money-rings__cash">
          <div className="project-money-rings__cash-label">Деньги сейчас</div>
          <div className={`project-money-rings__cash-value project-money-rings__cash-value--${cashTone}`}>
            {shortMoney(cash)}
          </div>
          <div className="project-money-rings__cash-caption">получено − оплачено</div>
          <div className="project-money-rings__cash-note">
            {critical
              ? (
                <span className={critical.value < 0 ? "project-money-rings__cash-critical--negative" : "project-money-rings__cash-critical--positive"}>
                  Критическая точка: {monthName(critical.month)} {shortMoney(critical.value)}
                  {critical.value < 0 ? "" : " · кассового разрыва по плану нет"}
                </span>
              )
              : "Критическая точка: нет данных"}
          </div>
        </div>
      </div>
    </section>
  );
}
