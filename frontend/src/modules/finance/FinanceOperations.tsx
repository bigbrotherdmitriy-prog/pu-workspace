import type { Dispatch, SetStateAction } from "react";
import type { FinanceOverview, FinanceStructuredPreview } from "./types";
import { formatMoney } from "../../utils/numberFormat";

type Props = {
  finance: FinanceOverview | null; preview: FinanceStructuredPreview | null; selectedRows: number[];
  setSelectedRows: Dispatch<SetStateAction<number[]>>; selectedContractId: number;
  kind: string; title: string; amount: string; date: string; extra: string;
  sourceDocumentId: number; scheduleItemId: number; budgetLineId: number;
  setKind: (value: string) => void; setTitle: (value: string) => void; setAmount: (value: string) => void;
  setDate: (value: string) => void; setExtra: (value: string) => void; setScheduleItemId: (value: number) => void;
  setBudgetLineId: (value: number) => void; onClosePreview: () => void; onImport: () => void; onAdd: () => void;
  onConfirm: (kind: string, id: number, status: string) => void; onConfirmPayment: (id: number, amount: number) => void;
  onCorrectPayment: (id: number, amount: number, date: string) => void;
  includeEditor?: boolean; includeRegisters?: boolean;
};

const money = formatMoney;

export function FinanceOperations(props: Props) {
  const { finance, preview, selectedRows, setSelectedRows, selectedContractId, kind, title, amount, date, extra,
    sourceDocumentId, scheduleItemId, budgetLineId, setKind, setTitle, setAmount, setDate, setExtra,
    setScheduleItemId, setBudgetLineId, onClosePreview, onImport, onAdd, onConfirm, onConfirmPayment, onCorrectPayment,
    includeEditor = true, includeRegisters = true } = props;
  const filterContract = <T extends { contract_id?: number }>(rows: T[] | undefined) => rows?.filter((item) => !selectedContractId || item.contract_id === selectedContractId) || [];
  return <>
    {includeEditor && preview && <section className="card structured-import" id="structured-import">
      <div className="card-head"><div><span className="eyebrow">ПАКЕТНОЕ ПРЕДЛОЖЕНИЕ</span><h2>{preview.name}</h2><p>Сопоставлено колонок: {Object.keys(preview.mapping).length}. Выберите строки; импорт создаст предложения со ссылкой на строку источника.</p></div><button className="secondary" onClick={onClosePreview}>Закрыть</button></div>
      {preview.issues.map((issue) => <p className="finance-warning" key={issue}>{issue}</p>)}
      <div className="structured-table"><table><thead><tr><th></th><th>Строка</th><th>Наименование</th><th>Дата / срок</th><th>Сумма / прогресс</th><th>Проверка</th></tr></thead><tbody>{preview.rows.slice(0, 100).map((row) => <tr className={row.importable ? "" : "invalid"} key={row.source_row}><td><input type="checkbox" disabled={!row.importable} checked={selectedRows.includes(row.source_row)} onChange={(event) => setSelectedRows((current) => event.target.checked ? [...current, row.source_row] : current.filter((value) => value !== row.source_row))} /></td><td>{row.source_row}</td><td>{row.title || "—"}<small>{row.category}</small></td><td>{row.planned_date || row.planned_finish || row.planned_start || "—"}</td><td>{row.amount ? money(Number(row.amount)) : `${row.progress || 0}%`}</td><td>{row.issues.length ? row.issues.join("; ") : "готово к предложению"}</td></tr>)}</tbody></table></div>
      {preview.truncated && <p className="finance-warning">Показаны первые 500 строк. Разделите файл или импортируйте его частями.</p>}
      <div className="structured-actions"><span>Выбрано строк: <strong>{selectedRows.length}</strong></span><button disabled={!selectedRows.length} onClick={onImport}>Создать пакет предложений</button></div>
    </section>}
    {includeEditor && <section className="card finance-entry" id="finance-entry">
      <div><h2>Добавить управленческую запись</h2><p>Новая запись создаётся как предложение и не влияет на подтверждённый прогноз.</p>{sourceDocumentId > 0 && <p className="finance-source-note">Источник: документ #{sourceDocumentId}. Связь сохранится для счёта или акта.</p>}</div>
      <div><select value={kind} onChange={(event) => setKind(event.target.value)}><option value="budget">Строка бюджета</option><option value="cash-in">Поступление ДДС</option><option value="cash-out">Выплата ДДС</option><option value="invoice">Счёт → предложение ДДС</option><option value="procurement">Закупка / поставка</option><option value="act">Акт</option><option value="baseline">Версия ГПР</option><option value="schedule">Этап ГПР</option></select>
        <input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Название" />
        {kind !== "baseline" && <input type="number" min="0" value={amount} onChange={(event) => setAmount(event.target.value)} placeholder="Сумма, ₽" />}
        {["cash-in", "cash-out", "invoice", "procurement", "act"].includes(kind) && <input type="date" value={date} onChange={(event) => setDate(event.target.value)} />}
        <input value={extra} onChange={(event) => setExtra(event.target.value)} placeholder={kind === "budget" ? "Категория" : kind === "act" ? "Номер акта" : kind === "baseline" ? "Комментарий" : kind === "schedule" ? "Комментарий к этапу" : "Контрагент / поставщик"} />
        {kind === "invoice" && <><select value={scheduleItemId} onChange={(event) => setScheduleItemId(Number(event.target.value))}><option value={0}>Связать с этапом ГПР (не выбран)</option>{finance?.schedule.filter((stage) => { const baseline = finance.baselines.find((row) => row.id === stage.baseline_id); return !selectedContractId || baseline?.contract_id === selectedContractId; }).map((stage) => <option key={stage.id} value={stage.id}>{stage.title}</option>)}</select><select value={budgetLineId} onChange={(event) => setBudgetLineId(Number(event.target.value))}><option value={0}>Связать со строкой бюджета (не выбрана)</option>{filterContract(finance?.budget).map((row) => <option key={row.id} value={row.id}>{row.description}</option>)}</select></>}
        <button disabled={!title.trim()} onClick={onAdd}>Создать предложение</button>
      </div>
    </section>}
    {includeRegisters && <section className="finance-grid">
      <article className="card"><h2>ГПР: план / факт</h2><div className="finance-list">{filterContract(finance?.baselines).map((item) => <div key={item.id}><span><strong>{item.name}</strong><small>Версия {item.version}</small></span><b>{item.status}</b>{item.status === "draft" && <button onClick={() => onConfirm("baselines", item.id, "approved")}>Утвердить baseline</button>}</div>)}{!filterContract(finance?.baselines).length && <p className="finance-empty">Добавьте первую версию ГПР.</p>}</div><p className="finance-warning">Отстающих работ: {finance?.summary.delayed_schedule || 0}</p></article>
      <article className="card"><h2>Бюджет</h2><div className="finance-list">{filterContract(finance?.budget).map((item) => <div key={item.id}><span><strong>{item.description}</strong><small>{item.category} · план {money(item.planned_amount)} · законтрактовано {money(item.committed_amount)} · факт {money(item.actual_amount)} · прогноз {money(item.forecast_amount)}</small></span><b>{item.status}</b>{item.status === "proposed" && <button onClick={() => onConfirm("budget", item.id, "approved")}>Подтвердить</button>}</div>)}{!filterContract(finance?.budget).length && <p className="finance-empty">Строк бюджета пока нет.</p>}</div></article>
      <article className="card"><h2>ДДС</h2><div className="finance-list">{filterContract(finance?.cash_flow).map((item) => <div key={item.id}><span><strong>{item.title}</strong><small>{item.direction === "inflow" ? "Поступление" : "Выплата"} · {item.planned_date} · {money(item.planned_amount)}{item.actual_date ? ` · факт ${item.actual_date}, ${money(item.actual_amount)}` : ""}</small></span><b>{item.status}</b>{item.status === "proposed" && <button onClick={() => onConfirm("cash-flow", item.id, "approved")}>Подтвердить</button>}{item.status === "approved" && <button onClick={() => onConfirmPayment(item.id, Number(item.planned_amount))}>Подтвердить оплату</button>}{["paid", "received"].includes(item.status) && item.actual_date && <button className="secondary" onClick={() => onCorrectPayment(item.id, Number(item.actual_amount), item.actual_date || "")}>Исправить факт оплаты</button>}</div>)}{!filterContract(finance?.cash_flow).length && <p className="finance-empty">План ДДС пока пуст.</p>}</div></article>
      <article className="card"><h2>Закупки и поставки</h2><div className="finance-list">{filterContract(finance?.procurement).map((item) => <div key={item.id}><span><strong>{item.title}</strong><small>{item.supplier || "Поставщик не указан"} · {item.planned_delivery || "без срока"} · {money(item.planned_amount)}</small></span><b>{item.stage}</b>{item.stage === "request" && <button onClick={() => onConfirm("procurement", item.id, "ordered")}>Заказано</button>}</div>)}{!filterContract(finance?.procurement).length && <p className="finance-empty">Закупок пока нет.</p>}</div><p className="finance-warning">Просроченных поставок: {finance?.summary.late_procurement || 0}</p></article>
      <article className="card"><h2>Акты и закрытие</h2><div className="finance-list">{filterContract(finance?.acts).map((item) => <div key={item.id}><span><strong>№{item.number} · {item.title}</strong><small>{item.act_date || "без даты"} · {money(item.amount)}</small></span><b>{item.status}</b>{item.status === "proposed" && <button onClick={() => onConfirm("acts", item.id, "approved")}>Подтвердить</button>}</div>)}{!filterContract(finance?.acts).length && <p className="finance-empty">Актов пока нет.</p>}</div></article>
      <article className="card finance-forecast"><h2>Прогноз</h2><p className={(finance?.summary.cash_gap || 0) < 0 ? "bad" : "good"}>{(finance?.summary.cash_gap || 0) < 0 ? `Ожидаемый кассовый разрыв ${money(finance?.summary.cash_gap)}${finance?.summary.cash_gap_date ? ` к ${finance.summary.cash_gap_date}` : ""}` : "Кассовый разрыв по подтверждённому плану не выявлен"}</p><p>Ожидают обработки актов: {finance?.summary.acts_pending || 0}</p><p>Прогноз бюджета: {money(finance?.summary.budget_forecast)}</p><p>Ожидают подтверждения оплаты: <strong>{finance?.summary.pending_payments || 0}</strong></p><p>Счета без полной цепочки: <strong>{finance?.summary.unlinked_invoices || 0}</strong></p></article>
    </section>}
  </>;
}
