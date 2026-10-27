import { useEffect, useMemo, useState } from "react";
import { api } from "../../api/client";
import { formatMoney } from "../../utils/numberFormat";
import "./receipts-by-basis.css";

type Doc = {
  id: number;
  title: string;
  kind: "КС-3" | "КС-2" | "УПД" | "Акт" | "Счёт" | "Документ";
  date: string | null;
  amount: string;
  received: string;
  remaining: string;
  state: "received" | "overdue" | "expected";
  source_document_id: number | null;
};
type ContractTotals = { invoiced: string; received: string; remaining: string };
type Contract = { contract_id: number | null; number: string | null; counterparty: string | null; documents: Doc[]; totals: ContractTotals };
type Response = { contracts: Contract[]; totals: ContractTotals };

type Props = { projectId: number; onChanged?: () => void };

type Draft = { id: number; amount: string; date: string };

const n = (value: string | number | null | undefined) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
};

const dayFormat = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric", timeZone: "UTC" });
const day = (value: string | null | undefined) => {
  if (!value) return "—";
  const date = new Date(`${value.slice(0, 10)}T00:00:00Z`);
  return Number.isNaN(date.getTime()) ? "—" : dayFormat.format(date);
};
const today = () => new Date().toISOString().slice(0, 10);

const stateLabel: Record<Doc["state"], string> = { received: "получено", overdue: "просрочено", expected: "ждём" };
const stateClass: Record<Doc["state"], string> = { received: "status-received", overdue: "status-overdue", expected: "status-expected" };
const contractTitle = (contract: Contract) => `${contract.counterparty || "Без договора"}${contract.number ? ` · ${contract.number}` : ""}`;

