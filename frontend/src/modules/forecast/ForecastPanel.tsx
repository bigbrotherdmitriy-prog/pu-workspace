import type { ForecastReport, ForecastSource } from "./types";
import "./forecast.css";

type Props = {
  report: ForecastReport | null;
  state?: "idle" | "loading" | "ready" | "error";
  error?: string | null;
  acknowledgedForecastId?: string | null;
  onReload?: () => void;
  onAcknowledge?: (forecastId: string) => void;
};

const rub = new Intl.NumberFormat("ru-RU", { style: "currency", currency: "RUB", maximumFractionDigits: 2 });
const money = (value: string) => rub.format(Number(value));
const confidence = (value: number) => `${Math.round(value * 100)}%`;

function SourceList({ sources }: { sources: ForecastSource[] }) {
  return <ul className="forecast-sources" aria-label="Источники расчёта">
    {sources.map((source) => <li key={`${source.entity_type}:${source.entity_id}`}>
      <strong>{source.entity_type} #{source.entity_id}</strong>
      <span>{source.fields.join(", ")} · {source.state}</span>
      {source.evidence_exact
        ? source.evidence.map((pin) => <small key={pin.evidence_id}>
          Evidence {pin.evidence_id.slice(0, 12)} · {pin.verification}
          {pin.page !== undefined ? ` · стр. ${pin.page}` : ""}
          {pin.coordinates ? ` · [${pin.coordinates.join(", ")}]` : ""}
        </small>)
        : <small className="forecast-unverified">Точное Evidence не привязано</small>}
    </li>)}
  </ul>;
}

export function ForecastPanel({
  report,
  state = report ? "ready" : "idle",
  error,
  acknowledgedForecastId,
  onReload,
  onAcknowledge,
}: Props) {
  if (state === "idle") return <section className="forecast-panel forecast-state"><p>Выберите проект для расчёта прогноза.</p></section>;
  if (state === "loading") return <section className="forecast-panel forecast-state" aria-busy="true"><p role="status">Собираем факты ГПР, бюджета и ДДС…</p></section>;
  if (state === "error" || !report) return <section className="forecast-panel forecast-state" role="alert">
    <p>{error || "Не удалось построить прогноз."}</p>{onReload && <button type="button" onClick={onReload}>Повторить</button>}
  </section>;

  const acknowledged = acknowledgedForecastId === report.forecast_id;
  return <section className="forecast-panel" aria-labelledby="forecast-title">
    <header className="forecast-header"><div><span className="forecast-eyebrow">ОБЪЯСНИМЫЙ ПРОГНОЗ</span>
      <h2 id="forecast-title">Сроки и денежный поток</h2><p>Срез на {report.as_of}. Только совет: никаких автодействий.</p></div>
      <div className={`forecast-confidence ${report.confidence.band}`}><strong>{confidence(report.confidence.score)}</strong><span>уверенность</span></div>
    </header>

    {report.confidence.band === "low" && <div className="forecast-warning" role="alert">Низкая уверенность: проверьте факты и Evidence до любого решения.</div>}

    <div className="forecast-metrics">
      <article><span>Прогноз завершения</span><strong>{report.schedule.predicted_finish || "Нет данных"}</strong></article>
      <article><span>Прогноз бюджета</span><strong>{money(report.budget.forecast_total)}</strong><small>отклонение {money(report.budget.variance)}</small></article>
      <article className={Number(report.cash_flow.minimum_balance) < 0 ? "negative" : ""}><span>Минимум ДДС</span><strong>{money(report.cash_flow.minimum_balance)}</strong><small>{report.cash_flow.cash_gap_date || "без кассового разрыва"}</small></article>
    </div>

    <article className="forecast-section"><header><h3>Сроки по ГПР</h3><code>{report.schedule.formula}</code></header>
      {report.schedule.stages.length ? <div className="forecast-table"><table><thead><tr><th>Этап</th><th>План</th><th>Прогноз</th><th>Факт</th><th>Уверенность</th></tr></thead><tbody>{report.schedule.stages.map((stage) => <tr key={stage.id}><td><strong>{stage.title}</strong><details><summary>Формула и источники</summary><p>{stage.formula_description}</p><SourceList sources={stage.sources} /></details></td><td>{stage.planned_finish || "—"}</td><td>{stage.predicted_finish || "—"}</td><td>{stage.actual_progress}%</td><td>{confidence(stage.confidence)}</td></tr>)}</tbody></table></div> : <p>Нет утверждённой версии ГПР.</p>}
    </article>

    <div className="forecast-columns"><article className="forecast-section"><header><h3>Бюджет</h3><code>{report.budget.formula}</code></header>
      <ul className="forecast-list">{report.budget.lines.map((line) => <li key={line.id}><div><strong>{line.description}</strong><span>план {money(line.planned_amount)} → прогноз {money(line.forecast_amount)}</span></div><b>{confidence(line.confidence)}</b><details><summary>Источники</summary><code>{line.formula}</code><SourceList sources={line.sources} /></details></li>)}</ul>
    </article>
      <article className="forecast-section"><header><h3>ДДС</h3><code>{report.cash_flow.formula}</code></header>
        <ul className="forecast-list">{report.cash_flow.events.map((event) => <li key={event.id}><div><strong>{event.title}</strong><span>{event.date} · {event.direction === "inflow" ? "+" : "−"}{money(event.amount)} · {event.value_kind === "actual" ? "факт" : "план"}</span></div><b>{money(event.running_balance)}</b><details><summary>Источники</summary><SourceList sources={event.sources} /></details></li>)}</ul>
      </article></div>

    <article className="forecast-section forecast-risks"><h3>Причины риска</h3>{report.risks.length
      ? <ul>{report.risks.map((risk, index) => <li className={`severity-${risk.severity}`} key={`${risk.code}:${index}`}><strong>{risk.code}</strong><p>{risk.explanation}</p><SourceList sources={risk.sources} /></li>)}</ul>
      : <p>По доступным данным риски не выявлены.</p>}</article>

    <footer className="forecast-confirm"><div><strong>Требуется проверка человека</strong><p>Прогноз — черновик. Эта кнопка лишь передаёт точный ID версии внешнему обработчику; сам модуль ничего не публикует.</p></div>
      <button type="button" disabled={!onAcknowledge || acknowledged} onClick={() => onAcknowledge?.(report.forecast_id)}>{acknowledged ? "Ознакомление зафиксировано" : "Подтвердить ознакомление"}</button>
    </footer>
  </section>;
}
