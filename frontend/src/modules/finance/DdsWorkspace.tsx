import { useEffect, useMemo, useState } from "react";
import { CalendarRange, CheckCheck, Download, ListFilter, Plus, RotateCcw, Search, Upload } from "lucide-react";
import { formatMoney } from "../../utils/numberFormat";
import type { FinanceOverview } from "./types";

type Props = {
  finance: FinanceOverview | null;
  selectedContractId: number;
  onPrepare: (kind: "cash-in" | "cash-out") => void;
  onConfirm: (kind: string, id: number, status: string) => void;
  onConfirmMany: (kind: string, ids: number[], status: string) => void | Promise<void>;
  onConfirmPayment: (id: number, amount: number) => void;
  onLinkControls: (id: number, contractId: number, scheduleItemId: number, budgetLineId: number) => void | Promise<void>;
  onMutatePlan?: (id: number, operation: "edit" | "move" | "copy", plannedDate: string, plannedAmount: number, expectedRecordVersion: number) => Promise<{ mutation_id: number }>;
  onUndoPlanMutation?: (mutationId: number) => Promise<void>;
  onOpenSchedule?: (scheduleItemId: number) => void;
  focusScheduleItemId?: number;
  onDropInvoices?: (files: File[]) => void | Promise<void>;
  onPrepareAdditionalExpense?: () => void;
};

type Tab = "months" | "calendar" | "details" | "summary";
type CashRow = NonNullable<FinanceOverview>["cash_flow"][number] & {
  object: string;
  category: string;
  note: string;
};

const tabs: { id: Tab; label: string }[] = [
  { id: "months", label: "ДДС по месяцам" },
  { id: "calendar", label: "Таблица ДДС" },
  { id: "details", label: "Детализация" },
  { id: "summary", label: "Сводка" },
];
const monthLong = new Intl.DateTimeFormat("ru-RU", { month: "long", year: "numeric", timeZone: "UTC" });
const dateFormat = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit", year: "2-digit", timeZone: "UTC" });

function monthKey(value: string) {
  return value.slice(0, 7);
}

function monthDate(key: string) {
  return new Date(`${key}-01T00:00:00Z`);
}

function amount(row: CashRow) {
  return Number(row.planned_amount);
}

function actualAmount(row: CashRow) {
  return row.actual_date ? Number(row.actual_amount) : 0;
}

function directionLabel(direction: string) {
  return direction === "inflow" ? "Приход" : "Расход";
}

