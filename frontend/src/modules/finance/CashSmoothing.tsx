import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { formatMoney } from "../../utils/numberFormat";
import "./cash-smoothing.css";

type SmoothingMove = {
  entry_id: number;
  title: string;
  direction: "inflow" | "outflow";
  amount: string;
  from: string;
  to: string;
  days: number;
};

type SmoothingVariant = {
  key: "delay_payments" | "pull_receipts" | "combined";
  title: string;
  min_balance: string;
  min_date: string | null;
  improvement: string;
  moves: SmoothingMove[];
  moves_count: number;
  max_shift_days: number;
  resolved: boolean;
};

type SmoothingBefore = {
  min_balance: string;
  min_date: string | null;
  negative_days: number;
};

type SmoothingResponse = {
  ok: boolean;
  before: SmoothingBefore;
  variants: SmoothingVariant[];
};

const dateFormat = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric", timeZone: "UTC" });

function formatDate(value: string | null) {
  if (!value) return "—";
  return dateFormat.format(new Date(`${value.slice(0, 10)}T00:00:00Z`));
}

export function CashSmoothing({ projectId, currency }: { projectId: number; currency: string }) {
  const [data, setData] = useState<SmoothingResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    setData(null);
    api<SmoothingResponse>(`/projects/${projectId}/cash-flow/smoothing`)
      .then((result) => { if (!cancelled) setData(result); })
      .catch((caught) => { if (!cancelled) setError((caught as Error).message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [projectId]);

  if (loading) return <section className="cash-smoothing"><p>Загружаем…</p></section>;
  if (error) return <section className="cash-smoothing"><p role="alert">{error}</p></section>;
  if (!data) return <section className="cash-smoothing" />;

  if (data.ok) {
    return <section className="cash-smoothing">
      <p>Кассового разрыва нет — минимальный остаток {formatMoney(Number(data.before.min_balance))} ({formatDate(data.before.min_date)})</p>
      <p className="cash-smoothing-note">Это сценарий-предложение. Плановый ДДС не изменяется.</p>
    </section>;
  }

  return <section className="cash-smoothing">
    <h3>Как смягчить кассовый разрыв</h3>
    <p>Сейчас: минимальный остаток {formatMoney(Number(data.before.min_balance))} {currency} на {formatDate(data.before.min_date)}, дней в минусе: {data.before.negative_days}</p>
    {data.variants.length === 0
      ? <p className="dds-empty">Подходящих переносов не найдено: подвижных платежей и поступлений нет</p>
      : <div className="cash-smoothing-variants">{data.variants.map((variant) => <div className="cash-smoothing-card" key={variant.key}>
        <div className="cash-smoothing-card-head">
          <h4>{variant.title}</h4>
          <span className={`cash-smoothing-badge ${variant.resolved ? "resolved" : "partial"}`}>{variant.resolved ? "Закрывает разрыв" : "Уменьшает разрыв"}</span>
        </div>
        <p>Минимум после переноса: {formatMoney(Number(variant.min_balance))}</p>
        <p>Улучшение: +{formatMoney(Number(variant.improvement))}</p>
        <p>Переносов: {variant.moves_count}, максимум на {variant.max_shift_days} дн.</p>
        {variant.moves.length > 0 && <details className="cash-smoothing-moves">
          <summary>Показать переносы</summary>
          <div className="dds-table-wrap"><table className="dds-table">
            <thead><tr><th>Платёж/поступление</th><th>Сумма, {currency}</th><th>Было → стало</th><th>±дней</th></tr></thead>
            <tbody>{variant.moves.map((move) => <tr key={move.entry_id}>
              <td>{move.title}</td>
              <td className="money">{formatMoney(Number(move.amount))}</td>
              <td>{formatDate(move.from)} → {formatDate(move.to)}</td>
              <td>{move.days > 0 ? `+${move.days}` : move.days}</td>
            </tr>)}</tbody>
          </table></div>
        </details>}
      </div>)}</div>}
    <p className="cash-smoothing-note">Это сценарий-предложение. Плановый ДДС не изменяется.</p>
  </section>;
}
