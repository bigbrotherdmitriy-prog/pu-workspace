import { useEffect, useMemo, useState } from "react";
import { api } from "../../api/client";
import "./purchase.css";

type Invoice = {
  entry_id: number;
  title: string;
  counterparty: string;
  planned_date: string | null;
  amount: string;
  paid: boolean;
  lines: number;
  confirmed: number;
  suggested: number;
};

type InvoiceLine = {
  id: number;
  cash_flow_entry_id: number;
  invoice_title: string;
  counterparty: string;
  name: string;
  unit: string | null;
  qty: string | null;
  match_status: "none" | "suggested" | "confirmed";
  match_score: string | null;
  purchase_item_id: number | null;
  purchase_item_name: string | null;
};

type PurchaseItemOption = { id: number; name: string };
type PurchaseListResponse = { items: PurchaseItemOption[] };

type Props = { projectId: number; onChanged?: () => void };

export function PurchaseInvoices({ projectId, onChanged }: Props) {
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [invoicesLoading, setInvoicesLoading] = useState(false);
  const [invoicesError, setInvoicesError] = useState("");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [lines, setLines] = useState<InvoiceLine[]>([]);
  const [linesLoading, setLinesLoading] = useState(false);
  const [linesError, setLinesError] = useState("");
  const [options, setOptions] = useState<PurchaseItemOption[]>([]);
  const [extractBusy, setExtractBusy] = useState(false);
  const [extractError, setExtractError] = useState("");
  const [choice, setChoice] = useState<Record<number, string>>({});
  const [actionBusy, setActionBusy] = useState<number | null>(null);
  const [actionError, setActionError] = useState<{ lineId: number; message: string } | null>(null);

  useEffect(() => {
    if (projectId <= 0) { setInvoices([]); return; }
    let alive = true;
    setInvoicesLoading(true);
    api<{ invoices: Invoice[] }>(`/projects/${projectId}/purchase/invoices`)
      .then((response) => {
        if (!alive) return;
        setInvoices(Array.isArray(response?.invoices) ? response.invoices : []);
        setInvoicesError("");
      })
      .catch((err: unknown) => {
        if (!alive) return;
        setInvoicesError(err instanceof Error ? err.message : "Не удалось загрузить счета");
      })
      .finally(() => { if (alive) setInvoicesLoading(false); });
    return () => { alive = false; };
  }, [projectId]);

  useEffect(() => {
    if (projectId <= 0) { setOptions([]); return; }
    let alive = true;
    api<PurchaseListResponse>(`/projects/${projectId}/purchase`)
      .then((response) => {
        if (!alive) return;
        setOptions(Array.isArray(response?.items) ? response.items : []);
      })
      .catch(() => { if (alive) setOptions([]); });
    return () => { alive = false; };
  }, [projectId]);

  const selectedInvoice = useMemo(
    () => invoices.find((invoice) => invoice.entry_id === selectedId) ?? null,
    [invoices, selectedId],
  );

  function loadLines(entryId: number) {
    setLinesLoading(true);
    setLinesError("");
    api<InvoiceLine[]>(`/projects/${projectId}/invoice-lines?entry_id=${entryId}`)
      .then((response) => {
        setLines(Array.isArray(response) ? response : []);
        setChoice({});
      })
      .catch((err: unknown) => {
        setLines([]);
        setLinesError(err instanceof Error ? err.message : "Не удалось загрузить строки счёта");
      })
      .finally(() => setLinesLoading(false));
  }

  function reloadInvoices() {
    api<{ invoices: Invoice[] }>(`/projects/${projectId}/purchase/invoices`)
      .then((response) => {
        setInvoices(Array.isArray(response?.invoices) ? response.invoices : []);
      })
      .catch((err: unknown) => {
        setInvoicesError(err instanceof Error ? err.message : "Не удалось загрузить счета");
      });
  }

  function selectInvoice(entryId: number) {
    setSelectedId(entryId);
    setActionError(null);
    setLines([]);
    loadLines(entryId);
  }

  async function extract() {
    if (selectedId === null) return;
    setExtractBusy(true);
    setExtractError("");
    try {
      await api<{ lines: number; suggested: number }>(
        `/projects/${projectId}/cash-flow/${selectedId}/invoice-lines/extract`,
        { method: "POST" },
      );
      loadLines(selectedId);
      reloadInvoices();
      onChanged?.();
    } catch (err) {
      const message = err instanceof Error ? err.message : "Не удалось извлечь строки";
      setExtractError(message);
    } finally {
      setExtractBusy(false);
    }
  }

  async function matchLine(line: InvoiceLine, purchaseItemId: number | null) {
    setActionBusy(line.id);
    setActionError(null);
    try {
      await api<InvoiceLine>(`/projects/${projectId}/invoice-lines/${line.id}/match`, {
        method: "POST",
        body: JSON.stringify({ purchase_item_id: purchaseItemId }),
      });
      if (selectedId !== null) loadLines(selectedId);
      reloadInvoices();
      onChanged?.();
    } catch (err) {
      setActionError({ lineId: line.id, message: err instanceof Error ? err.message : "Не удалось сохранить соответствие" });
    } finally {
      setActionBusy(null);
    }
  }

  function currentChoice(line: InvoiceLine): string {
    const stored = choice[line.id];
    if (stored !== undefined) return stored;
    return line.purchase_item_id === null ? "" : String(line.purchase_item_id);
  }

  function onSelectChange(line: InvoiceLine, value: string) {
    setChoice((prev) => ({ ...prev, [line.id]: value }));
  }

  function onConfirmClick(line: InvoiceLine) {
    const value = currentChoice(line);
    if (!value) return;
    void matchLine(line, Number(value));
  }

  function onClearClick(line: InvoiceLine) {
    void matchLine(line, null);
  }

  return (
    <div className="pr-inv">
      <div className="pr-inv-list">
        <div className="pr-inv-list-title">Счета</div>
        {invoicesError && <div className="pr-error" role="alert">{invoicesError}</div>}
        {invoicesLoading ? (
          <div className="pr-empty">Загрузка…</div>
        ) : invoices.length === 0 ? (
          <div className="pr-empty">Нет счетов с исходным документом</div>
        ) : (
          <ul className="pr-inv-items">
            {invoices.map((invoice) => {
              const active = invoice.entry_id === selectedId;
              return (
                <li key={invoice.entry_id}>
                  <button
                    type="button"
                    className={`pr-inv-item${active ? " pr-inv-item-active" : ""}`}
                    aria-pressed={active}
                    onClick={() => selectInvoice(invoice.entry_id)}
                  >
                    <span className="pr-inv-item-title">{invoice.title || "—"}</span>
                    <span className="pr-inv-item-counterparty">{invoice.counterparty || "—"}</span>
                    <span className="pr-inv-item-meta">
                      <span>{invoice.planned_date || "—"}</span>
                      <span className="pr-inv-amount">{invoice.amount || "—"}</span>
                    </span>
                    <span className="pr-inv-item-meta">
                      <span className={`pr-badge ${invoice.paid ? "pr-badge-ok" : "pr-badge-warn"}`}>
                        {invoice.paid ? "оплачен" : "не оплачен"}
                      </span>
                      <span className="pr-inv-lines">
                        строк {invoice.lines}, подтверждено {invoice.confirmed}
                      </span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <div className="pr-inv-detail">
        {selectedInvoice === null ? (
          <div className="pr-empty">Выберите счёт, чтобы увидеть строки</div>
        ) : (
          <>
            <div className="pr-inv-detail-head">
              <div className="pr-inv-detail-title">{selectedInvoice.title || "Без названия"}</div>
              <button type="button" disabled={extractBusy} onClick={() => void extract()}>
                Извлечь строки из счёта
              </button>
            </div>
            {extractError && <div className="pr-error" role="alert">{extractError}</div>}
            {linesError && <div className="pr-error" role="alert">{linesError}</div>}
            {linesLoading ? (
              <div className="pr-empty">Загрузка…</div>
            ) : lines.length === 0 ? (
              <div className="pr-empty">Строки ещё не извлечены — нажмите «Извлечь строки из счёта»</div>
            ) : (
              <div className="pr-table">
                <table>
                  <thead>
                    <tr>
                      <th>Наименование</th>
                      <th>Ед.</th>
                      <th>Кол-во</th>
                      <th>Статус</th>
                      <th>Позиция закупки</th>
                      <th>Действия</th>
                    </tr>
                  </thead>
                  <tbody>
                    {lines.map((line) => {
                      const statusLabel =
                        line.match_status === "suggested"
                          ? `предложено${line.match_score ? ` ${line.match_score}%` : ""}`
                          : line.match_status === "confirmed"
                            ? "подтверждено"
                            : "нет";
                      const statusClass =
                        line.match_status === "confirmed"
                          ? "pr-badge-ok"
                          : line.match_status === "suggested"
                            ? "pr-badge-warn"
                            : "pr-badge-muted";
                      const value = currentChoice(line);
                      const rowErr = actionError?.lineId === line.id ? actionError.message : "";
                      return (
                        <tr key={line.id}>
                          <td>
                            <div className="pr-name">{line.name || "—"}</div>
                          </td>
                          <td>{line.unit || "—"}</td>
                          <td className="pr-num">{line.qty || "—"}</td>
                          <td>
                            <span className={`pr-badge ${statusClass}`}>{statusLabel}</span>
                          </td>
                          <td>
                            <select
                              className="pr-select"
                              aria-label="Позиция закупки"
                              value={value}
                              onChange={(event) => onSelectChange(line, event.target.value)}
                            >
                              <option value="">—</option>
                              {options.map((option) => (
                                <option key={option.id} value={String(option.id)}>{option.name}</option>
                              ))}
                            </select>
                          </td>
                          <td>
                            <div className="pr-inv-actions">
                              <button
                                type="button"
                                disabled={actionBusy === line.id || !value}
                                onClick={() => onConfirmClick(line)}
                              >
                                Подтвердить
                              </button>
                              {line.match_status === "confirmed" && (
                                <button
                                  type="button"
                                  disabled={actionBusy === line.id}
                                  onClick={() => onClearClick(line)}
                                >
                                  Снять
                                </button>
                              )}
                            </div>
                            {rowErr && <div className="pr-row-error" role="alert">{rowErr}</div>}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

export default PurchaseInvoices;