function csvCell(value: unknown) {
  const raw = String(value ?? "").replace(/\r?\n/g, " ");
  const text = typeof value === "string" && /^[=+\-@]/u.test(raw) ? `'${raw}` : raw;
  return /[;"]/u.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function downloadCsv(filename: string, data: unknown[][]) {
  const csv = `\uFEFF${data.map((row) => row.map(csvCell).join(";")).join("\r\n")}`;
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

export function DdsWorkspace({ finance, selectedContractId, onPrepare, onConfirm, onConfirmMany, onConfirmPayment, onLinkControls, onMutatePlan, onUndoPlanMutation, onOpenSchedule, focusScheduleItemId, onDropInvoices, onPrepareAdditionalExpense }: Props) {
  const [tab, setTab] = useState<Tab>("calendar");
  const [objectFilter, setObjectFilter] = useState("all");
  const [categoryFilter, setCategoryFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const [startMonth, setStartMonth] = useState("");
  const [finishMonth, setFinishMonth] = useState("");
  const [query, setQuery] = useState("");
  const [selectedProposed, setSelectedProposed] = useState<Set<number>>(new Set());
  const [confirmingMany, setConfirmingMany] = useState(false);
  const [linkDrafts, setLinkDrafts] = useState<Record<number, { scheduleItemId: number; budgetLineId: number }>>({});
  const [currency, setCurrency] = useState("");
  const [dragSourceId, setDragSourceId] = useState(0);
  const [pendingDrop, setPendingDrop] = useState<{ row: CashRow; month: string } | null>(null);
  const [undoStack, setUndoStack] = useState<number[]>([]);
  const [dropActive, setDropActive] = useState(false);
  const allRows = useMemo<CashRow[]>(() => (finance?.cash_flow || [])
    .filter((row) => !selectedContractId || row.contract_id === selectedContractId)
    .map((row) => ({
      ...row,
      object: row.object_name?.trim() || "Общие",
      category: row.category?.trim() || (row.direction === "inflow" ? "Приход от заказчика" : "Прочее"),
      note: row.note?.trim() || row.title,
    })), [finance, selectedContractId]);
  const currencies = useMemo(() => Array.from(new Set(allRows.map((row) => row.currency || "RUB"))).sort(), [allRows]);
  const selectedCurrency = currencies.includes(currency) ? currency : currencies[0] || "RUB";
  const rows = useMemo(() => allRows.filter((row) => (row.currency || "RUB") === selectedCurrency), [allRows, selectedCurrency]);
  useEffect(() => {
    if (!focusScheduleItemId) return;
    setTab("details");
    window.setTimeout(() => document.getElementById(`dds-row-${focusScheduleItemId}`)?.scrollIntoView({ block: "center" }), 0);
  }, [focusScheduleItemId]);
  const objects = useMemo(() => Array.from(new Set(rows.map((row) => row.object))).sort((a, b) => a.localeCompare(b, "ru")), [rows]);
  const categories = useMemo(() => Array.from(new Set(rows.map((row) => row.category))).sort((a, b) => a.localeCompare(b, "ru")), [rows]);
  const linkOptions = (row: CashRow) => {
    const contractId = row.contract_id || selectedContractId;
    const baselineIds = new Set((finance?.baselines || []).filter((item) => item.contract_id === contractId).map((item) => item.id));
    return {
      contractId,
      schedule: (finance?.schedule || []).filter((item) => baselineIds.has(item.baseline_id)),
      budget: (finance?.budget || []).filter((item) => item.contract_id === contractId),
    };
  };
  const setLinkDraft = (id: number, patch: Partial<{ scheduleItemId: number; budgetLineId: number }>) => {
    setLinkDrafts((current) => ({
      ...current,
      [id]: { scheduleItemId: current[id]?.scheduleItemId || 0, budgetLineId: current[id]?.budgetLineId || 0, ...patch },
    }));
  };
  const visibleRows = useMemo(() => rows.filter((row) =>
    (objectFilter === "all" || row.object === objectFilter) &&
    (categoryFilter === "all" || row.category === categoryFilter) &&
    (statusFilter === "all" || row.status === statusFilter) &&
    (!startMonth || monthKey(row.planned_date) >= startMonth) &&
    (!finishMonth || monthKey(row.planned_date) <= finishMonth) &&
    (!query.trim() || `${row.title} ${row.note} ${row.counterparty || ""}`.toLocaleLowerCase("ru-RU").includes(query.trim().toLocaleLowerCase("ru-RU")))
  ), [rows, objectFilter, categoryFilter, statusFilter, startMonth, finishMonth, query]);
  const months = useMemo(() => {
    if (!visibleRows.length) return [];
    const keys = visibleRows.flatMap((row) => [
      monthKey(row.planned_date),
      ...(row.actual_date ? [monthKey(row.actual_date)] : []),
    ]).sort();
    const start = new Date(Date.UTC(monthDate(keys[0]).getUTCFullYear(), 0, 1));
    const finish = new Date(Date.UTC(monthDate(keys[keys.length - 1]).getUTCFullYear(), 11, 1));
    const result: string[] = [];
    for (const cursor = new Date(start); cursor <= finish; cursor.setUTCMonth(cursor.getUTCMonth() + 1)) {
      result.push(cursor.toISOString().slice(0, 7));
    }
    return result;
  }, [visibleRows]);
  const byMonth = useMemo(() => months.map((key) => {
    const matching = visibleRows.filter((row) => monthKey(row.planned_date) === key && row.status !== "cancelled");
    const inflow = matching.filter((row) => row.direction === "inflow").reduce((sum, row) => sum + amount(row), 0);
    const outflow = matching.filter((row) => row.direction === "outflow").reduce((sum, row) => sum + amount(row), 0);
    return { key, inflow, outflow, net: inflow - outflow };
  }), [months, visibleRows]);
  const totals = useMemo(() => visibleRows.filter((row) => row.status !== "cancelled").reduce((result, row) => {
    const value = amount(row);
    if (row.direction === "inflow") result.inflow += value;
    else result.outflow += value;
    result.net = result.inflow - result.outflow;
    return result;
  }, { inflow: 0, outflow: 0, net: 0 }), [visibleRows]);
  const visibleObjects = Array.from(new Set(visibleRows.map((row) => row.object))).sort((a, b) => a.localeCompare(b, "ru"));
  const visibleCategories = Array.from(new Set(visibleRows.map((row) => row.category))).sort((a, b) => a.localeCompare(b, "ru"));
  const cumulative = byMonth.reduce<{ [key: string]: number }>((result, item, index) => {
    result[item.key] = item.net + (index ? result[byMonth[index - 1].key] : 0);
    return result;
  }, {});
  const calendarYearLabel = months.length ? (months[0].slice(0, 4) === months[months.length - 1].slice(0, 4) ? months[0].slice(0, 4) : `${months[0].slice(0, 4)}–${months[months.length - 1].slice(0, 4)}`) : String(new Date().getFullYear());
  const rowMonthTotal = (items: CashRow[], key: string) => items.filter((row) => monthKey(row.planned_date) === key && row.status !== "cancelled").reduce((sum, row) => sum + amount(row), 0);
  const rowTotal = (items: CashRow[]) => items.filter((row) => row.status !== "cancelled").reduce((sum, row) => sum + amount(row), 0);
  const objectSummary = visibleObjects.map((object) => {
    const matching = visibleRows.filter((row) => row.object === object && row.status !== "cancelled");
    const inflow = matching.filter((row) => row.direction === "inflow").reduce((sum, row) => sum + amount(row), 0);
    const outflow = matching.filter((row) => row.direction === "outflow").reduce((sum, row) => sum + amount(row), 0);
    return { object, inflow, outflow, net: inflow - outflow };
  });
  const categorySummary = visibleCategories.map((category) => {
    const value = visibleRows.filter((row) => row.category === category && row.direction === "outflow" && row.status !== "cancelled").reduce((sum, row) => sum + amount(row), 0);
    return { category, value, share: totals.outflow ? value / totals.outflow : 0 };
  }).filter((item) => item.value > 0).sort((a, b) => b.value - a.value);

  const canConfirm = (row: CashRow) => row.status === "proposed" && (
    row.direction !== "outflow" || !row.source_document_id
    || Boolean(row.contract_id && row.schedule_item_id && row.budget_line_id)
  );
  const proposedRows = visibleRows.filter(canConfirm);
  const selectedVisibleIds = proposedRows.filter((row) => selectedProposed.has(row.id)).map((row) => row.id);
  const allVisibleProposedSelected = proposedRows.length > 0 && selectedVisibleIds.length === proposedRows.length;
  const toggleProposed = (id: number) => setSelectedProposed((current) => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return next;
  });
  const toggleAllProposed = () => setSelectedProposed((current) => {
    const next = new Set(current);
    proposedRows.forEach((row) => allVisibleProposedSelected ? next.delete(row.id) : next.add(row.id));
    return next;
  });
  const confirmSelected = async () => {
    if (!selectedVisibleIds.length || confirmingMany) return;
    setConfirmingMany(true);
    try {
      await onConfirmMany("cash-flow", selectedVisibleIds, "approved");
      setSelectedProposed((current) => {
        const next = new Set(current);
        selectedVisibleIds.forEach((id) => next.delete(id));
        return next;
      });
    } finally {
      setConfirmingMany(false);
    }
  };
  const mutatePlan = async (row: CashRow, operation: "edit" | "move" | "copy", plannedDate: string, plannedAmount: number) => {
    if (!onMutatePlan) return;
    if (row.status !== "proposed" || row.actual_date || Number(row.actual_amount) !== 0) {
      window.alert("Подтверждённый план или факт нельзя менять в таблице. Используйте корректировку платежа.");
      return;
    }
    const result = await onMutatePlan(row.id, operation, plannedDate, plannedAmount, row.record_version || 1);
    setUndoStack((current) => [...current, result.mutation_id]);
  };
  const applyDrop = async (operation: "move" | "copy") => {
    if (!pendingDrop) return;
    const [year, month] = pendingDrop.month.split("-").map(Number);
    const lastDay = new Date(Date.UTC(year, month, 0)).getUTCDate();
    const day = Math.min(Number(pendingDrop.row.planned_date.slice(8, 10)) || 1, lastDay);
    await mutatePlan(pendingDrop.row, operation, `${pendingDrop.month}-${String(day).padStart(2, "0")}`, amount(pendingDrop.row));
    setPendingDrop(null); setDragSourceId(0);
  };
  const undoLast = async () => {
    const mutationId = undoStack[undoStack.length - 1];
    if (!mutationId || !onUndoPlanMutation) return;
    await onUndoPlanMutation(mutationId);
    setUndoStack((current) => current.slice(0, -1));
  };
  const dropInvoices = (files: File[]) => {
    const pdf = files.filter((file) => file.type === "application/pdf" || file.name.toLocaleLowerCase().endsWith(".pdf"));
    if (pdf.length) void onDropInvoices?.(pdf);
    else window.alert("Перетащите счёт в формате PDF.");
  };
  const planCell = (row: CashRow, key: string) => {
    const active = monthKey(row.planned_date) === key;
    const editable = row.status === "proposed" && !row.actual_date && Number(row.actual_amount) === 0;
    return <td className={`money dds-plan-cell ${active ? "filled" : ""}`} key={key}
      onDragOver={(event) => { if (dragSourceId && dragSourceId !== row.id) return; event.preventDefault(); }}
      onDrop={(event) => { event.preventDefault(); const source = visibleRows.find((item) => item.id === dragSourceId); if (source && monthKey(source.planned_date) !== key) setPendingDrop({ row: source, month: key }); }}>
      {active && editable ? <div draggable onDragStart={() => setDragSourceId(row.id)} onDragEnd={() => setDragSourceId(0)}>
        <input aria-label={`План ${row.title} ${key}`} type="number" min="0.01" step="0.01" defaultValue={amount(row)}
          onKeyDown={(event) => { if (event.key === "Enter") event.currentTarget.blur(); }}
          onBlur={(event) => { const value = Number(event.target.value); if (Number.isFinite(value) && value > 0 && value !== amount(row)) void mutatePlan(row, "edit", row.planned_date, value); }} />
      </div> : active ? <span title="Подтверждённый план неизменяем; используйте корректировку">{formatMoney(amount(row))}</span> : "—"}
      {active && actualAmount(row) > 0 && <small>факт {formatMoney(actualAmount(row))}</small>}
    </td>;
  };
  const exportView = (target: Tab = tab) => {
    const suffix = new Date().toISOString().slice(0, 10);
    if (target === "months") {
      downloadCsv(`ДДС_по_месяцам_${suffix}.csv`, [
        ["Месяц", `План прихода, ${selectedCurrency}`, `План расхода, ${selectedCurrency}`, `Плановый поток, ${selectedCurrency}`, `Плановый остаток, ${selectedCurrency}`],
        ...byMonth.map((item) => [monthLong.format(monthDate(item.key)), item.inflow, item.outflow, item.net, cumulative[item.key]]),
        ["ИТОГО", totals.inflow, totals.outflow, totals.net, totals.net],
      ]);
    } else if (target === "calendar") {
      const header = ["Уровень", "Объект / статья / операция", "Тип", ...months, `ИТОГО, ${selectedCurrency}`];
      const data: unknown[][] = [header];
      visibleObjects.forEach((object) => {
        const objectRows = visibleRows.filter((row) => row.object === object && row.status !== "cancelled");
        data.push(["Объект", object, "", ...months.map((key) => objectRows.filter((row) => monthKey(row.planned_date) === key).reduce((sum, row) => sum + amount(row), 0)), objectRows.reduce((sum, row) => sum + amount(row), 0)]);
        visibleCategories.forEach((category) => {
          const matching = objectRows.filter((row) => row.category === category);
          if (!matching.length) return;
          data.push(["Статья", category, "", ...months.map((key) => matching.filter((row) => monthKey(row.planned_date) === key).reduce((sum, row) => sum + amount(row), 0)), matching.reduce((sum, row) => sum + amount(row), 0)]);
          matching.forEach((row) => data.push(["Операция", row.title, directionLabel(row.direction), ...months.map((key) => monthKey(row.planned_date) === key ? amount(row) : ""), amount(row)]));
        });
      });
      downloadCsv(`ДДС_календарь_${suffix}.csv`, data);
    } else if (target === "details") {
      downloadCsv(`ДДС_полный_${suffix}.csv`, [
        ["№", "Плановая дата", "Фактическая дата", "Месяц плана", "Объект", "Статья", "Тип операции", "План", "Факт", "Валюта", "Описание операции", "Статус", "ID операции", "ID ГПР", "ID договора", "ID документа"],
        ...visibleRows.map((row, index) => [index + 1, row.planned_date, row.actual_date || "", monthLong.format(monthDate(monthKey(row.planned_date))), row.object, row.category, directionLabel(row.direction), amount(row), actualAmount(row), row.currency, row.note, row.status, row.id, row.schedule_item_id || "", row.contract_id || "", row.source_document_id || ""]),
      ]);
    } else {
      downloadCsv(`ДДС_сводка_${suffix}.csv`, [
        ["ПО ОБЪЕКТАМ"],
        ["Объект", `План прихода, ${selectedCurrency}`, `План расхода, ${selectedCurrency}`, `Плановый поток, ${selectedCurrency}`],
        ...objectSummary.map((item) => [item.object, item.inflow, item.outflow, item.net]),
        ["ИТОГО", totals.inflow, totals.outflow, totals.net],
        [],
        ["РАСХОДЫ ПО СТАТЬЯМ"],
        ["Статья затрат", `План, ${selectedCurrency}`, "Доля, %"],
        ...categorySummary.map((item) => [item.category, item.value, (item.share * 100).toFixed(1)]),
        ["ИТОГО расходы", totals.outflow, totals.outflow ? "100.0" : "0.0"],
      ]);
    }
  };
  const exportAdditionalExpenses = () => {
    const suffix = new Date().toISOString().slice(0, 10);
    const extra = visibleRows.filter((row) => row.direction === "outflow" && row.category.toLocaleLowerCase("ru-RU") === "дополнительные расходы");
    downloadCsv(`ДДС_дополнительные_расходы_${suffix}.csv`, [
      ["Плановая дата", "Фактическая дата", "Объект", "Статья", "План", "Факт", "Валюта", "Описание", "Статус", "ID операции", "ID ГПР"],
      ...extra.map((row) => [row.planned_date, row.actual_date || "", row.object, row.category, amount(row), actualAmount(row), row.currency, row.note, row.status, row.id, row.schedule_item_id || ""]),
    ]);
  };

  return <section className="card dds-workspace" id="dds-workspace">
    <div className="dds-head">
      <div><span className="eyebrow">ПЛАТЁЖНЫЙ КАЛЕНДАРЬ</span><h2>Движение денежных средств</h2><p>Все представления считаются из единой детализации. План и факт хранятся и показываются раздельно.</p></div>
      <div className="dds-head-actions"><button className="secondary" type="button" disabled={!undoStack.length} onClick={() => void undoLast()}><RotateCcw /> Отменить</button><button className="secondary" type="button" onClick={() => exportView()}><Download /> Экспорт: {tabs.find((item) => item.id === tab)?.label}</button><button className="secondary" type="button" onClick={() => exportView("details")}><Download /> Полный ДДС</button><button className="secondary" type="button" onClick={exportAdditionalExpenses}><Download /> Дополнительные расходы</button><button className="secondary" type="button" onClick={() => onPrepare("cash-in")}><Plus /> Приход</button><button type="button" onClick={() => onPrepare("cash-out")}><Plus /> Расход</button>{onPrepareAdditionalExpense && <button type="button" onClick={onPrepareAdditionalExpense}><Plus /> Доп. расход</button>}</div>
    </div>
    <div className={`dds-invoice-drop ${dropActive ? "active" : ""}`} onDragOver={(event) => { event.preventDefault(); setDropActive(true); }} onDragLeave={() => setDropActive(false)} onDrop={(event) => { event.preventDefault(); setDropActive(false); dropInvoices(Array.from(event.dataTransfer.files)); }}>
      <Upload /><div><strong>Перетащите сюда счёт PDF</strong><small>После OCR появится черновик. Статью затрат и плановую дату оплаты выбирает пользователь; платёж автоматически не подтверждается.</small></div>
    </div>
    <div className="dds-tabs" role="tablist" aria-label="Разделы ДДС">{tabs.map((item) => <button type="button" role="tab" aria-selected={tab === item.id} className={tab === item.id ? "active" : ""} onClick={() => setTab(item.id)} key={item.id}>{item.label}</button>)}</div>
    <div className="dds-filters">
      <label><ListFilter /><select aria-label="Фильтр по объекту" value={objectFilter} onChange={(event) => setObjectFilter(event.target.value)}><option value="all">Все объекты</option>{objects.map((object) => <option key={object}>{object}</option>)}</select></label>
      <label><select aria-label="Фильтр по статье" value={categoryFilter} onChange={(event) => setCategoryFilter(event.target.value)}><option value="all">Все статьи</option>{categories.map((category) => <option key={category}>{category}</option>)}</select></label>
      <label><select aria-label="Фильтр по статусу" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}><option value="all">Все статусы</option><option value="proposed">Предложено</option><option value="approved">Подтверждено</option><option value="paid">Оплачено</option><option value="received">Получено</option><option value="cancelled">Отменено</option></select></label>
      {currencies.length > 0 && <label><select aria-label="Валюта ДДС" value={selectedCurrency} onChange={(event) => setCurrency(event.target.value)}>{currencies.map((code) => <option key={code}>{code}</option>)}</select></label>}
      <label><input aria-label="Период с" type="month" value={startMonth} onChange={(event) => setStartMonth(event.target.value)} /></label>
      <label><input aria-label="Период по" type="month" value={finishMonth} onChange={(event) => setFinishMonth(event.target.value)} /></label>
      <label className="dds-search"><Search /><input aria-label="Поиск по ДДС" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Найти операцию или контрагента" /></label>
      <span>{visibleRows.length} операций</span>
    </div>

    {tab === "months" && <div className="dds-table-wrap"><table className="dds-table"><thead><tr><th>Месяц</th><th>План прихода, {selectedCurrency}</th><th>План расхода, {selectedCurrency}</th><th>Плановый поток, {selectedCurrency}</th><th>Плановый остаток, {selectedCurrency}</th><th>Факт, {selectedCurrency}</th></tr></thead><tbody>{byMonth.map((item) => { const actual = visibleRows.filter((row) => row.actual_date && monthKey(row.actual_date) === item.key && row.status !== "cancelled").reduce((sum, row) => sum + (row.direction === "inflow" ? actualAmount(row) : -actualAmount(row)), 0); return <tr key={item.key}><td>{monthLong.format(monthDate(item.key))}</td><td className="money positive">{formatMoney(item.inflow)}</td><td className="money">{formatMoney(item.outflow)}</td><td className={`money ${item.net < 0 ? "negative" : "positive"}`}>{formatMoney(item.net)}</td><td className={`money ${cumulative[item.key] < 0 ? "negative" : ""}`}>{formatMoney(cumulative[item.key])}</td><td className="money">{formatMoney(actual)}</td></tr>; })}<tr className="total"><td>ИТОГО</td><td className="money">{formatMoney(totals.inflow)}</td><td className="money">{formatMoney(totals.outflow)}</td><td className="money">{formatMoney(totals.net)}</td><td className="money">{formatMoney(totals.net)}</td><td className="money">{formatMoney(visibleRows.reduce((sum, row) => sum + (row.direction === "inflow" ? actualAmount(row) : -actualAmount(row)), 0))}</td></tr></tbody></table>{!byMonth.length && <p className="dds-empty">Добавьте первую плановую операцию ДДС.</p>}</div>}

    {tab === "calendar" && <div className="dds-calendar-shell">{pendingDrop && <div className="dds-drop-choice" role="dialog" aria-label="Действие с плановой суммой"><strong>{pendingDrop.row.title}: {monthLong.format(monthDate(monthKey(pendingDrop.row.planned_date)))} → {monthLong.format(monthDate(pendingDrop.month))}</strong><button type="button" onClick={() => void applyDrop("move")}>Переместить</button><button type="button" className="secondary" onClick={() => void applyDrop("copy")}>Копировать</button><button type="button" className="secondary" onClick={() => setPendingDrop(null)}>Отмена</button></div>}<div className="dds-table-wrap dds-calendar" onWheel={(event) => { if (event.shiftKey) event.currentTarget.scrollLeft += event.deltaY; }}><table className="dds-table dds-template"><thead><tr><th>Статья ДДС</th><th>Итого за {calendarYearLabel}, {selectedCurrency}</th>{months.map((key) => <th key={key}>{monthLong.format(monthDate(key)).replace(/\s+\d{4}.*/, "")}</th>)}</tr></thead><tbody>
      <tr className="group inflow primary"><td>Платежи — всего</td><td className="money">{formatMoney(totals.inflow)}</td>{months.map((key) => <td className="money" key={key}>{formatMoney(byMonth.find((item) => item.key === key)?.inflow || 0)}</td>)}</tr>
      {visibleObjects.flatMap((object) => { const items = visibleRows.filter((row) => row.object === object && row.direction === "inflow"); if (!items.length) return []; return [<tr className="group inflow" key={`in-${object}`}><td>{object} — всего поступлений</td><td className="money">{formatMoney(rowTotal(items))}</td>{months.map((key) => <td className="money" key={key}>{formatMoney(rowMonthTotal(items, key))}</td>)}</tr>, ...items.map((row) => <tr className="operation" id={`dds-row-${row.schedule_item_id || 0}`} key={row.id}><td className="operation-title">{row.title}{row.schedule_item_id && <button type="button" className="dds-schedule-link" onClick={() => onOpenSchedule?.(row.schedule_item_id!)}>ГПР #{row.schedule_item_id}</button>}</td><td className="money">{formatMoney(amount(row))}</td>{months.map((key) => planCell(row, key))}</tr>)]; })}
      {visibleObjects.flatMap((object) => { const items = visibleRows.filter((row) => row.object === object && row.direction === "outflow"); if (!items.length) return []; return [<tr className="group outflow" key={`out-${object}`}><td>{object} — всего затрат</td><td className="money">{formatMoney(rowTotal(items))}</td>{months.map((key) => <td className="money" key={key}>{formatMoney(rowMonthTotal(items, key))}</td>)}</tr>, ...items.map((row) => <tr className="operation" id={`dds-row-${row.schedule_item_id || 0}`} key={row.id}><td className="operation-title">{row.title}{row.schedule_item_id && <button type="button" className="dds-schedule-link" onClick={() => onOpenSchedule?.(row.schedule_item_id!)}>ГПР #{row.schedule_item_id}</button>}</td><td className="money">{formatMoney(amount(row))}</td>{months.map((key) => planCell(row, key))}</tr>)]; })}
      <tr className="dds-expense-total"><td>Расходы по месяцам</td><td className="money">{formatMoney(totals.outflow)}</td>{months.map((key) => <td className="money" key={key}>{formatMoney(byMonth.find((item) => item.key === key)?.outflow || 0)}</td>)}</tr>
      <tr className="dds-balance"><td>Баланс накопленным итогом</td><td className="money">{formatMoney(totals.net)}</td>{months.map((key) => <td className={`money ${(cumulative[key] || 0) < 0 ? "negative" : ""}`} key={key}>{formatMoney(cumulative[key] || 0)}</td>)}</tr>
    </tbody></table>{!visibleRows.length && <p className="dds-empty">Календарь появится после добавления операций.</p>}</div></div>}

    {tab === "details" && <div className="dds-table-wrap">{proposedRows.length > 0 && <div className="dds-head-actions"><button className="secondary" type="button" onClick={toggleAllProposed}>{allVisibleProposedSelected ? "Снять выбор" : "Выбрать предложенные"}</button><button type="button" disabled={!selectedVisibleIds.length || confirmingMany} onClick={confirmSelected}><CheckCheck /> {confirmingMany ? "Подтверждаем…" : `Подтвердить выбранные (${selectedVisibleIds.length})`}</button></div>}<table className="dds-table"><thead><tr><th><input type="checkbox" aria-label="Выбрать все предложенные операции" checked={allVisibleProposedSelected} disabled={!proposedRows.length} onChange={toggleAllProposed} /></th><th>№</th><th>Плановая дата</th><th>Месяц</th><th>Объект</th><th>Статья</th><th>Тип операции</th><th>План, {selectedCurrency}</th><th>Факт, {selectedCurrency}</th><th>Описание операции</th><th>Статус</th><th>Связь</th><th></th></tr></thead><tbody>{visibleRows.map((row, index) => {
      const controlsComplete = Boolean(row.contract_id && row.schedule_item_id && row.budget_line_id);
      const options = linkOptions(row);
      const draft = linkDrafts[row.id] || { scheduleItemId: row.schedule_item_id || 0, budgetLineId: row.budget_line_id || 0 };
      return <tr id={`dds-row-${row.schedule_item_id || 0}`} key={row.id}><td>{canConfirm(row) && <input type="checkbox" aria-label={`Выбрать операцию ${row.title}`} checked={selectedProposed.has(row.id)} onChange={() => toggleProposed(row.id)} />}</td><td>{index + 1}</td><td>{dateFormat.format(new Date(`${row.planned_date}T00:00:00Z`))}</td><td>{monthLong.format(monthDate(monthKey(row.planned_date)))}</td><td>{row.object}</td><td>{row.category}</td><td><span className={`dds-direction ${row.direction}`}>{directionLabel(row.direction)}</span></td><td className="money">{formatMoney(amount(row))}</td><td className="money">{row.actual_date ? `${formatMoney(actualAmount(row))} · ${dateFormat.format(new Date(`${row.actual_date}T00:00:00Z`))}` : "—"}</td><td>{row.note}</td><td>{row.status}</td><td>{row.schedule_item_id ? <button type="button" className="dds-schedule-link" onClick={() => onOpenSchedule?.(row.schedule_item_id!)}>ГПР #{row.schedule_item_id}</button> : "—"}</td><td className="dds-row-actions">
        {row.status === "proposed" && row.direction === "outflow" && row.source_document_id && !controlsComplete ? <div className="dds-control-links"><select aria-label={`Этап ГПР для ${row.title}`} value={draft.scheduleItemId} onChange={(event) => setLinkDraft(row.id, { scheduleItemId: Number(event.target.value) })}><option value={0}>Этап ГПР</option>{options.schedule.map((item) => <option value={item.id} key={item.id}>{item.title}</option>)}</select><select aria-label={`Строка бюджета для ${row.title}`} value={draft.budgetLineId} onChange={(event) => setLinkDraft(row.id, { budgetLineId: Number(event.target.value) })}><option value={0}>Строка бюджета</option>{options.budget.map((item) => <option value={item.id} key={item.id}>{item.description}</option>)}</select><button type="button" disabled={!options.contractId || !draft.scheduleItemId || !draft.budgetLineId} onClick={() => onLinkControls(row.id, options.contractId, draft.scheduleItemId, draft.budgetLineId)}>Связать с контролями</button></div> : row.status === "proposed" && <button type="button" onClick={() => onConfirm("cash-flow", row.id, "approved")}>Подтвердить</button>}
        {row.status === "approved" && <button type="button" onClick={() => onConfirmPayment(row.id, Number(row.planned_amount))}>Оплата</button>}
      </td></tr>;
    })}</tbody></table>{!visibleRows.length && <p className="dds-empty">Нет операций по выбранным фильтрам.</p>}</div>}

    {tab === "summary" && <div className="dds-summary">
      <section><h3>По объектам</h3><div className="dds-table-wrap"><table className="dds-table"><thead><tr><th>Объект</th><th>План прихода, {selectedCurrency}</th><th>План расхода, {selectedCurrency}</th><th>Плановый поток, {selectedCurrency}</th></tr></thead><tbody>{objectSummary.map((item) => <tr key={item.object}><td>{item.object}</td><td className="money positive">{formatMoney(item.inflow)}</td><td className="money">{formatMoney(item.outflow)}</td><td className={`money ${item.net < 0 ? "negative" : "positive"}`}>{formatMoney(item.net)}</td></tr>)}<tr className="total"><td>ИТОГО</td><td className="money">{formatMoney(totals.inflow)}</td><td className="money">{formatMoney(totals.outflow)}</td><td className="money">{formatMoney(totals.net)}</td></tr></tbody></table></div></section>
      <section><h3>Расходы по статьям</h3><div className="dds-table-wrap"><table className="dds-table"><thead><tr><th>Статья затрат</th><th>План, {selectedCurrency}</th><th>Доля, %</th></tr></thead><tbody>{categorySummary.map((item) => <tr key={item.category}><td>{item.category}</td><td className="money">{formatMoney(item.value)}</td><td><div className="dds-share"><span style={{ width: `${Math.max(3, item.share * 100)}%` }}></span></div>{(item.share * 100).toFixed(1)}%</td></tr>)}<tr className="total"><td>ИТОГО расходы</td><td className="money">{formatMoney(totals.outflow)}</td><td>100%</td></tr></tbody></table></div></section>
    </div>}
    <footer className="dds-note"><CalendarRange /> Плановые суммы используются до подтверждения факта. Отменённые операции не входят в расчёты.</footer>
  </section>;
}
