import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { PurchaseInvoices } from "./PurchaseInvoices";
import { PurchaseReport } from "./PurchaseReport";
import "./purchase.css";

type PurchaseStatus = "need" | "ordered" | "paid" | "received";

type PurchaseItem = {
  id: number;
  name: string;
  type_mark: string | null;
  manufacturer: string | null;
  unit: string | null;
  qty_needed: string;
  qty_ordered: string;
  qty_left: string;
  status: PurchaseStatus;
  note: string | null;
  volumes: string[];
};

type PurchaseSummary = {
  total: number;
  need: number;
  ordered: number;
  paid: number;
  received: number;
};

type PurchaseResponse = { items: PurchaseItem[]; summary: PurchaseSummary };

type Props = { projectId: number };

const statusOrder: PurchaseStatus[] = ["need", "ordered", "paid", "received"];

const statusLabel: Record<PurchaseStatus, string> = {
  need: "Нужно заказать",
  ordered: "Заказано",
  paid: "Оплачено",
  received: "Получено",
};

const statusShortLabel: Record<PurchaseStatus, string> = {
  need: "Нужно заказать",
  ordered: "Заказано",
  paid: "Оплачено",
  received: "Получено",
};

const statusClass: Record<PurchaseStatus, string> = {
  need: "pr-status-need",
  ordered: "pr-status-ordered",
  paid: "pr-status-paid",
  received: "pr-status-received",
};

const emptySummary: PurchaseSummary = { total: 0, need: 0, ordered: 0, paid: 0, received: 0 };