function csvCell(value: string) {
  const text = String(value ?? "").replace(/"/g, '""');
  return `"${text}"`;
}
function csvNumber(value: string) {
  return String(value ?? "").replace(".", ",");
}
function fileStamp() {
  const now = new Date();
  const dd = String(now.getDate()).padStart(2, "0");
  const mm = String(now.getMonth() + 1).padStart(2, "0");
  return `${dd}${mm}${now.getFullYear()}`;
}

export function ReceiptsByBasis({ projectId, onChanged }: Props) {
  const [data, setData] = useState<Response | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [rowError, setRowError] = useState<{ id: number; message: string } | null>(null);
  const [busy, setBusy] = useState<number | null>(null);

  useEffect(() => {
    if (projectId <= 0) { setData(null); return; }
    let alive = true;
    const controller = new AbortController();
    setLoading(true);
    api<Response>(`/projects/${projectId}/receipts-by-basis`, { signal: controller.signal })
      .then((response) => {
        if (!alive) return;
        if (response && Array.isArray(response.contracts)) { setData(response); setError(""); }
        else { setData({ contracts: [], totals: { invoiced: "0", received: "0", remaining: "0" } }); setError(""); }
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (!alive) return;
        setError(err instanceof Error ? err.message : "Не удалось загрузить приходы по основаниям");
      })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; controller.abort(); };
  }, [projectId, reload]);

  const contracts = data?.contracts ?? [];
  const documents = useMemo(() => contracts.flatMap((contract) => contract.documents.map((doc) => ({ contract, doc }))), [contracts]);

  function exportCsv() {
    const header = ["Контрагент", "Договор", "Тип", "Документ", "Дата", "Выставлено", "Получено", "Остаток", "Статус"];
    const rows = documents.map(({ contract, doc }) => [
      csvCell(contract.counterparty || "Без договора"),
      csvCell(contract.number || ""),
      csvCell(doc.kind),
      csvCell(doc.title),
      csvCell(day(doc.date)),
      csvCell(csvNumber(doc.amount)),
      csvCell(csvNumber(doc.received)),
      csvCell(csvNumber(doc.remaining)),
      csvCell(stateLabel[doc.state] || ""),
    ].join(";"));
    const totals = data?.totals ?? { invoiced: "0", received: "0", remaining: "0" };
    rows.push(["Итого", "", "", "", "", csvCell(csvNumber(totals.invoiced)), csvCell(csvNumber(totals.received)), csvCell(csvNumber(totals.remaining)), ""].join(";"));
    const csv = `\uFEFF${header.join(";")}\r\n${rows.join("\r\n")}`;
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `Приходы_по_основаниям_${fileStamp()}.csv`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  }

  async function save(receipt: Doc) {
    if (!draft) return;
    const raw = draft.amount.replace(/\s/g, "").replace(",", ".");
    if (!(Number(raw) > 0) || !draft.date) return;
    setBusy(receipt.id);
    setRowError(null);
    try {
      await api(`/projects/${projectId}/cash-flow/${receipt.id}/record-receipt`, {
        method: "POST",
        body: JSON.stringify({ amount: Number(raw).toFixed(2), actual_date: draft.date }),
      });
      setDraft(null);
      setReload((v) => v + 1);
      onChanged?.();
    } catch (err) {
      setRowError({ id: receipt.id, message: err instanceof Error ? err.message : "Не удалось сохранить поступление" });
    } finally {
      setBusy(null);
    }
  }

  if (error) return <div className="receipts-basis-error" role="alert">{error}</div>;
  if (!data) return <div className="receipts-basis-empty">{loading ? "Загружаем приходы…" : "Нет данных по приходам"}</div>;
  if (!contracts.length) return <div className="receipts-basis-empty">Приходов по основаниям пока нет</div>;

  const totals = data.totals;

  return (
    <div className="receipts-basis">
      <div className="receipts-basis-actions">
        <button type="button" onClick={exportCsv}>Выгрузить CSV</button>
      </div>
      {contracts.map((contract) => (
        <div key={contract.contract_id ?? `none-${contract.number ?? ""}`} className="receipts-basis-contract">
          <div className="receipts-basis-contract-head">{contractTitle(contract)}</div>
          <div className="receipts-basis-table">
            <table>
              <thead>
                <tr>
                  <th>Тип</th>
                  <th>Документ</th>
                  <th>Дата</th>
                  <th>Выставлено</th>
                  <th>Получено</th>
                  <th>Остаток</th>
                  <th>Статус</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {contract.documents.map((doc) => {
                  const canReceive = n(doc.remaining) > 0.009;
                  const isOpen = draft?.id === doc.id;
                  return (
                    <tr key={doc.id}>
                      <td>{doc.kind}</td>
                      <td>{doc.title}</td>
                      <td>{day(doc.date)}</td>
                      <td className="money">{formatMoney(n(doc.amount))}</td>
                      <td className="money">{formatMoney(n(doc.received))}</td>
                      <td className="money">{formatMoney(n(doc.remaining))}</td>
                      <td><span className={`receipts-basis-status ${stateClass[doc.state] || "status-expected"}`}>{stateLabel[doc.state] || "ждём"}</span></td>
                      <td className="action">
                        {canReceive
                          ? isOpen
                            ? null
                            : <button type="button" disabled={busy === doc.id} onClick={() => { setRowError(null); setDraft({ id: doc.id, amount: n(doc.remaining).toFixed(2), date: today() }); }}>Получено</button>
                          : null}
                        {isOpen && draft && (
                          <span className="receipts-basis-form">
                            <input
                              id={`receipt-amount-${doc.id}`}
                              aria-label="Сумма поступления"
                              inputMode="decimal"
                              value={draft.amount}
                              onChange={(event) => setDraft({ ...draft, amount: event.target.value })}
                            />
                            <input
                              id={`receipt-date-${doc.id}`}
                              aria-label="Дата поступления"
                              type="date"
                              value={draft.date}
                              onChange={(event) => setDraft({ ...draft, date: event.target.value })}
                            />
                            <button type="button" disabled={busy === doc.id || !draft.date || !(Number(draft.amount.replace(",", ".")) > 0)} onClick={() => void save(doc)}>Сохранить</button>
                            <button type="button" className="secondary" onClick={() => setDraft(null)}>Отмена</button>
                          </span>
                        )}
                        {rowError?.id === doc.id && <span className="receipts-basis-row-error" role="alert">{rowError.message}</span>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
              <tfoot>
                <tr>
                  <td colSpan={3}>Итого по договору</td>
                  <td className="money">{formatMoney(n(contract.totals?.invoiced))}</td>
                  <td className="money">{formatMoney(n(contract.totals?.received))}</td>
                  <td className="money">{formatMoney(n(contract.totals?.remaining))}</td>
                  <td colSpan={2} />
                </tr>
              </tfoot>
            </table>
          </div>
        </div>
      ))}
      <div className="receipts-basis-totals">
        <span>Выставлено: <b>{formatMoney(n(totals?.invoiced))}</b></span>
        <span>Получено: <b>{formatMoney(n(totals?.received))}</b></span>
        <span>Долг заказчика: <b>{formatMoney(n(totals?.remaining))}</b></span>
      </div>
    </div>
  );
}

export default ReceiptsByBasis;
