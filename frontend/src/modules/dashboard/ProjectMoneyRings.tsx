import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { formatMoney } from "../../utils/numberFormat";
import "./project-money-rings.css";

type Pair = { plan: string; fact: string };
type ExpenseTotal = { plan: string; fact: string; invoiced: string };
type MarginPair = { plan: string; fact: string };
export type BudgetFromDds = {
  income: Pair;
  expense_total: ExpenseTotal;
  profit: Pair;
  margin_pct: MarginPair;
};

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
          // An unexpected payload must not take the whole work center down.
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

  const fmt = (x: string | number | null | undefined) => {
    const n = Number(x);
    return formatMoney(Number.isFinite(n) ? Math.round(n) : 0);
  };

  const header = (
    <div className="project-money-rings__header">
      <h3 className="project-money-rings__title">Деньги проекта</h3>
      {onOpenFinance && (
        <button type="button" className="project-money-rings__link" onClick={onOpenFinance}>
          Открыть бюджет
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

  const incomeFact = Number(data.income.fact) || 0;
  const incomePlan = Number(data.income.plan) || 0;
  const expenseFact = Number(data.expense_total.fact) || 0;
  const expensePlan = Number(data.expense_total.plan) || 0;
  const expenseInvoiced = Number(data.expense_total.invoiced) || 0;

  const cash = incomeFact - expenseFact;
  const cashTone = cash >= 0 ? "positive" : "negative";

  return (
    <section className="project-money-rings">
      {header}
      <div className="project-money-rings__grid">
        <Ring
          label="Получено от заказчика"
          value={incomeFact}
          total={incomePlan}
          caption={`${fmt(incomeFact)} из ${fmt(incomePlan)}`}
          tone="good"
        />
        <Ring
          label="Оплачено расходов"
          value={expenseFact}
          total={expensePlan}
          caption={`${fmt(expenseFact)} из ${fmt(expensePlan)}`}
          tone="neutral"
        />
        <Ring
          label="Счета оплачены"
          value={expenseFact}
          total={expenseInvoiced}
          caption={expenseInvoiced > 0 ? `оплачено ${fmt(expenseFact)} из выставленных ${fmt(expenseInvoiced)}` : "Счетов пока нет"}
          tone="warn"
        />
        <div className="project-money-rings__card project-money-rings__cash">
          <div className="project-money-rings__cash-label">Деньги сейчас</div>
          <div className={`project-money-rings__cash-value project-money-rings__cash-value--${cashTone}`}>
            {fmt(cash)}
          </div>
          <div className="project-money-rings__cash-caption">получено − оплачено</div>
          <div className="project-money-rings__cash-note">
            Прибыль по плану: {fmt(data.profit?.plan ?? "0")} ({data.margin_pct?.plan ?? "—"}%)
          </div>
        </div>
      </div>
    </section>
  );
}

function Ring({ label, value, total, caption, tone }: {
  label: string;
  value: number;
  total: number;
  caption: string;
  tone: "good" | "neutral" | "warn";
}) {
  const ratioRaw = total > 0 ? value / total : 0;
  const ratio = Math.max(0, Math.min(1, ratioRaw));
  const percent = Math.round(ratio * 100);
  const radius = 42;
  const circumference = 2 * Math.PI * radius;
  const dash = circumference * ratio;
  const gap = circumference - dash;

  return (
    <div className={`project-money-rings__card project-money-rings__ring project-money-rings__ring--${tone}`}>
      <div
        className="project-money-rings__chart"
        role="img"
        aria-label={`${label}: ${percent}%`}
      >
        <svg viewBox="0 0 100 100" width="120" height="120" aria-hidden="true">
          <circle
            cx="50"
            cy="50"
            r={radius}
            fill="none"
            stroke="var(--ring-track, #e5e7eb)"
            strokeWidth="10"
          />
          <circle
            className="project-money-rings__arc"
            cx="50"
            cy="50"
            r={radius}
            fill="none"
            strokeWidth="10"
            strokeLinecap="round"
            strokeDasharray={`${dash} ${gap}`}
            strokeDashoffset="0"
            transform="rotate(-90 50 50)"
          />
        </svg>
        <div className="project-money-rings__percent">{percent}%</div>
      </div>
      <div className="project-money-rings__label">{label}</div>
      <div className="project-money-rings__caption">{caption}</div>
    </div>
  );
}
