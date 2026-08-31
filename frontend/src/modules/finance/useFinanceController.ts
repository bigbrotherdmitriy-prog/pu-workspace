import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { formatMoney } from "../../utils/numberFormat";
import type {
  FinanceDocumentCandidate,
  FinanceOverview,
  FinanceStructuredPreview,
  FinanceStructuredRow,
} from "./types";

type FinanceControllerOptions = {
  ready: boolean;
  projectId: number;
  setNotice: (message: string) => void;
  setError: (message: string) => void;
};

const money = formatMoney;

export function useFinanceController({ ready, projectId, setNotice, setError }: FinanceControllerOptions) {
  const [finance, setFinance] = useState<FinanceOverview | null>(null);
  const [financeCandidates, setFinanceCandidates] = useState<FinanceDocumentCandidate[]>([]);
  const [financeStructuredPreview, setFinanceStructuredPreview] = useState<FinanceStructuredPreview | null>(null);
  const [financeStructuredRows, setFinanceStructuredRows] = useState<number[]>([]);
  const [selectedFinanceContractId, setSelectedFinanceContractId] = useState(0);
  const [financeKind, setFinanceKind] = useState("budget");
  const [financeTitle, setFinanceTitle] = useState("");
  const [financeAmount, setFinanceAmount] = useState("");
  const [financeDate, setFinanceDate] = useState("");
  const [financeExtra, setFinanceExtra] = useState("");
  const [financeSourceDocumentId, setFinanceSourceDocumentId] = useState(0);
  const [financeScheduleItemId, setFinanceScheduleItemId] = useState(0);
  const [financeBudgetLineId, setFinanceBudgetLineId] = useState(0);

  async function loadFinance() {
    if (!projectId) return;
    try {
      const contractQuery = selectedFinanceContractId ? `&contract_id=${selectedFinanceContractId}` : "";
      const [overview, suggestions] = await Promise.all([
        api<FinanceOverview>(`/execution/overview?project_id=${projectId}`),
        api<{ candidates: FinanceDocumentCandidate[] }>(`/execution/document-candidates?project_id=${projectId}${contractQuery}`),
      ]);
      setFinance(overview);
      setFinanceCandidates(suggestions.candidates || []);
    } catch (error) {
      setError((error as Error).message);
    }
  }

  useEffect(() => {
    if (ready && projectId) void loadFinance();
  }, [ready, projectId, selectedFinanceContractId]);

  function prepareFinanceItem(kind: string) {
    setFinanceKind(kind);
    setFinanceTitle("");
    setFinanceAmount("");
    setFinanceDate("");
    setFinanceExtra("");
    setFinanceSourceDocumentId(0);
    window.setTimeout(() => document.getElementById("finance-entry")?.scrollIntoView({ behavior: "smooth", block: "center" }), 0);
  }

  async function useFinanceCandidate(candidate: FinanceDocumentCandidate) {
    if (["schedule", "budget", "cash-flow"].includes(candidate.kind)) {
      try {
        const preview = await api<FinanceStructuredPreview>(`/execution/documents/${candidate.document_id}/structured-preview?project_id=${projectId}&kind=${candidate.kind}`);
        setFinanceStructuredPreview(preview);
        setFinanceStructuredRows(preview.rows.filter((row: FinanceStructuredRow) => row.importable).map((row) => row.source_row));
        setNotice(`Таблица «${candidate.name}» разобрана. Проверьте строки перед пакетным импортом.`);
        window.setTimeout(() => document.getElementById("structured-import")?.scrollIntoView({ behavior: "smooth", block: "start" }), 0);
      } catch (error) {
        setError((error as Error).message);
      }
      return;
    }
    setFinanceKind(candidate.kind);
    setFinanceTitle(candidate.name.replace(/\.[^.]+$/, ""));
    setFinanceAmount(candidate.hints.amount || "");
    setFinanceDate(candidate.hints.date || "");
    setFinanceExtra(candidate.kind === "act" ? candidate.hints.number || "" : "");
    setFinanceSourceDocumentId(candidate.document_id);
    setNotice(`Документ «${candidate.name}» выбран как источник. Проверьте поля и подтвердите предложение.`);
    window.setTimeout(() => document.getElementById("finance-entry")?.scrollIntoView({ behavior: "smooth", block: "center" }), 0);
  }

  async function prepareDroppedFinanceDocument(documentId: number, name: string,
                                                kind: "schedule" | "budget" | "cash-flow",
                                                contractId: number) {
    setSelectedFinanceContractId(contractId);
    try {
      const preview = await api<FinanceStructuredPreview>(`/execution/documents/${documentId}/structured-preview?project_id=${projectId}&kind=${kind}`);
      setFinanceStructuredPreview(preview);
      setFinanceStructuredRows(preview.rows.filter((row) => row.importable).map((row) => row.source_row));
      setNotice(`«${name}» распознан как ${kind === "schedule" ? "ГПР" : kind === "budget" ? "бюджет" : "ДДС"}. Проверьте строки перед созданием предложений.`);
    } catch (error) {
      setError((error as Error).message);
    }
  }

  async function importStructuredFinance() {
    if (!financeStructuredPreview || !financeStructuredRows.length) return;
    const baseline = finance?.baselines.find((row) => row.status === "draft" && (!selectedFinanceContractId || row.contract_id === selectedFinanceContractId));
    if (financeStructuredPreview.kind === "schedule" && !baseline) {
      setError("Для импорта ГПР сначала создайте или выберите черновик baseline этого договора");
      return;
    }
    if (!window.confirm(`Создать ${financeStructuredRows.length} предложений из «${financeStructuredPreview.name}»? Оригинал не изменится.`)) return;
    try {
      const result = await api<{ created: number }>(`/execution/documents/${financeStructuredPreview.document_id}/structured-import`, {
        method: "POST",
        body: JSON.stringify({
          project_id: projectId,
          contract_id: selectedFinanceContractId || null,
          kind: financeStructuredPreview.kind,
          baseline_id: baseline?.id || null,
          direction: "outflow",
          source_rows: financeStructuredRows,
        }),
      });
      setFinanceStructuredPreview(null);
      setFinanceStructuredRows([]);
      setNotice(`Создано предложений: ${result.created}. Исходный файл не изменён.`);
      await loadFinance();
    } catch (error) {
      setError((error as Error).message);
    }
  }

  async function addFinanceItem() {
    const amount = Number(financeAmount || 0);
    if (!financeTitle.trim()) return;
    try {
      let path = "/execution/budget";
      let body: Record<string, unknown> = { project_id: projectId, contract_id: selectedFinanceContractId || null };
      if (financeKind === "budget") body = { ...body, category: financeExtra.trim() || "Прочее", description: financeTitle.trim(), planned_amount: amount };
      if (financeKind === "cash-in" || financeKind === "cash-out") {
        path = "/execution/cash-flow";
        body = { ...body, direction: financeKind === "cash-in" ? "inflow" : "outflow", title: financeTitle.trim(), planned_date: financeDate, planned_amount: amount, counterparty: financeExtra.trim() || null };
      }
      if (financeKind === "invoice") {
        if (!selectedFinanceContractId) throw new Error("Сначала выберите договор для счёта");
        if (!financeScheduleItemId) throw new Error("Свяжите счёт с этапом ГПР");
        if (!financeBudgetLineId) throw new Error("Свяжите счёт со строкой бюджета");
        path = "/execution/invoice-proposals";
        body = { ...body, direction: "outflow", title: financeTitle.trim(), planned_date: financeDate, planned_amount: amount, counterparty: financeExtra.trim() || null, schedule_item_id: financeScheduleItemId || null, budget_line_id: financeBudgetLineId || null, source_document_id: financeSourceDocumentId || null };
      }
      if (financeKind === "procurement") {
        path = "/execution/procurement";
        body = { ...body, title: financeTitle.trim(), supplier: financeExtra.trim() || null, planned_delivery: financeDate || null, planned_amount: amount };
      }
      if (financeKind === "act") {
        path = "/execution/acts";
        body = { ...body, number: financeExtra.trim() || "б/н", title: financeTitle.trim(), act_date: financeDate || null, amount, document_id: financeSourceDocumentId || null };
      }
      if (financeKind === "baseline") {
        path = "/execution/baselines";
        body = { ...body, name: financeTitle.trim(), note: financeExtra.trim() || null };
      }
      if (financeKind === "schedule") {
        const baseline = finance?.baselines.find((row) => row.status === "draft");
        if (!baseline) throw new Error("Сначала создайте черновик версии ГПР");
        path = "/execution/schedule-items";
        body = { baseline_id: baseline.id, title: financeTitle.trim(), planned_finish: financeDate || null, planned_progress: amount };
      }
      await api(path, { method: "POST", body: JSON.stringify(body) });
      setFinanceTitle("");
      setFinanceAmount("");
      setFinanceDate("");
      setFinanceExtra("");
      setNotice("Запись создана как предложение и ожидает подтверждения");
      await loadFinance();
    } catch (error) {
      setError((error as Error).message);
    }
  }

  async function confirmFinance(kind: string, id: number, status: string) {
    try {
      await api(`/execution/${kind}/${id}/status`, { method: "PATCH", body: JSON.stringify({ status }) });
      setNotice("Статус финансовой записи подтверждён и сохранён в аудите");
      await loadFinance();
    } catch (error) { setError((error as Error).message); }
  }

  async function confirmCashPayment(id: number, amount: number) {
    const rawAmount = window.prompt("Фактически оплаченная сумма, ₽", String(amount));
    if (rawAmount === null) return;
    const actualAmount = Number(rawAmount);
    if (!Number.isFinite(actualAmount) || actualAmount <= 0) { setError("Введите корректную сумму оплаты"); return; }
    const actualDate = window.prompt("Дата оплаты, ГГГГ-ММ-ДД", new Date().toISOString().slice(0, 10));
    if (!actualDate) return;
    if (!window.confirm(`Подтвердить оплату ${money(actualAmount)} от ${actualDate}?`)) return;
    try {
      await api(`/execution/cash-flow/${id}/confirm-payment`, { method: "POST", body: JSON.stringify({ actual_amount: actualAmount, actual_date: actualDate }) });
      setNotice("Оплата подтверждена пользователем, факт записан в ДДС и бюджет");
      await loadFinance();
    } catch (error) { setError((error as Error).message); }
  }

  async function updateScheduleActual(id: number) {
    const value = window.prompt("Фактическая готовность, %", "100");
    if (value === null) return;
    const progress = Number(value);
    if (!Number.isFinite(progress) || progress < 0 || progress > 100) {
      setError("Введите число от 0 до 100");
      return;
    }
    try {
      await api(`/execution/schedule-items/${id}`, { method: "PATCH", body: JSON.stringify({ actual_progress: progress, actual_finish: progress === 100 ? new Date().toISOString().slice(0, 10) : null }) });
      setNotice("Факт по работе ГПР обновлён");
      await loadFinance();
    } catch (error) { setError((error as Error).message); }
  }

  async function recordFinanceActual(kind: string, id: number, status: string) {
    const raw = window.prompt("Фактическая сумма, ₽", "0");
    if (raw === null) return;
    const amount = Number(raw);
    if (!Number.isFinite(amount) || amount < 0) {
      setError("Введите корректную сумму");
      return;
    }
    try {
      await api(`/execution/${kind}/${id}/status`, { method: "PATCH", body: JSON.stringify({ status, actual_amount: amount, actual_date: new Date().toISOString().slice(0, 10) }) });
      setNotice("Фактическое исполнение записано и сохранено в аудите");
      await loadFinance();
    } catch (error) { setError((error as Error).message); }
  }

  return {
    finance, financeCandidates, financeStructuredPreview, financeStructuredRows,
    selectedFinanceContractId, financeKind, financeTitle, financeAmount, financeDate,
    financeExtra, financeSourceDocumentId, financeScheduleItemId, financeBudgetLineId,
    setFinanceStructuredPreview, setFinanceStructuredRows, setSelectedFinanceContractId,
    setFinanceKind, setFinanceTitle, setFinanceAmount, setFinanceDate, setFinanceExtra,
    setFinanceSourceDocumentId, setFinanceScheduleItemId, setFinanceBudgetLineId,
    loadFinance, prepareFinanceItem, useFinanceCandidate, prepareDroppedFinanceDocument, importStructuredFinance,
    addFinanceItem, confirmFinance, confirmCashPayment, updateScheduleActual, recordFinanceActual,
  };
}
