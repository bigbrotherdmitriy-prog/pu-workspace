import { useMemo } from "react";
import { formatMoney } from "../../utils/numberFormat";
import { buildFinanceForecastView, type FinanceForecastRollup, type ForecastMoney } from "./financeForecastModel";
import "./financeForecast.css";

type Props = { overview: unknown; projectId: number };
const metricNames: Array<[keyof ForecastMoney, string]> = [
  ["planned", "План"], ["committed", "Законтрактовано"], ["actual", "Факт"], ["forecast", "Прогноз"], ["variance", "Отклонение"],
];

function RollupTable({ title, rows, hierarchical = false }: { title: string; rows: FinanceForecastRollup[]; hierarchical?: boolean }) {
  return <section className="finance-forecast-rollup"><h3>{title}</h3>{rows.length ? <div className="finance-forecast-table"><table>
    <thead><tr><th>Контур</th>{metricNames.map(([, label]) => <th key={label}>{label}, RUB</th>)}</tr></thead>
    <tbody>{rows.map((row) => <tr key={row.key} className={row.isSummary ? "summary" : undefined}>
      <th scope="row"><span style={hierarchical ? { paddingInlineStart: `${(row.level ?? 0) * 18}px` } : undefined}>{row.label}</span>
        {hierarchical && <small>{row.isSummary ? "Сводный узел" : row.contractId === null ? "Без договора" : `Договор #${row.contractId}`}</small>}</th>
      {metricNames.map(([field]) => <td key={field}>{formatMoney(row[field])}</td>)}</tr>)}</tbody>
  </table></div> : <p className="finance-forecast-empty">Нет подтверждённых строк для расчёта.</p>}</section>;
}

export function FinanceForecastPanel({ overview, projectId }: Props) {
  const result = useMemo(() => {
    try { return { view: buildFinanceForecastView(overview, projectId), error: null }; }
    catch { return { view: null, error: "Финансовый прогноз скрыт: данные не прошли проверку целостности." }; }
  }, [overview, projectId]);
  if (!result.view) return <section className="finance-forecast-panel finance-forecast-error" role="alert">{result.error}</section>;
  const { view } = result;
  return <section className="finance-forecast-panel" aria-labelledby="finance-forecast-title">
    <header><div><span className="eyebrow">ФИНАНСОВЫЙ ПРОГНОЗ</span><h2 id="finance-forecast-title">План → обязательства → факт → прогноз</h2>
      <p>Только подтверждённые строки в RUB. Конвертация валют и платёжные действия не выполняются.</p></div>
      <strong className={`finance-forecast-reliability ${view.reliability}`}>{view.reliability === "reliable" ? "Надёжно" : view.reliability === "decision_required" ? "Требуется решение" : "Неполные данные"}</strong>
    </header>
    <p className="finance-forecast-reliability-note" role={view.reliability === "reliable" ? undefined : "alert"}>{view.reliabilityMessage}</p>
    <div className="finance-forecast-metrics">{metricNames.map(([field, label]) => <article key={field}><span>{label}</span><strong>{formatMoney(view.totals[field])}</strong></article>)}</div>
    <article className={view.cashGap < 0 ? "finance-forecast-gap negative" : "finance-forecast-gap"}><span>Кассовый разрыв</span><strong>{formatMoney(view.cashGap)}</strong>
      <small>{view.cashGap < 0 ? view.cashGapDate ? `Первая дата: ${view.cashGapDate}` : "Дата не определена" : "По доступным данным не выявлен"}</small></article>
    {view.decisions.length > 0 && <section className="finance-forecast-decisions" aria-label="Требуемые решения"><h3>До использования итогов</h3><ul>{view.decisions.map((item) => <li key={`${item.code}:${item.decisionBy}`}><b>{item.decisionBy}</b><span>{item.message}</span><code>{item.code}</code></li>)}</ul></section>}
    <RollupTable title="По договорам" rows={view.contracts} />
    <RollupTable title="По структуре ГПР / WBS" rows={view.wbs} hierarchical />
    <footer>Прогноз информационный. Автоплатежи, проводки и автоматическая конвертация отключены.</footer>
  </section>;
}