export function PurchaseModule({ projectId }: Props) {
  const [items, setItems] = useState<PurchaseItem[]>([]);
  const [summary, setSummary] = useState<PurchaseSummary>(emptySummary);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [rowError, setRowError] = useState<{ id: number; message: string } | null>(null);
  const [filter, setFilter] = useState<PurchaseStatus | null>(null);
  const [rebuildBusy, setRebuildBusy] = useState(false);
  const [rebuildMessage, setRebuildMessage] = useState("");
  const [rebuildError, setRebuildError] = useState("");
  const [exportError, setExportError] = useState("");
  const [exportBusy, setExportBusy] = useState(false);
  const [reloadTick, setReloadTick] = useState(0);
  const [draftQty, setDraftQty] = useState<Record<number, string>>({});
  const [tab, setTab] = useState<"spec" | "invoices" | "report">("spec");

  useEffect(() => {
    if (projectId <= 0) { setItems([]); setSummary(emptySummary); return; }
    let alive = true;
    const controller = new AbortController();
    setLoading(true);
    api<PurchaseResponse>(`/projects/${projectId}/purchase`, { signal: controller.signal })
      .then((response) => {
        if (!alive) return;
        setItems(Array.isArray(response?.items) ? response.items : []);
        setSummary(response?.summary ?? emptySummary);
        setError("");
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (!alive) return;
        setError(err instanceof Error ? err.message : "Не удалось загрузить закупку");
      })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; controller.abort(); };
  }, [projectId, reloadTick]);

  async function rebuild() {
    setRebuildBusy(true);
    setRebuildError("");
    setRebuildMessage("");
    try {
      const result = await api<{ items: PurchaseItem[]; created: number; updated: number; removed: number }>(
        `/projects/${projectId}/purchase/rebuild`,
        { method: "POST" },
      );
      const count = Array.isArray(result?.items) ? result.items.length : 0;
      setRebuildMessage(
        `Строк: ${count}, новых: ${result?.created ?? 0}, обновлено: ${result?.updated ?? 0}, убрано: ${result?.removed ?? 0}`,
      );
      setReloadTick((value) => value + 1);
    } catch (err) {
      setRebuildError(err instanceof Error ? err.message : "Не удалось собрать закупку");
    } finally {
      setRebuildBusy(false);
    }
  }

  async function downloadExcel() {
    setExportBusy(true);
    setExportError("");
    try {
      const response = await fetch(`/projects/${projectId}/purchase/export.xlsx`, { credentials: "same-origin" });
      if (!response.ok) throw new Error(`Не удалось скачать файл (${response.status})`);
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "Закупка.xlsx";
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    } catch (err) {
      setExportError(err instanceof Error ? err.message : "Не удалось скачать файл");
    } finally {
      setExportBusy(false);
    }
  }

  async function patchRow(id: number, patch: Record<string, unknown>) {
    setRowError(null);
    try {
      const updated = await api<PurchaseItem>(`/projects/${projectId}/purchase/${id}`, {
        method: "PATCH",
        body: JSON.stringify(patch),
      });
      setItems((prev) => prev.map((item) => (item.id === id ? { ...item, ...updated } : item)));
      setReloadTick((value) => value + 1);
    } catch (err) {
      setRowError({ id, message: err instanceof Error ? err.message : "Не удалось сохранить позицию" });
      setReloadTick((value) => value + 1);
    }
  }

  function changeStatus(item: PurchaseItem, status: PurchaseStatus) {
    void patchRow(item.id, { status });
  }

  function commitQty(item: PurchaseItem, raw: string) {
    const original = item.qty_ordered ?? "";
    const value = raw.trim();
    setDraftQty((prev) => {
      const next = { ...prev };
      delete next[item.id];
      return next;
    });
    if (value === original) return;
    void patchRow(item.id, { qty_ordered: value });
  }

  const sortedItems = items.slice().sort((a, b) => {
    const ai = statusOrder.indexOf(a.status);
    const bi = statusOrder.indexOf(b.status);
    if (ai !== bi) return ai - bi;
    return a.id - b.id;
  });
  const visibleItems = filter ? sortedItems.filter((item) => item.status === filter) : sortedItems;

  function toggleFilter(status: PurchaseStatus) {
    setFilter((prev) => (prev === status ? null : status));
  }

  const summaryCount = (status: PurchaseStatus): number => {
    if (status === "need") return summary.need ?? 0;
    if (status === "ordered") return summary.ordered ?? 0;
    if (status === "paid") return summary.paid ?? 0;
    return summary.received ?? 0;
  };

  return (
    <div className="pr">
      <div className="pr-tabs" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "spec"}
          className={`pr-tab${tab === "spec" ? " pr-tab-active" : ""}`}
          onClick={() => setTab("spec")}
        >
          Спецификация
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "invoices"}
          className={`pr-tab${tab === "invoices" ? " pr-tab-active" : ""}`}
          onClick={() => setTab("invoices")}
        >
          Счета
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "report"}
          className={`pr-tab${tab === "report" ? " pr-tab-active" : ""}`}
          onClick={() => setTab("report")}
        >
          Отчёт
        </button>
      </div>

      {tab !== "spec" && (
        <div className="pr-tab-panel">
          {tab === "invoices" ? (
            <PurchaseInvoices projectId={projectId} onChanged={() => setReloadTick((value) => value + 1)} />
          ) : (
            <PurchaseReport projectId={projectId} reloadKey={reloadTick} />
          )}
        </div>
      )}

      <div className={`pr-spec${tab === "spec" ? "" : " pr-spec-hidden"}`}>
      <div className="pr-head">
        <h2 className="pr-title">Закупка</h2>
        <div className="pr-actions">
          <button type="button" disabled={rebuildBusy} onClick={() => void rebuild()}>
            Собрать из рабочей документации
          </button>
          <button type="button" disabled={exportBusy} onClick={() => void downloadExcel()}>
            Скачать Excel
          </button>
        </div>
      </div>

      <div className="pr-counters">
        {statusOrder.map((status) => {
          const active = filter === status;
          return (
            <button
              key={status}
              type="button"
              className={`pr-counter ${statusClass[status]}${active ? " pr-counter-active" : ""}`}
              aria-pressed={active}
              onClick={() => toggleFilter(status)}
            >
              <span className="pr-counter-label">{statusShortLabel[status]}</span>
              <span className="pr-counter-value">{summaryCount(status)}</span>
            </button>
          );
        })}
      </div>

      {rebuildMessage && <div className="pr-ok">{rebuildMessage}</div>}
      {rebuildError && <div className="pr-error" role="alert">{rebuildError}</div>}
      {exportError && <div className="pr-error" role="alert">{exportError}</div>}
      {error && <div className="pr-error" role="alert">{error}</div>}

      {visibleItems.length === 0 && !loading ? (
        <div className="pr-empty">
          {items.length === 0
            ? "Сначала подтвердите позиции в разделе «Рабочая документация», затем нажмите «Собрать из рабочей документации»"
            : "По этому фильтру ничего нет"}
        </div>
      ) : (
        <div className="pr-table">
          <table>
            <thead>
              <tr>
                <th>№</th>
                <th>Наименование</th>
                <th>Ед.</th>
                <th>Нужно</th>
                <th>Заказано</th>
                <th>Осталось</th>
                <th>Статус</th>
                <th>Тома</th>
              </tr>
            </thead>
            <tbody>
              {visibleItems.map((item, index) => {
                const draft = draftQty[item.id] ?? item.qty_ordered ?? "";
                const rowErr = rowError?.id === item.id ? rowError.message : "";
                return (
                  <tr key={item.id}>
                    <td className="pr-num">{index + 1}</td>
                    <td>
                      <div className="pr-name">{item.name || "—"}</div>
                      {(item.type_mark || item.manufacturer) && (
                        <div className="pr-sub">
                          {[item.type_mark, item.manufacturer].filter(Boolean).join(" · ")}
                        </div>
                      )}
                    </td>
                    <td>{item.unit || "—"}</td>
                    <td className="pr-num">{item.qty_needed || "—"}</td>
                    <td className="pr-num">
                      <input
                        className="pr-qty"
                        type="number"
                        aria-label="Заказано"
                        value={draft}
                        onChange={(event) => setDraftQty((prev) => ({ ...prev, [item.id]: event.target.value }))}
                        onBlur={(event) => commitQty(item, event.target.value)}
                      />
                    </td>
                    <td className="pr-num">{item.qty_left || "—"}</td>
                    <td>
                      <select
                        className={`pr-status ${statusClass[item.status] || "pr-status-need"}`}
                        aria-label="Статус закупки"
                        value={item.status}
                        onChange={(event) => changeStatus(item, event.target.value as PurchaseStatus)}
                      >
                        {statusOrder.map((status) => (
                          <option key={status} value={status}>{statusLabel[status]}</option>
                        ))}
                      </select>
                      {rowErr && <div className="pr-row-error" role="alert">{rowErr}</div>}
                    </td>
                    <td>{item.volumes && item.volumes.length > 0 ? item.volumes.join(", ") : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      </div>
    </div>
  );
}

export default PurchaseModule;
