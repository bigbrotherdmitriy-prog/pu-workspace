import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { formatMoney } from "../../utils/numberFormat";
import type {
  FinanceDocumentCandidate,
  FinanceOverview,
  FinanceStructuredPreview,
  FinanceStructuredRow,
  CostCategory,
  InvoiceExtractionProposal,
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
  const [financeObject, setFinanceObject] = useState("");
  const [financeCategory, setFinanceCategory] = useState("");
  const [financeNote, setFinanceNote] = useState("");
  const [financeSourceDocumentId, setFinanceSourceDocumentId] = useState(0);
  const [financeScheduleItemId, setFinanceScheduleItemId] = useState(0);
  const [financeBudgetLineId, setFinanceBudgetLineId] = useState(0);
  const [financeBaselineId, setFinanceBaselineId] = useState(0);
  const [costCategories, setCostCategories] = useState<CostCategory[]>([]);
  const [invoiceExtractionProposal, setInvoiceExtractionProposal] = useState<InvoiceExtractionProposal | null>(null);
  const [invoiceAiRetrying, setInvoiceAiRetrying] = useState(false);

  async function loadFinance() {
    if (!projectId) return;
    try {
      const contractQuery = selectedFinanceContractId ? `&contract_id=${selectedFinanceContractId}` : "";
      const [overview, suggestions, categoryResult] = await Promise.all([
        api<FinanceOverview>(`/execution/overview?project_id=${projectId}`),
        api<{ candidates: FinanceDocumentCandidate[] }>(`/execution/document-candidates?project_id=${projectId}${contractQuery}`),
        api<{ categories: CostCategory[] }>(`/execution/cost-categories?project_id=${projectId}`),
      ]);
      setFinance(overview);
      setFinanceCandidates(suggestions.candidates || []);
      setCostCategories(categoryResult.categories || []);
    } catch (error) {
      setError((error as Error).message);
    }
  }

  useEffect(() => {
    if (ready && projectId) void loadFinance();
  }, [ready, projectId, selectedFinanceContractId]);

  function prepareFinanceItem(kind: string, baselineId = 0) {
    setFinanceKind(kind);
    setFinanceTitle("");
    setFinanceAmount("");
    setFinanceDate("");
    setFinanceExtra("");
    setFinanceObject("");
    setFinanceCategory("");
    setFinanceNote("");
    setFinanceSourceDocumentId(0);
    setFinanceBaselineId(baselineId);
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
    if (candidate.kind === "invoice") {
      try {
        const proposal = await api<InvoiceExtractionProposal>(`/execution/documents/${candidate.document_id}/invoice-extraction-proposals`, {
          method: "POST",
          body: JSON.stringify({ project_id: projectId, target_kind: "cash_flow" }),
        });
        setInvoiceExtractionProposal(proposal);
        setNotice(`Счёт «${candidate.name}» разобран. Проверьте сумму, назначение и категорию перед подтверждением.`);
        window.setTimeout(() => document.getElementById("invoice-extraction-review")?.scrollIntoView({ behavior: "smooth", block: "start" }), 0);
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

  async function reviewUploadedFinanceDocuments(documentIds: number[]) {
    const requested = new Set(documentIds);
    if (!requested.size) {
      setNotice("Файл обработан, но финансовый документ не был создан. Проверьте качество распознавания.");
      return;
    }
    try {
      const suggestions = await api<{ candidates: FinanceDocumentCandidate[] }>(
        `/execution/document-candidates?project_id=${projectId}`,
      );
      const candidates = suggestions.candidates || [];
      setFinanceCandidates(candidates);
      const uploaded = candidates.filter((candidate) => requested.has(candidate.document_id));
      if (uploaded.length === 1) {
        await useFinanceCandidate(uploaded[0]);
        return;
      }
      if (uploaded.length > 1) {
        setNotice(`Загружено финансовых документов: ${uploaded.length}. Выберите нужный в списке для проверки.`);
        return;
      }
      setNotice("Документ загружен, но не распознан как счёт, акт, ГПР, бюджет или ДДС. Он сохранён в документах проекта.");
    } catch (error) {
      setError((error as Error).message);
    }
  }

  function editInvoiceExtraction(patch: Partial<InvoiceExtractionProposal>) {
    setInvoiceExtractionProposal((current) => current ? { ...current, ...patch } : current);
  }

  async function addCostCategory(name: string) {
    const value = name.trim();
    if (!value) return;
    try {
      await api("/execution/cost-categories", {
        method: "POST", body: JSON.stringify({ project_id: projectId, name: value }),
      });
      await loadFinance();
      setNotice(`Категория «${value}» добавлена в справочник.`);
    } catch (error) { setError((error as Error).message); }
  }

  async function confirmInvoiceExtraction() {
    const proposal = invoiceExtractionProposal;
    if (!proposal) return;
    try {
      const reviewed = await api<InvoiceExtractionProposal>(`/execution/invoice-extraction-proposals/${proposal.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          selected_cost_category_id: proposal.selected_cost_category_id || null,
          amount: proposal.amount || null,
          counterparty: proposal.counterparty || null,
          payment_purpose: proposal.payment_purpose || null,
          planned_date: proposal.planned_date || null,
          target_kind: proposal.target_kind,
        }),
      });
      const confirmed = await api<InvoiceExtractionProposal>(`/execution/invoice-extraction-proposals/${reviewed.id}/confirm`, {
        method: "POST",
        body: JSON.stringify({
          contract_id: selectedFinanceContractId || null,
          schedule_item_id: reviewed.target_kind === "cash_flow" ? financeScheduleItemId || null : null,
          budget_line_id: reviewed.target_kind === "cash_flow" ? financeBudgetLineId || null : null,
        }),
      });
      setInvoiceExtractionProposal(confirmed);
      setNotice("Счёт подтверждён человеком; финансовая строка создана как предложение.");
      await loadFinance();
    } catch (error) { setError((error as Error).message); }
  }

  async function rejectInvoiceExtraction() {
    if (!invoiceExtractionProposal) return;
    try {
      const rejected = await api<InvoiceExtractionProposal>(`/execution/invoice-extraction-proposals/${invoiceExtractionProposal.id}/reject`, { method: "POST" });
      setInvoiceExtractionProposal(rejected);
      setNotice("Предложение по счёту отклонено; финансовые записи не создавались.");
    } catch (error) { setError((error as Error).message); }
  }

  async function retryInvoiceAiAnalysis() {
    const proposal = invoiceExtractionProposal;
    if (!proposal || invoiceAiRetrying) return;
    setInvoiceAiRetrying(true);
    try {
      const retried = await api<InvoiceExtractionProposal>(
        `/execution/invoice-extraction-proposals/${proposal.id}/retry-ai`,
        { method: "POST" },
      );
      setInvoiceExtractionProposal(retried);
      if (retried.extraction_method === "llm") {
        setNotice("AI-анализ выполнен повторно. Проверьте обновлённые реквизиты и основания.");
      } else {
        setNotice("AI пока недоступен. Резервный результат сохранён; можно повторить позже.");
      }
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setInvoiceAiRetrying(false);
    }
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
      window.setTimeout(() => document.getElementById("structured-import")?.scrollIntoView({ behavior: "smooth", block: "start" }), 100);
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
        body = { ...body, direction: financeKind === "cash-in" ? "inflow" : "outflow", title: financeTitle.trim(), planned_date: financeDate, planned_amount: amount, counterparty: financeExtra.trim() || null, object_name: financeObject.trim() || "Общие", category: financeCategory.trim() || (financeKind === "cash-in" ? "Приход от заказчика" : "Прочее"), note: financeNote.trim() || financeTitle.trim() };
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
        if (!financeBudgetLineId) throw new Error("Свяжите акт со строкой бюджета");
        path = "/execution/acts";
        body = { ...body, number: financeExtra.trim() || "б/н", title: financeTitle.trim(), act_date: financeDate || null, amount, document_id: financeSourceDocumentId || null, budget_line_id: financeBudgetLineId };
      }
      if (financeKind === "baseline") {
        path = "/execution/baselines";
        body = { ...body, name: financeTitle.trim(), note: financeExtra.trim() || null };
      }
      if (financeKind === "schedule") {
        const baseline = finance?.baselines.find((row) => row.id === financeBaselineId && row.status === "draft")
          || finance?.baselines.find((row) => row.status === "draft" && (!selectedFinanceContractId || row.contract_id === selectedFinanceContractId));
        if (!baseline) throw new Error("Сначала создайте черновик версии ГПР");
        path = "/execution/schedule-items";
        body = { baseline_id: baseline.id, title: financeTitle.trim(), planned_start: financeDate || null, planned_finish: financeDate || null, duration_days: 1, planned_progress: amount };
      }
      await api(path, { method: "POST", body: JSON.stringify(body) });
      setFinanceTitle("");
      setFinanceAmount("");
      setFinanceDate("");
      setFinanceExtra("");
      setFinanceObject("");
      setFinanceCategory("");
      setFinanceNote("");
      setNotice("Запись создана как предложение и ожидает подтверждения");
      await loadFinance();
    } catch (error) {
      setError((error as Error).message);
    }
  }

  async function linkCashFlowControls(id: number, contractId: number, scheduleItemId: number, budgetLineId: number) {
    try {
      await api(`/execution/cash-flow/${id}/link-controls`, {
        method: "POST",
        body: JSON.stringify({
          contract_id: contractId,
          schedule_item_id: scheduleItemId,
          budget_line_id: budgetLineId,
        }),
      });
      setNotice("ДДС безопасно связан с договором, этапом ГПР и строкой бюджета.");
      await loadFinance();
    } catch (error) { setError((error as Error).message); }
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

  async function confirmFinanceMany(kind: string, ids: number[], status: string) {
    try {
      await Promise.all(ids.map((id) => api(`/execution/${kind}/${id}/status`, { method: "PATCH", body: JSON.stringify({ status }) })));
      setNotice(`Подтверждено записей: ${ids.length}. Изменения сохранены в аудите.`);
      await loadFinance();
    } catch (error) { setError((error as Error).message); }
  }

  async function updateScheduleTask(id: number, patch: Record<string, unknown>) {
    try {
      await api(`/execution/schedule-items/${id}`, { method: "PATCH", body: JSON.stringify(patch) });
      setNotice("Задача ГПР обновлена, сроки и связи сохранены");
      await loadFinance();
    } catch (error) {
      setError((error as Error).message);
    }
  }

  async function bulkUpdateSchedule(baselineId: number, itemIds: number[], patch: Record<string, unknown>) {
    try {
      const result = await api<{ updated_ids: number[]; auto_scheduled_ids: number[] }>("/execution/schedule-items/bulk", {
        method: "PATCH",
        body: JSON.stringify({ baseline_id: baselineId, item_ids: itemIds, ...patch }),
      });
      setNotice(`Обновлено задач: ${result.updated_ids.length}. Автопересчитано: ${result.auto_scheduled_ids.length}.`);
      await loadFinance();
    } catch (error) { setError((error as Error).message); }
  }

  async function cloneScheduleBaseline(baselineId: number) {
    try {
      const result = await api<{ id: number; cloned_item_ids: number[] }>(`/execution/baselines/${baselineId}/clone`, { method: "POST", body: JSON.stringify({}) });
      setNotice(`Создана новая версия ГПР. Скопировано задач: ${result.cloned_item_ids.length}.`);
      await loadFinance();
      return result.id;
    } catch (error) {
      setError((error as Error).message);
      return undefined;
    }
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
    finance, financeCandidates, financeStructuredPreview, financeStructuredRows, costCategories, invoiceExtractionProposal, invoiceAiRetrying,
    selectedFinanceContractId, financeKind, financeTitle, financeAmount, financeDate,
    financeExtra, financeObject, financeCategory, financeNote, financeSourceDocumentId, financeScheduleItemId, financeBudgetLineId, financeBaselineId,
    setFinanceStructuredPreview, setFinanceStructuredRows, setSelectedFinanceContractId,
    setFinanceKind, setFinanceTitle, setFinanceAmount, setFinanceDate, setFinanceExtra, setFinanceObject, setFinanceCategory, setFinanceNote,
    setFinanceSourceDocumentId, setFinanceScheduleItemId, setFinanceBudgetLineId, setFinanceBaselineId,
    setInvoiceExtractionProposal, editInvoiceExtraction,
    loadFinance, prepareFinanceItem, useFinanceCandidate, reviewUploadedFinanceDocuments,
    prepareDroppedFinanceDocument, importStructuredFinance,
    addFinanceItem, addCostCategory, confirmInvoiceExtraction, rejectInvoiceExtraction, retryInvoiceAiAnalysis,
    confirmFinance, confirmFinanceMany, confirmCashPayment, linkCashFlowControls, updateScheduleActual, updateScheduleTask, bulkUpdateSchedule, cloneScheduleBaseline, recordFinanceActual,
  };
}
