import { useMemo, useState } from "react";
import { CalendarRange, CheckCheck, Download, ListFilter, Plus, Search } from "lucide-react";
import { formatMoney } from "../../utils/numberFormat";
import type { FinanceOverview } from "./types";

type Props = {
  finance: FinanceOverview | null;
  selectedContractId: number;
  onPrepare: (kind: "cash-in" | "cash-out") => void;
  onConfirm: (kind: string, id: number, status: string) => void;
  onConfirmMany: (kind: string, ids: number[], status: string) => void | Promise<void>;
  onConfirmPayment: (id: number, amount: number) => void;
};

type Tab = "months" | "calendar" | "details" | "summary";
type CashRow = NonNullable<FinanceOverview>["cash_flow"][number] & {
  object: string;
  category: string;
  note: string;
};

const tabs: { id: Tab; label: string }[] = [
  { id: "months", label: "ДДС по месяцам" },
  { id: "calendar", label: "Календарь (вид ГПР)" },
  { id: "details", label: "Детализация" },
  { id: "summary", label: "Сводка" },
];
const monthLong = new Intl.DateTimeFormat("ru-RU", { month: "long", year: "numeric", timeZone: "UTC" });
const monthShort = new Intl.DateTimeFormat("ru-RU", { month: "short", timeZone: "UTC" });
const dateFormat = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit", year: "2-digit", timeZone: "UTC" });

function monthKey(value: string) {
  return value.slice(0, 7);
}

function monthDate(key: string) {
  return new Date(`${key}-01T00:00:00Z`);
}

