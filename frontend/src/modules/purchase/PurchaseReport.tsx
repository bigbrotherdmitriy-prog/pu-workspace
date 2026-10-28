import { useEffect, useState } from "react";
import { api } from "../../api/client";
import "./purchase.css";

type ReportItem = {
  id: number;
  name: string;
  type_mark: string | null;
  unit: string | null;
  needed: string;
  ordered: string;
  paid: string;
  left: string;
  to_pay: string;
  status: string;
};

type ReportTotals = {
  positions: number;
  fully_ordered: number;
  fully_paid: number;
  needed_sum: string;
  ordered_sum: string;
  paid_sum: string;
};

type ReportResponse = {
  items: ReportItem[];
  totals: ReportTotals;
  unmatched_lines: number;
};

type Props = { projectId: number; reloadKey?: number };

const emptyTotals: ReportTotals = {
  positions: 0,
  fully_ordered: 0,
  fully_paid: 0,
  needed_sum: "0",
  ordered_sum: "0",
  paid_sum: "0",
};

function isPositive(value: string | null | undefined): boolean {
  if (!value) return false;
  const num = Number(value);
  return Number.isFinite(num) && num > 0;
}

export function PurchaseReport({ projectId, reloadKey }: Props) {
  const [items, setItems] = useState<ReportItem[]>([]);
  const [totals, setTotals] = useState<ReportTotals>(emptyTotals);
  const [unmatched, setUnmatched] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (projectId <= 0) { setItems([]); setTotals(emptyTotals); setUnmatched(0); return; }
    let alive = true;
    const controller = new AbortController();
    setLoading(true);
    api<ReportResponse>(`/projects/${projectId}/purchase/report`, { signal: controller.signal })
      .then((response) => {
        if (!alive) return;
        setItems(Array.isArray(response?.items) ? response.items : []);
        setTotals(response?.totals ?? emptyTotals);
        setUnmatched(typeof response?.unmatched_lines === "number" ? response.unmatched_lines : 0);
        setError("");
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (!alive) return;
        setError(err instanceof Error ? err.message : "Не удалось загрузить отчёт");
      })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; controller.abort(); };
  }, [projectId, reloadKey]);

  return (
    <div className="pr-report">
      {error && <div className="pr-error" role="alert">{error}</div>}

      <div className="pr-report-totals">
        <div className="pr-report-counts">
          Позиций {totals.positions} · заказано полностью {totals.fully_ordered} · оплачено полностью {totals.fully_paid}
        </div>
        <div className="pr-report-sums">
          <span className="pr-report-sum">
            <span className="pr-report-sum-label">Нужно</span>
            <span className="pr-report-sum-value">{totals.needed_sum}</span>
          </span>
          <span className="pr-report-sum">
            <span className="pr-report-sum-label">Заказано</span>
            <span className="pr-report-sum-value">{totals.ordered_sum}</span>
          </span>
          <span className="pr-report-sum">
            <span className="pr-report-sum-label">Оплачено</span>
            <span className="pr-report-sum-value">{totals.paid_sum}</span>
          </span>
        </div>
      </div>

      {unmatched > 0 && (
        <div className="pr-report-warn" role="alert">
          Не сопоставлено строк счетов: {unmatched}
        </div>
      )}

      {loading ? (
        <div className="pr-empty">Загрузка…</div>
      ) : items.length === 0 ? (
        <div className="pr-empty">Пока нет данных для отчёта</div>
      ) : (
        <div className="pr-table">
          <table>
            <thead>
              <tr>
                <th>Наименование</th>
                <th>Ед.</th>
                <th>Нужно</th>
                <th>Заказано</th>
                <th>Оплачено</th>
                <th>Осталось заказать</th>
                <th>К оплате</th>
                <th>Статус</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => {
                const isLeft = isPositive(item.left);
                return (
                  <tr key={item.id} className={isLeft ? "pr-report-row-left" : ""}>
                    <td>
                      <div className="pr-name">{item.name || "—"}</div>
                      {item.type_mark && <div className="pr-sub">{item.type_mark}</div>}
                    </td>
                    <td>{item.unit || "—"}</td>
                    <td className="pr-num">{item.needed || "—"}</td>
                    <td className="pr-num">{item.ordered || "—"}</td>
                    <td className="pr-num">{item.paid || "—"}</td>
                    <td className={`pr-num${isLeft ? " pr-report-left" : ""}`}>
                      {isLeft ? `Осталось заказать ${item.left}` : item.left || "—"}
                    </td>
                    <td className="pr-num">{item.to_pay || "—"}</td>
                    <td className="pr-report-status">{item.status || "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default PurchaseReport;
