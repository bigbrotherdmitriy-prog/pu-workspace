import { useEffect, useMemo, useState, type Dispatch, type SetStateAction } from "react";
import type { CostCategory, FinanceOverview, FinanceStructuredPreview, InvoiceExtractionProposal } from "./types";
import { formatMoney } from "../../utils/numberFormat";

type Props = {
  finance: FinanceOverview | null; preview: FinanceStructuredPreview | null; selectedRows: number[];
  setSelectedRows: Dispatch<SetStateAction<number[]>>; selectedContractId: number;
  kind: string; title: string; amount: string; date: string; extra: string; objectName: string; category: string; note: string;
  sourceDocumentId: number; scheduleItemId: number; budgetLineId: number; baselineId: number;
  setKind: (value: string) => void; setTitle: (value: string) => void; setAmount: (value: string) => void;
  setDate: (value: string) => void; setExtra: (value: string) => void; setScheduleItemId: (value: number) => void;
  setObjectName: (value: string) => void; setCategory: (value: string) => void; setNote: (value: string) => void;
  setBaselineId: (value: number) => void;
  setBudgetLineId: (value: number) => void; onClosePreview: () => void; onImport: () => void; onAdd: () => void;
  onEditPreviewRow?: (selectionId: number, patch: Record<string, string>) => void;
  onConfirm: (kind: string, id: number, status: string) => void; onConfirmPayment: (id: number, amount: number) => void;
  costCategories?: CostCategory[]; invoiceProposal?: InvoiceExtractionProposal | null;
  onEditInvoice?: (patch: Partial<InvoiceExtractionProposal>) => void;
  onConfirmInvoice?: () => void; onRejectInvoice?: () => void; onCloseInvoice?: () => void;
  onRetryInvoiceAi?: () => void; invoiceAiRetrying?: boolean;
  onAddCostCategory?: (name: string) => void;
  includeEditor?: boolean; includeRegisters?: boolean; includeScheduleRegister?: boolean; includeCashFlowRegister?: boolean;
  editorScope?: "all" | "gpr" | "finance";
};

const money = formatMoney;

