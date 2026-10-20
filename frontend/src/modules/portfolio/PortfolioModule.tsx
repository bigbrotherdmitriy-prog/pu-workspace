import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { formatMoney } from "../../utils/numberFormat";
import "./portfolio.css";

type PortfolioStatus = "risk" | "attention" | "ok";

type PortfolioProject = {
  project_id: number;
  name: string;
  currency: string;
  plan_inflow: string;
  plan_outflow: string;
  received_inflow: string;
  paid_outflow: string;
  overdue_count: number;
  overdue_amount: string;
  invoices_to_confirm: number;
  cash_gap_month: string | null;
  cash_gap_amount: string | null;
  cash_gap_explanation: string | null;
  status: PortfolioStatus;
};

type PortfolioTotals = {
  projects: number;
  risk: number;
  attention: number;
  overdue_amount: string;
  invoices_to_confirm: number;
};

type PortfolioResponse = {
  projects: PortfolioProject[];
  totals: PortfolioTotals;
};

const statusLabels: Record<PortfolioStatus, string> = {
  risk: "Риск",
  attention: "Внимание",
  ok: "В норме",
};

function formatMonth(month: string): string {
  const formatted = new Intl.DateTimeFormat("ru-RU", {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${month}-01T00:00:00Z`));
  return formatted.replace(/\s*г\.$/, "");
}

export function PortfolioModule({ onOpenProject }: { onOpenProject: (projectId: number) => void }) {
  const [data, setData] = useState<PortfolioResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api<PortfolioResponse>("/portfolio")
      .then((response) => {
        if (!cancelled) {
          setData(response);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err));
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) {
    return <p>Загружаем портфель…</p>;
  }

  if (error) {
    return <p role="alert">{error}</p>;
  }

  const projects = data?.projects ?? [];
  const totals = data?.totals ?? { projects: 0, risk: 0, attention: 0, overdue_amount: "0", invoices_to_confirm: 0 };

  return (
    <section>
      <h2>Портфель проектов</h2>
      <p>
        Проектов: {totals.projects} · В риске: {totals.risk} · Требуют внимания: {totals.attention} · Просрочено счетов на: {formatMoney(totals.overdue_amount)} ₽ · Счетов на подтверждение: {totals.invoices_to_confirm}
      </p>
      {projects.length === 0 ? (
        <p>Нет активных проектов</p>
      ) : (
        <div className="portfolio-grid">
          {projects.map((project) => (
            <article key={project.project_id} className={`portfolio-card portfolio-${project.status}`}>
              <header className="portfolio-card-header">
                <h3>{project.name}</h3>
                <span className={`portfolio-badge portfolio-badge-${project.status}`}>{statusLabels[project.status]}</span>
              </header>
              <p>
                План: приход {formatMoney(project.plan_inflow)} / расход {formatMoney(project.plan_outflow)} {project.currency}
              </p>
              <p>
                Факт: получено {formatMoney(project.received_inflow)} / оплачено {formatMoney(project.paid_outflow)}
              </p>
              {project.cash_gap_month ? (
                <>
                  <p>
                    Кассовый разрыв: {formatMonth(project.cash_gap_month)} — {formatMoney(project.cash_gap_amount ?? "0")}
                  </p>
                  {project.cash_gap_explanation ? (
                    <p className="portfolio-card-note">{project.cash_gap_explanation}</p>
                  ) : null}
                </>
              ) : null}
              {project.overdue_count > 0 ? (
                <p>
                  Просрочено счетов: {project.overdue_count} на {formatMoney(project.overdue_amount)}
                </p>
              ) : null}
              {project.invoices_to_confirm > 0 ? (
                <p>Счетов на подтверждение: {project.invoices_to_confirm}</p>
              ) : null}
              <button
                type="button"
                aria-label={`Открыть проект ${project.name}`}
                onClick={() => onOpenProject(project.project_id)}
              >
                Открыть проект
              </button>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