function amount(row: CashRow) {
  return row.actual_date ? Number(row.actual_amount) : Number(row.planned_amount);
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

export function DdsWorkspace({ finance, selectedContractId, onPrepare, onConfirm, onConfirmMany, onConfirmPayment }: Props) {
  const [tab, setTab] = useState<Tab>("months");
  const [objectFilter, setObjectFilter] = useState("all");
  const [categoryFilter, setCategoryFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const [startMonth, setStartMonth] = useState("");
  const [finishMonth, setFinishMonth] = useState("");
  const [query, setQuery] = useState("");
  const [selectedProposed, setSelectedProposed] = useState<Set<number>>(new Set());
  const [confirmingMany, setConfirmingMany] = useState(false);
  const rows = useMemo<CashRow[]>(() => (finance?.cash_flow || [])
    .filter((row) => !selectedContractId || row.contract_id === selectedContractId)
    .map((row) => ({
      ...row,
      object: row.object_name?.trim() || "Общие",
      category: row.category?.trim() || (row.direction === "inflow" ? "Приход от заказчика" : "Прочее"),
      note: row.note?.trim() || row.title,
    })), [finance, selectedContractId]);
  const objects = useMemo(() => Array.from(new Set(rows.map((row) => row.object))).sort((a, b) => a.localeCompare(b, "ru")), [rows]);
  const categories = useMemo(() => Array.from(new Set(rows.map((row) => row.category))).sort((a, b) => a.localeCompare(b, "ru")), [rows]);
  const visibleRows = useMemo(() => rows.filter((row) =>
    (objectFilter === "all" || row.object === objectFilter) &&
    (categoryFilter === "all" || row.category === categoryFilter) &&
    (statusFilter === "all" || row.status === statusFilter) &&
    (!startMonth || monthKey(row.actual_date || row.planned_date) >= startMonth) &&
    (!finishMonth || monthKey(row.actual_date || row.planned_date) <= finishMonth) &&
    (!query.trim() || `${row.title} ${row.note} ${row.counterparty || ""}`.toLocaleLowerCase("ru-RU").includes(query.trim().toLocaleLowerCase("ru-RU")))
  ), [rows, objectFilter, categoryFilter, statusFilter, startMonth, finishMonth, query]);
  const months = useMemo(() => {
    if (!visibleRows.length) return [];
    const keys = visibleRows.map((row) => monthKey(row.actual_date || row.planned_date)).sort();
    const start = monthDate(keys[0]);
    const finish = monthDate(keys[keys.length - 1]);
    const result: string[] = [];
    for (const cursor = new Date(start); cursor <= finish; cursor.setUTCMonth(cursor.getUTCMonth() + 1)) {
      result.push(cursor.toISOString().slice(0, 7));
    }
    return result;
  }, [visibleRows]);
  const byMonth = useMemo(() => months.map((key) => {
    const matching = visibleRows.filter((row) => monthKey(row.actual_date || row.planned_date) === key && row.status !== "cancelled");
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
  const quarterGroups = months.reduce<{ label: string; count: number }[]>((result, key) => {
    const date = monthDate(key);
    const label = `Кв. ${Math.floor(date.getUTCMonth() / 3) + 1}, ${date.getUTCFullYear()}`;
    const last = result[result.length - 1];
    if (last?.label === label) last.count += 1;
    else result.push({ label, count: 1 });
    return result;
  }, []);
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

  const proposedRows = visibleRows.filter((row) => row.status === "proposed");
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
  const exportView = (target: Tab = tab) => {
    const suffix = new Date().toISOString().slice(0, 10);
    if (target === "months") {
      downloadCsv(`ДДС_по_месяцам_${suffix}.csv`, [
        ["Месяц", "Приход от заказчика, ₽", "Расход (затраты), ₽", "Чистый денежный поток, ₽", "Остаток нарастающим итогом, ₽"],
        ...byMonth.map((item) => [monthLong.format(monthDate(item.key)), item.inflow, item.outflow, item.net, cumulative[item.key]]),
        ["ИТОГО", totals.inflow, totals.outflow, totals.net, totals.net],
      ]);
    } else if (target === "calendar") {
      const header = ["Уровень", "Объект / статья / операция", "Тип", ...months, "ИТОГО, ₽"];
      const data: unknown[][] = [header];
      visibleObjects.forEach((object) => {
        const objectRows = visibleRows.filter((row) => row.object === object && row.status !== "cancelled");
        data.push(["Объект", object, "", ...months.map((key) => objectRows.filter((row) => monthKey(row.actual_date || row.planned_date) === key).reduce((sum, row) => sum + amount(row), 0)), objectRows.reduce((sum, row) => sum + amount(row), 0)]);
        visibleCategories.forEach((category) => {
          const matching = objectRows.filter((row) => row.category === category);
          if (!matching.length) return;
          data.push(["Статья", category, "", ...months.map((key) => matching.filter((row) => monthKey(row.actual_date || row.planned_date) === key).reduce((sum, row) => sum + amount(row), 0)), matching.reduce((sum, row) => sum + amount(row), 0)]);
          matching.forEach((row) => data.push(["Операция", row.title, directionLabel(row.direction), ...months.map((key) => monthKey(row.actual_date || row.planned_date) === key ? amount(row) : ""), amount(row)]));
        });
      });
      downloadCsv(`ДДС_календарь_${suffix}.csv`, data);
    } else if (target === "details") {
      downloadCsv(`ДДС_детализация_${suffix}.csv`, [
        ["№", "Дата", "Месяц", "Объект", "Статья", "Тип операции", "Сумма, ₽", "Описание операции", "Статус"],
        ...visibleRows.map((row, index) => [index + 1, row.actual_date || row.planned_date, monthLong.format(monthDate(monthKey(row.actual_date || row.planned_date))), row.object, row.category, directionLabel(row.direction), amount(row), row.note, row.status]),
      ]);
    } else {
      downloadCsv(`ДДС_сводка_${suffix}.csv`, [
        ["ПО ОБЪЕКТАМ"],
        ["Объект", "Приход, ₽", "Расход, ₽", "Чистый поток, ₽"],
        ...objectSummary.map((item) => [item.object, item.inflow, item.outflow, item.net]),
        ["ИТОГО", totals.inflow, totals.outflow, totals.net],
        [],
        ["РАСХОДЫ ПО СТАТЬЯМ"],
        ["Статья затрат", "Сумма, ₽", "Доля, %"],
        ...categorySummary.map((item) => [item.category, item.value, (item.share * 100).toFixed(1)]),
        ["ИТОГО расходы", totals.outflow, totals.outflow ? "100.0" : "0.0"],
      ]);
    }
  };

  return <section className="card dds-workspace">
    <div className="dds-head">
      <div><span className="eyebrow">ПЛАТЁЖНЫЙ КАЛЕНДАРЬ</span><h2>Движение денежных средств</h2><p>Все представления считаются из единой детализации. План заменяется фактом после подтверждения оплаты.</p></div>
      <div className="dds-head-actions"><button className="secondary" type="button" onClick={() => exportView()}><Download /> Экспорт: {tabs.find((item) => item.id === tab)?.label}</button>{tab !== "details" && <button className="secondary" type="button" onClick={() => exportView("details")}><Download /> Детализация</button>}<button className="secondary" type="button" onClick={() => onPrepare("cash-in")}><Plus /> Приход</button><button type="button" onClick={() => onPrepare("cash-out")}><Plus /> Расход</button></div>
    </div>
    <div className="dds-tabs" role="tablist" aria-label="Разделы ДДС">{tabs.map((item) => <button type="button" role="tab" aria-selected={tab === item.id} className={tab === item.id ? "active" : ""} onClick={() => setTab(item.id)} key={item.id}>{item.label}</button>)}</div>
    <div className="dds-filters">
      <label><ListFilter /><select aria-label="Фильтр по объекту" value={objectFilter} onChange={(event) => setObjectFilter(event.target.value)}><option value="all">Все объекты</option>{objects.map((object) => <option key={object}>{object}</option>)}</select></label>
      <label><select aria-label="Фильтр по статье" value={categoryFilter} onChange={(event) => setCategoryFilter(event.target.value)}><option value="all">Все статьи</option>{categories.map((category) => <option key={category}>{category}</option>)}</select></label>
      <label><select aria-label="Фильтр по статусу" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}><option value="all">Все статусы</option><option value="proposed">Предложено</option><option value="approved">Подтверждено</option><option value="paid">Оплачено</option><option value="received">Получено</option><option value="cancelled">Отменено</option></select></label>
      <label><input aria-label="Период с" type="month" value={startMonth} onChange={(event) => setStartMonth(event.target.value)} /></label>
      <label><input aria-label="Период по" type="month" value={finishMonth} onChange={(event) => setFinishMonth(event.target.value)} /></label>
      <label className="dds-search"><Search /><input aria-label="Поиск по ДДС" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Найти операцию или контрагента" /></label>
      <span>{visibleRows.length} операций</span>
    </div>

    {tab === "months" && <div className="dds-table-wrap"><table className="dds-table"><thead><tr><th>Месяц</th><th>Приход от заказчика, ₽</th><th>Расход (затраты), ₽</th><th>Чистый денежный поток, ₽</th><th>Остаток нарастающим итогом, ₽</th></tr></thead><tbody>{byMonth.map((item) => <tr key={item.key}><td>{monthLong.format(monthDate(item.key))}</td><td className="money positive">{formatMoney(item.inflow)}</td><td className="money">{formatMoney(item.outflow)}</td><td className={`money ${item.net < 0 ? "negative" : "positive"}`}>{formatMoney(item.net)}</td><td className={`money ${cumulative[item.key] < 0 ? "negative" : ""}`}>{formatMoney(cumulative[item.key])}</td></tr>)}<tr className="total"><td>ИТОГО</td><td className="money">{formatMoney(totals.inflow)}</td><td className="money">{formatMoney(totals.outflow)}</td><td className="money">{formatMoney(totals.net)}</td><td className="money">{formatMoney(totals.net)}</td></tr></tbody></table>{!byMonth.length && <p className="dds-empty">Добавьте первую плановую операцию ДДС.</p>}</div>}

    {tab === "calendar" && <div className="dds-table-wrap dds-calendar"><table className="dds-table"><thead><tr className="dds-quarter-row"><th rowSpan={2}>Объект / статья / операция</th><th rowSpan={2}>Тип</th>{quarterGroups.map((group) => <th colSpan={group.count} key={group.label}>{group.label}</th>)}<th rowSpan={2}>ИТОГО, ₽</th></tr><tr>{months.map((key) => <th key={key}>{monthShort.format(monthDate(key))}<small>{key.slice(0, 4)}</small></th>)}</tr></thead><tbody>{visibleObjects.flatMap((object) => {
      const objectRows = visibleRows.filter((row) => row.object === object && row.status !== "cancelled");
      if (!objectRows.length) return [];
      const objectTotal = objectRows.reduce((sum, row) => sum + amount(row), 0);
      const detailRows = visibleCategories.flatMap((category) => {
        const matching = objectRows.filter((row) => row.category === category);
        if (!matching.length) return [];
        return [<tr className="group category" key={`${object}-${category}`}><td>{category}</td><td></td>{months.map((key) => <td className="money" key={key}>{formatMoney(matching.filter((row) => monthKey(row.actual_date || row.planned_date) === key).reduce((sum, row) => sum + amount(row), 0))}</td>)}<td className="money">{formatMoney(matching.reduce((sum, row) => sum + amount(row), 0))}</td></tr>, ...matching.map((row) => <tr key={row.id}><td className="operation">{row.title}</td><td><span className={`dds-direction ${row.direction}`}>{directionLabel(row.direction)}</span></td>{months.map((key) => <td className="money" key={key}>{monthKey(row.actual_date || row.planned_date) === key ? formatMoney(amount(row)) : "—"}</td>)}<td className="money">{formatMoney(amount(row))}</td></tr>)];
      });
      return [<tr className="group object" key={object}><td>{object.toLocaleUpperCase("ru-RU")}</td><td></td>{months.map((key) => <td className="money" key={key}>{formatMoney(objectRows.filter((row) => monthKey(row.actual_date || row.planned_date) === key).reduce((sum, row) => sum + amount(row), 0))}</td>)}<td className="money">{formatMoney(objectTotal)}</td></tr>, ...detailRows];
    })}</tbody></table>{!visibleRows.length && <p className="dds-empty">Календарь появится после добавления операций.</p>}</div>}

    {tab === "details" && <div className="dds-table-wrap">{proposedRows.length > 0 && <div className="dds-head-actions"><button className="secondary" type="button" onClick={toggleAllProposed}>{allVisibleProposedSelected ? "Снять выбор" : "Выбрать предложенные"}</button><button type="button" disabled={!selectedVisibleIds.length || confirmingMany} onClick={confirmSelected}><CheckCheck /> {confirmingMany ? "Подтверждаем…" : `Подтвердить выбранные (${selectedVisibleIds.length})`}</button></div>}<table className="dds-table"><thead><tr><th><input type="checkbox" aria-label="Выбрать все предложенные операции" checked={allVisibleProposedSelected} disabled={!proposedRows.length} onChange={toggleAllProposed} /></th><th>№</th><th>Дата</th><th>Месяц</th><th>Объект</th><th>Статья</th><th>Тип операции</th><th>Сумма, ₽</th><th>Описание операции</th><th>Статус</th><th></th></tr></thead><tbody>{visibleRows.map((row, index) => <tr key={row.id}><td>{row.status === "proposed" && <input type="checkbox" aria-label={`Выбрать операцию ${row.title}`} checked={selectedProposed.has(row.id)} onChange={() => toggleProposed(row.id)} />}</td><td>{index + 1}</td><td>{dateFormat.format(new Date(`${row.actual_date || row.planned_date}T00:00:00Z`))}</td><td>{monthLong.format(monthDate(monthKey(row.actual_date || row.planned_date)))}</td><td>{row.object}</td><td>{row.category}</td><td><span className={`dds-direction ${row.direction}`}>{directionLabel(row.direction)}</span></td><td className="money">{formatMoney(amount(row))}</td><td>{row.note}</td><td>{row.status}</td><td className="dds-row-actions">{row.status === "proposed" && <button type="button" onClick={() => onConfirm("cash-flow", row.id, "approved")}>Подтвердить</button>}{row.status === "approved" && <button type="button" onClick={() => onConfirmPayment(row.id, Number(row.planned_amount))}>Оплата</button>}</td></tr>)}</tbody></table>{!visibleRows.length && <p className="dds-empty">Нет операций по выбранным фильтрам.</p>}</div>}

    {tab === "summary" && <div className="dds-summary">
      <section><h3>По объектам</h3><div className="dds-table-wrap"><table className="dds-table"><thead><tr><th>Объект</th><th>Приход, ₽</th><th>Расход, ₽</th><th>Чистый поток, ₽</th></tr></thead><tbody>{objectSummary.map((item) => <tr key={item.object}><td>{item.object}</td><td className="money positive">{formatMoney(item.inflow)}</td><td className="money">{formatMoney(item.outflow)}</td><td className={`money ${item.net < 0 ? "negative" : "positive"}`}>{formatMoney(item.net)}</td></tr>)}<tr className="total"><td>ИТОГО</td><td className="money">{formatMoney(totals.inflow)}</td><td className="money">{formatMoney(totals.outflow)}</td><td className="money">{formatMoney(totals.net)}</td></tr></tbody></table></div></section>
      <section><h3>Расходы по статьям</h3><div className="dds-table-wrap"><table className="dds-table"><thead><tr><th>Статья затрат</th><th>Сумма, ₽</th><th>Доля, %</th></tr></thead><tbody>{categorySummary.map((item) => <tr key={item.category}><td>{item.category}</td><td className="money">{formatMoney(item.value)}</td><td><div className="dds-share"><span style={{ width: `${Math.max(3, item.share * 100)}%` }}></span></div>{(item.share * 100).toFixed(1)}%</td></tr>)}<tr className="total"><td>ИТОГО расходы</td><td className="money">{formatMoney(totals.outflow)}</td><td>100%</td></tr></tbody></table></div></section>
    </div>}
    <footer className="dds-note"><CalendarRange /> Плановые суммы используются до подтверждения факта. Отменённые операции не входят в расчёты.</footer>
  </section>;
}