export function FinanceOperations(props: Props) {
  const { finance, preview, selectedRows, setSelectedRows, selectedContractId, kind, title, amount, date, extra, objectName, category, note,
    sourceDocumentId, scheduleItemId, budgetLineId, baselineId, setKind, setTitle, setAmount, setDate, setExtra,
    setScheduleItemId, setBudgetLineId, setBaselineId, setObjectName, setCategory, setNote, onClosePreview, onImport, onAdd, onEditPreviewRow, onConfirm, onConfirmPayment,
    costCategories = [], invoiceProposal, onEditInvoice, onConfirmInvoice, onRejectInvoice, onCloseInvoice, onAddCostCategory,
    onRetryInvoiceAi, invoiceAiRetrying = false,
    includeEditor = true, includeRegisters = true, includeScheduleRegister = true, includeCashFlowRegister = true,
    editorScope = "all" } = props;
  const [newCategory, setNewCategory] = useState("");
  const [previewFilter, setPreviewFilter] = useState<"all" | "inflow" | "outflow" | "issues">("all");
  const editorKinds = useMemo(() => editorScope === "gpr" ? ["baseline", "schedule"] : editorScope === "finance" ? ["budget", "cash-in", "cash-out", "invoice", "procurement", "act"] : ["budget", "cash-in", "cash-out", "invoice", "procurement", "act", "baseline", "schedule"], [editorScope]);
  useEffect(() => {
    if (includeEditor && !editorKinds.includes(kind)) setKind(editorKinds[0]);
  }, [editorKinds, includeEditor, kind, setKind]);
  const filterContract = <T extends { contract_id?: number }>(rows: T[] | undefined) => rows?.filter((item) => !selectedContractId || item.contract_id === selectedContractId) || [];
  const contractBaselineIds = new Set(finance?.baselines.filter((row) => row.contract_id === selectedContractId).map((row) => row.id) || []);
  const contractSchedule = finance?.schedule.filter((row) => contractBaselineIds.has(row.baseline_id)) || [];
  const contractBudget = finance?.budget.filter((row) => row.contract_id === selectedContractId) || [];
  const importableRows = preview?.rows.filter((row) => row.importable) || [];
  const visiblePreviewRows = (preview?.rows || []).filter((row) =>
    previewFilter === "all" || (previewFilter === "issues" ? row.issues.length > 0 : row.direction === previewFilter)
  );
  const previewTotal = importableRows.filter((row) => selectedRows.includes(row.selection_id)).reduce((sum, row) => sum + Number(row.amount || 0), 0);
  const visibleIds = visiblePreviewRows.filter((row) => row.importable).map((row) => row.selection_id);
  const allVisibleSelected = visibleIds.length > 0 && visibleIds.every((id) => selectedRows.includes(id));
  useEffect(() => { setPreviewFilter("all"); }, [preview?.document_id]);
  return <>
    {includeEditor && invoiceProposal && <section className="card structured-import invoice-extraction-review" id="invoice-extraction-review">
      <div className="card-head"><div><span className="eyebrow">СЧЁТ · ПРЕДЛОЖЕНИЕ AI</span><h2>Проверьте данные перед импортом</h2><p>Ни одно поле не попадёт в бюджет или ДДС без подтверждения менеджером.</p></div><button className="secondary" onClick={onCloseInvoice}>Закрыть</button></div>
      {invoiceProposal.extraction_method === "regex" && <div className="finance-warning">AI недоступен ({invoiceProposal.fallback_reason || "fallback"}). Сумма найдена резервным правилом; заполните остальные поля вручную.{invoiceProposal.status === "proposed" && invoiceProposal.fallback_reason === "temporarily_unavailable" && <button className="secondary" disabled={invoiceAiRetrying} onClick={onRetryInvoiceAi}>{invoiceAiRetrying ? "Повторный анализ…" : "Повторить AI-анализ"}</button>}</div>}
      <div className="invoice-review-grid">
        <label>Сумма<input type="number" min="0.01" value={invoiceProposal.amount ?? ""} onChange={(event) => onEditInvoice?.({ amount: Number(event.target.value) || undefined })} /></label>
        <label>Валюта<input value={invoiceProposal.currency} disabled /></label>
        <label>Плановая дата<input type="date" value={invoiceProposal.planned_date || ""} onChange={(event) => onEditInvoice?.({ planned_date: event.target.value || undefined })} /></label>
        <label>Контрагент<input value={invoiceProposal.counterparty || ""} onChange={(event) => onEditInvoice?.({ counterparty: event.target.value })} /></label>
        <label className="wide">Назначение платежа<input value={invoiceProposal.payment_purpose || ""} onChange={(event) => onEditInvoice?.({ payment_purpose: event.target.value })} /></label>
        <label>Куда импортировать<select value={invoiceProposal.target_kind} onChange={(event) => onEditInvoice?.({ target_kind: event.target.value as "cash_flow" | "budget" })}><option value="cash_flow">ДДС</option><option value="budget">Бюджет</option></select></label>
        <label>Категория затрат<select aria-label="Категория затрат счёта" value={invoiceProposal.selected_cost_category_id || ""} onChange={(event) => onEditInvoice?.({ selected_cost_category_id: Number(event.target.value) || undefined })}><option value="">Выберите категорию</option>{costCategories.filter((item) => item.is_active).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        {invoiceProposal.target_kind === "cash_flow" && <>
          <label>Этап ГПР<select aria-label="Этап ГПР счёта" value={scheduleItemId} onChange={(event) => setScheduleItemId(Number(event.target.value))}><option value={0}>Выберите этап ГПР</option>{contractSchedule.map((row) => <option value={row.id} key={row.id}>{row.title}</option>)}</select></label>
          <label>Строка бюджета<select aria-label="Строка бюджета счёта" value={budgetLineId} onChange={(event) => setBudgetLineId(Number(event.target.value))}><option value={0}>Выберите строку бюджета</option>{contractBudget.map((row) => <option value={row.id} key={row.id}>{row.description}</option>)}</select></label>
        </>}
      </div>
      <div className="invoice-evidence">
        <strong>Основания из документа</strong>
        <small>Сумма: {invoiceProposal.amount_evidence_quote || "не найдено"}</small>
        <small>Контрагент: {invoiceProposal.counterparty_evidence_quote || "не найдено"}</small>
        <small>Назначение: {invoiceProposal.payment_purpose_evidence_quote || "не найдено"}</small>
        <small>Категория: {invoiceProposal.category_evidence_quote || "нет подтверждаемой цитаты"}</small>
      </div>
      <div className="structured-actions"><input value={newCategory} onChange={(event) => setNewCategory(event.target.value)} placeholder="Новая категория" /><button className="secondary" disabled={!newCategory.trim()} onClick={() => { onAddCostCategory?.(newCategory); setNewCategory(""); }}>Добавить в справочник</button></div>
      {invoiceProposal.status === "proposed" ? <div className="structured-actions"><button className="secondary" onClick={onRejectInvoice}>Отклонить</button><button disabled={!invoiceProposal.amount || !invoiceProposal.payment_purpose || !invoiceProposal.selected_cost_category_id || (invoiceProposal.target_kind === "cash_flow" && (!invoiceProposal.planned_date || !selectedContractId || !scheduleItemId || !budgetLineId))} onClick={onConfirmInvoice}>Подтвердить и создать предложение</button></div> : <p><strong>Статус: {invoiceProposal.status}</strong></p>}
    </section>}
    {includeEditor && preview && <section className="card structured-import structured-import-review" id="structured-import">
      <div className="card-head"><div><span className="eyebrow">ПРОВЕРКА ПЛАНОВОГО ДДС</span><h2>{preview.name}</h2><p>Проверьте операции. В ДДС они попадут как предложения и не повлияют на прогноз до вашего подтверждения.</p></div><button className="secondary" onClick={onClosePreview}>Отмена</button></div>
      {preview.issues.map((issue) => <p className="finance-warning" key={issue}>{issue}</p>)}
      {preview.layout === "monthly_matrix" ? <p className="muted">Месячный план ДДС за {preview.plan_year} год. Дата каждого платежа — конец месяца.{preview.inferred_december ? " Колонка после ноября распознана как декабрь." : ""}</p> : null}
      <div className="structured-summary"><div><span>Найдено</span><strong>{importableRows.length}</strong><small>операций</small></div><div><span>Приход</span><strong>{importableRows.filter((row) => row.direction === "inflow").length}</strong><small>{money(importableRows.filter((row) => row.direction === "inflow").reduce((sum, row) => sum + Number(row.amount || 0), 0))}</small></div><div><span>Расход</span><strong>{importableRows.filter((row) => row.direction === "outflow").length}</strong><small>{money(importableRows.filter((row) => row.direction === "outflow").reduce((sum, row) => sum + Number(row.amount || 0), 0))}</small></div><div><span>Выбрано</span><strong>{selectedRows.length}</strong><small>на {money(previewTotal)}</small></div></div>
      <div className="structured-toolbar"><div><button type="button" className={previewFilter === "all" ? "selected" : "secondary"} onClick={() => setPreviewFilter("all")}>Все</button><button type="button" className={previewFilter === "inflow" ? "selected" : "secondary"} onClick={() => setPreviewFilter("inflow")}>Приход</button><button type="button" className={previewFilter === "outflow" ? "selected" : "secondary"} onClick={() => setPreviewFilter("outflow")}>Расход</button><button type="button" className={previewFilter === "issues" ? "selected" : "secondary"} onClick={() => setPreviewFilter("issues")}>С замечаниями</button></div><button type="button" className="secondary" disabled={!visibleIds.length} onClick={() => setSelectedRows((current) => allVisibleSelected ? current.filter((id) => !visibleIds.includes(id)) : Array.from(new Set([...current, ...visibleIds])))}>{allVisibleSelected ? "Снять видимые" : "Выбрать видимые"}</button></div>
      <div className="structured-table"><table><thead><tr><th></th><th>Источник</th><th>Наименование / статья</th><th>Тип</th><th>Плановая дата</th><th>Сумма, ₽</th><th>Проверка</th></tr></thead><tbody>{visiblePreviewRows.slice(0, 500).map((row) => <tr className={row.importable ? "" : "invalid"} key={row.selection_id}><td><input aria-label={`Выбрать ${row.source_coordinate}`} type="checkbox" disabled={!row.importable} checked={selectedRows.includes(row.selection_id)} onChange={(event) => setSelectedRows((current) => event.target.checked ? Array.from(new Set([...current, row.selection_id])) : current.filter((value) => value !== row.selection_id))} /></td><td title={row.source_name}>{row.source_coordinate}</td><td><input aria-label={`Наименование ${row.source_coordinate}`} value={row.title || ""} onChange={(event) => onEditPreviewRow?.(row.selection_id, { title: event.target.value })} /><input aria-label={`Категория ${row.source_coordinate}`} value={row.category || ""} placeholder="Категория" onChange={(event) => onEditPreviewRow?.(row.selection_id, { category: event.target.value })} /></td><td><select aria-label={`Тип ${row.source_coordinate}`} value={row.direction || "outflow"} onChange={(event) => onEditPreviewRow?.(row.selection_id, { direction: event.target.value })}><option value="inflow">Приход</option><option value="outflow">Расход</option></select></td><td><input aria-label={`Дата ${row.source_coordinate}`} type="date" value={row.planned_date || row.planned_finish || row.planned_start || ""} onChange={(event) => onEditPreviewRow?.(row.selection_id, { planned_date: event.target.value })} /></td><td><input aria-label={`Сумма ${row.source_coordinate}`} type="number" min="0.01" step="0.01" value={row.amount || ""} onChange={(event) => onEditPreviewRow?.(row.selection_id, { amount: event.target.value })} /></td><td>{row.issues.length ? row.issues.join("; ") : "Готово"}</td></tr>)}</tbody></table></div>
      {preview.truncated && <p className="finance-warning">Показаны первые 500 строк. Разделите файл или импортируйте его частями.</p>}
      <div className="structured-actions structured-review-actions"><span>Выбрано: <strong>{selectedRows.length}</strong></span><div><button className="secondary" onClick={onClosePreview}>Отмена</button><button disabled={!selectedRows.length} onClick={onImport}>Создать предложения ({selectedRows.length})</button></div></div>
    </section>}
    {includeEditor && !preview && !invoiceProposal && <section className="card finance-entry" id="finance-entry">
      <div><h2>{editorScope === "gpr" ? "Добавить версию или задачу ГПР" : editorScope === "finance" ? "Добавить бюджетную или платёжную запись" : "Добавить управленческую запись"}</h2><p>Новая запись создаётся как предложение и не влияет на подтверждённый прогноз.</p>{sourceDocumentId > 0 && <p className="finance-source-note">Источник: документ #{sourceDocumentId}. Связь сохранится для счёта или акта.</p>}</div>
      <div><select aria-label="Тип финансовой записи" value={kind} onChange={(event) => setKind(event.target.value)}>{editorKinds.includes("budget") && <option value="budget">Строка бюджета</option>}{editorKinds.includes("cash-in") && <option value="cash-in">Поступление ДДС</option>}{editorKinds.includes("cash-out") && <option value="cash-out">Выплата ДДС</option>}{editorKinds.includes("invoice") && <option value="invoice">Счёт → предложение ДДС</option>}{editorKinds.includes("procurement") && <option value="procurement">Закупка / поставка</option>}{editorKinds.includes("act") && <option value="act">Акт</option>}{editorKinds.includes("baseline") && <option value="baseline">Версия ГПР</option>}{editorKinds.includes("schedule") && <option value="schedule">Этап ГПР</option>}</select>
        <input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Название" />
        {kind !== "baseline" && <input type="number" min="0" max={kind === "schedule" ? 100 : undefined} value={amount} onChange={(event) => setAmount(event.target.value)} placeholder={kind === "schedule" ? "План выполнения, %" : "Сумма, ₽"} />}
        {kind === "schedule" && <select aria-label="Версия для новой задачи" value={baselineId} onChange={(event) => setBaselineId(Number(event.target.value))}><option value={0}>Выберите черновик ГПР</option>{filterContract(finance?.baselines).filter((row) => row.status === "draft").map((row) => <option value={row.id} key={row.id}>v{row.version} · {row.name}</option>)}</select>}
        {["cash-in", "cash-out", "invoice", "procurement", "act", "schedule"].includes(kind) && <input aria-label={kind === "schedule" ? "Дата новой задачи" : undefined} type="date" value={date} onChange={(event) => setDate(event.target.value)} />}
        {["cash-in", "cash-out"].includes(kind) && <><input value={objectName} onChange={(event) => setObjectName(event.target.value)} placeholder="Объект (например, Дубна)" /><input value={category} onChange={(event) => setCategory(event.target.value)} placeholder={kind === "cash-in" ? "Статья (приход от заказчика)" : "Статья затрат"} /><input value={note} onChange={(event) => setNote(event.target.value)} placeholder="Описание операции" /></>}
        <input value={extra} onChange={(event) => setExtra(event.target.value)} placeholder={kind === "budget" ? "Категория" : kind === "act" ? "Номер акта" : kind === "baseline" ? "Комментарий" : kind === "schedule" ? "Комментарий к этапу" : "Контрагент / поставщик"} />
        {kind === "invoice" && <select value={scheduleItemId} onChange={(event) => setScheduleItemId(Number(event.target.value))}><option value={0}>Связать с этапом ГПР (не выбран)</option>{finance?.schedule.filter((stage) => { const baseline = finance.baselines.find((row) => row.id === stage.baseline_id); return !selectedContractId || baseline?.contract_id === selectedContractId; }).map((stage) => <option key={stage.id} value={stage.id}>{stage.title}</option>)}</select>}
        {["invoice", "act"].includes(kind) && <select aria-label={kind === "act" ? "Строка бюджета для акта" : undefined} value={budgetLineId} onChange={(event) => setBudgetLineId(Number(event.target.value))}><option value={0}>Связать со строкой бюджета (не выбрана)</option>{filterContract(finance?.budget).map((row) => <option key={row.id} value={row.id}>{row.description}</option>)}</select>}
        <button disabled={!title.trim()} onClick={onAdd}>Создать предложение</button>
      </div>
    </section>}
    {includeRegisters && <section className="finance-grid">
      {includeScheduleRegister && <article className="card"><h2>ГПР: план / факт</h2><div className="finance-list">{filterContract(finance?.baselines).map((item) => <div key={item.id}><span><strong>{item.name}</strong><small>Версия {item.version}</small></span><b>{item.status}</b>{item.status === "draft" && <button onClick={() => onConfirm("baselines", item.id, "approved")}>Утвердить baseline</button>}</div>)}{!filterContract(finance?.baselines).length && <p className="finance-empty">Добавьте первую версию ГПР.</p>}</div><p className="finance-warning">Отстающих работ: {finance?.summary.delayed_schedule || 0}</p></article>}
      <article className="card" id="budget-register"><h2>Бюджет</h2><div className="finance-list">{filterContract(finance?.budget).map((item) => <div key={item.id}><span><strong>{item.description}</strong><small>{item.category} · план {money(item.planned_amount)} · законтрактовано {money(item.committed_amount)} · факт работ {money(item.actual_amount)} · прогноз {money(item.forecast_amount)}</small>{item.overrun_amount > 0 && <small className="bad">Перерасход: {money(item.overrun_amount)}. Подписание не заблокировано.</small>}</span><b>{item.status}</b>{item.status === "proposed" && <button onClick={() => onConfirm("budget", item.id, "approved")}>Подтвердить</button>}</div>)}{!filterContract(finance?.budget).length && <p className="finance-empty">Строк бюджета пока нет.</p>}</div></article>
      {includeCashFlowRegister && <article className="card"><h2>ДДС</h2><div className="finance-list">{filterContract(finance?.cash_flow).map((item) => <div key={item.id}><span><strong>{item.title}</strong><small>{item.direction === "inflow" ? "Поступление" : "Выплата"} · {item.planned_date} · {money(item.planned_amount)}</small></span><b>{item.status}</b>{item.status === "proposed" && <button onClick={() => onConfirm("cash-flow", item.id, "approved")}>Подтвердить</button>}{item.status === "approved" && <button onClick={() => onConfirmPayment(item.id, Number(item.planned_amount))}>Подтвердить оплату</button>}</div>)}{!filterContract(finance?.cash_flow).length && <p className="finance-empty">План ДДС пока пуст.</p>}</div></article>}
      <article className="card"><h2>Закупки и поставки</h2><div className="finance-list">{filterContract(finance?.procurement).map((item) => <div key={item.id}><span><strong>{item.title}</strong><small>{item.supplier || "Поставщик не указан"} · {item.planned_delivery || "без срока"} · {money(item.planned_amount)}</small></span><b>{item.stage}</b>{item.stage === "request" && <button onClick={() => onConfirm("procurement", item.id, "ordered")}>Заказано</button>}</div>)}{!filterContract(finance?.procurement).length && <p className="finance-empty">Закупок пока нет.</p>}</div><p className="finance-warning">Просроченных поставок: {finance?.summary.late_procurement || 0}</p></article>
      <article className="card"><h2>Акты и закрытие</h2><div className="finance-list">{filterContract(finance?.acts).map((item) => <div key={item.id}><span><strong>№{item.number} · {item.title}</strong><small>{item.act_date || "без даты"} · {money(item.amount)} · бюджет #{item.budget_line_id || "не связан"}</small></span><b>{item.status}</b>{item.status === "proposed" && <button onClick={() => onConfirm("acts", item.id, "approved")}>Подтвердить</button>}{item.status === "approved" && <button onClick={() => onConfirm("acts", item.id, "signed")}>Подписать</button>}{["signed", "paid"].includes(item.status) && <button className="secondary" onClick={() => onConfirm("acts", item.id, "approved")}>Отменить подписание</button>}</div>)}{!filterContract(finance?.acts).length && <p className="finance-empty">Актов пока нет.</p>}</div></article>
      <article className="card finance-forecast"><h2>Прогноз</h2><p className={(finance?.summary.cash_gap || 0) < 0 ? "bad" : "good"}>{(finance?.summary.cash_gap || 0) < 0 ? `Ожидаемый кассовый разрыв ${money(finance?.summary.cash_gap)}${finance?.summary.cash_gap_date ? ` к ${finance.summary.cash_gap_date}` : ""}` : "Кассовый разрыв по подтверждённому плану не выявлен"}</p><p>Ожидают обработки актов: {finance?.summary.acts_pending || 0}</p><p>Прогноз бюджета: {money(finance?.summary.budget_forecast)}</p><p>Ожидают подтверждения оплаты: <strong>{finance?.summary.pending_payments || 0}</strong></p><p>Счета без полной цепочки: <strong>{finance?.summary.unlinked_invoices || 0}</strong></p></article>
    </section>}
  </>;
}
