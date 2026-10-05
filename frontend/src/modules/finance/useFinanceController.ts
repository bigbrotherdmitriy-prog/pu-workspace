import { useEffect, useLayoutEffect, useRef, useState } from "react";
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
const currentPlanYear = new Date().getFullYear();

export function useFinanceController({ ready, projectId, setNotice, setError }: FinanceControllerOptions) {
  const [finance, setFinance] = useState<FinanceOverview | null>(null);
  const [financeCandidates, setFinanceCandidates] = useState<FinanceDocumentCandidate[]>([]);
  const [financeStructuredPreview, setFinanceStructuredPreview] = useState<FinanceStructuredPreview | null>(null);
  const [financeStructuredRows, setFinanceStructuredRows] = useState<number[]>([]);
  const [selectedFinanceContract, setSelectedFinanceContract] = useState({ projectId, id: 0 });
  // A contract from the previous project must never reach the next request,
  // including the render before the project-reset effect has run.
  const selectedFinanceContractId = selectedFinanceContract.projectId === projectId
    ? selectedFinanceContract.id : 0;
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
  const [invoiceExtractionProposal, setInvoiceExtractionProposalState] = useState<InvoiceExtractionProposal | null>(null);
  const [invoiceConfirmationError, setInvoiceConfirmationError] = useState("");
  const [invoiceConfirming, setInvoiceConfirming] = useState(false);
  const invoiceConfirmationRequest = useRef(false);
  const vatRefreshRequest = useRef(false);
  const vatRefreshKeys = useRef(new Map<string, string>());
  const invoiceReviewGeneration = useRef(0);
  const renderedInvoiceReviewGeneration = invoiceReviewGeneration.current;
  const [invoiceAiRetrying, setInvoiceAiRetrying] = useState(false);
  const projectContext = useRef({ projectId });
  if (projectContext.current.projectId !== projectId) projectContext.current = { projectId };
  const loadSequence = useRef(0);
  const loadContext = useRef({ projectId, contractId: selectedFinanceContractId, ready });
  if (loadContext.current.projectId !== projectId
      || loadContext.current.contractId !== selectedFinanceContractId
      || loadContext.current.ready !== ready) {
    loadContext.current = { projectId, contractId: selectedFinanceContractId, ready };
  }

  function setSelectedFinanceContractId(id: number) {
    if (loadContext.current.projectId === projectId) setSelectedFinanceContract({ projectId, id });
  }

  function captureProject() {
    const context = projectContext.current;
    return () => context.projectId === projectId && projectContext.current === context;
  }

  function setInvoiceExtractionProposal(proposal: InvoiceExtractionProposal | null) {
    // Invalidate pending work immediately, including before the next render.
    invoiceReviewGeneration.current += 1;
    setInvoiceConfirmationError("");
    setInvoiceExtractionProposalState(proposal);
  }

  useLayoutEffect(() => {
    setSelectedFinanceContractId(0);
    setFinance(null);
    setFinanceCandidates([]);
    setCostCategories([]);
    setFinanceStructuredPreview(null);
    setFinanceStructuredRows([]);
    setInvoiceExtractionProposal(null);
    setInvoiceConfirmationError("");
    setInvoiceAiRetrying(false);
    setFinanceKind("budget");
    setFinanceTitle("");
    setFinanceAmount("");
    setFinanceDate("");
    setFinanceExtra("");
    setFinanceObject("");
    setFinanceCategory("");
    setFinanceNote("");
    setFinanceSourceDocumentId(0);
    setFinanceScheduleItemId(0);
    setFinanceBudgetLineId(0);
    setFinanceBaselineId(0);
  }, [projectId]);

  async function loadFinance() {
    if (!projectId || loadContext.current.projectId !== projectId) return;
    const context = loadContext.current;
    const sequence = ++loadSequence.current;
    const current = () => loadContext.current === context && loadSequence.current === sequence;
    const errors: string[] = [];
    const contractQuery = selectedFinanceContractId ? `&contract_id=${selectedFinanceContractId}` : "";
    async function loadBlock<T>(path: string, apply: (value: T) => void, clear: () => void, label: string) {
      try {
        const value = await api<T>(path);
        if (current()) apply(value);
      } catch (error) {
        if (!current()) return;
        clear();
        errors.push(`${label}: ${error instanceof Error ? error.message : "Не удалось загрузить"}`);
        setError(errors.join("; "));
      }
    }
    // Each block publishes its own response immediately. A slow or failed
    // sibling neither hides successful data nor leaves failed data stale.
    await Promise.allSettled([
      loadBlock<FinanceOverview>(`/execution/overview?project_id=${projectId}`,
        setFinance, () => setFinance(null), "Финансовый обзор"),
      loadBlock<{ candidates: FinanceDocumentCandidate[] }>(`/execution/document-candidates?project_id=${projectId}${contractQuery}`,
        value => setFinanceCandidates(value.candidates || []), () => setFinanceCandidates([]), "Финансовые документы"),
      loadBlock<{ categories: CostCategory[] }>(`/execution/cost-categories?project_id=${projectId}`,
        value => setCostCategories(value.categories || []), () => setCostCategories([]), "Статьи затрат"),
    ]);
  }

  useEffect(() => {
    if (ready && projectId) void loadFinance();
    return () => { loadSequence.current += 1; };
  }, [ready, projectId, selectedFinanceContractId]);

  function prepareFinanceItem(kind: string, baselineId = 0) {
    // Starting a manual entry is an explicit context switch.  Do not leave a
    // previously confirmed invoice or spreadsheet review covering the editor.
    setInvoiceExtractionProposal(null);
    setInvoiceConfirmationError("");
    setFinanceStructuredPreview(null);
    setFinanceStructuredRows([]);
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
    const current = captureProject();
    if (!current()) return;
    if (["schedule", "budget", "cash-flow"].includes(candidate.kind)) {
      try {
        const preview = await api<FinanceStructuredPreview>(`/execution/documents/${candidate.document_id}/structured-preview?project_id=${projectId}&kind=${candidate.kind}&plan_year=${currentPlanYear}`);
        if (!current()) return;
        setFinanceStructuredPreview(preview);
        setFinanceStructuredRows(preview.rows.filter((row: FinanceStructuredRow) => row.importable).map((row) => row.selection_id));
        setNotice(`Таблица «${candidate.name}» разобрана. Проверьте строки перед пакетным импортом.`);
        window.setTimeout(() => document.getElementById("structured-import")?.scrollIntoView({ behavior: "smooth", block: "start" }), 0);
      } catch (error) {
        if (current()) setError((error as Error).message);
      }
      return;
    }
    if (candidate.kind === "invoice") {
      invoiceReviewGeneration.current += 1;
      setInvoiceConfirmationError("");
      try {
        const proposal = await api<InvoiceExtractionProposal>(`/execution/documents/${candidate.document_id}/invoice-extraction-proposals`, {
          method: "POST",
          body: JSON.stringify({ project_id: projectId, target_kind: "cash_flow" }),
        });
        if (!current()) return;
        setInvoiceExtractionProposal(proposal);
        setNotice(`Счёт «${candidate.name}» разобран. Проверьте сумму, назначение и категорию перед подтверждением.`);
        window.setTimeout(() => document.getElementById("invoice-extraction-review")?.scrollIntoView({ behavior: "smooth", block: "start" }), 0);
      } catch (error) {
        if (current()) setError((error as Error).message);
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
    const current = captureProject();
    if (!current()) return;
    const requested = new Set(documentIds);
    if (!requested.size) {
      setNotice("Файл обработан, но финансовый документ не был создан. Проверьте качество распознавания.");
      return;
    }
    try {
      const suggestions = await api<{ candidates: FinanceDocumentCandidate[] }>(
        `/execution/document-candidates?project_id=${projectId}`,
      );
      if (!current()) return;
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
      if (current()) setError((error as Error).message);
    }
  }

  function editInvoiceExtraction(patch: Partial<InvoiceExtractionProposal>) {
    invoiceReviewGeneration.current += 1;
    setInvoiceExtractionProposalState((current) => current ? { ...current, ...patch } : current);
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

  async function confirmInvoiceExtraction(): Promise<boolean> {
    const currentProject = captureProject();
    const current = () => currentProject()
      && invoiceReviewGeneration.current === renderedInvoiceReviewGeneration;
    if (!current() || invoiceConfirmationRequest.current) return false;
    setInvoiceConfirmationError("");
    const proposal = invoiceExtractionProposal;
    if (!proposal || !projectId || proposal.project_id !== projectId) return false;
    invoiceConfirmationRequest.current = true;
    setInvoiceConfirming(true);
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
      if (!current()) return false;
      const confirmed = await api<InvoiceExtractionProposal>(`/execution/invoice-extraction-proposals/${reviewed.id}/confirm`, {
        method: "POST",
        body: JSON.stringify({
          contract_id: selectedFinanceContractId || null,
          schedule_item_id: reviewed.target_kind === "cash_flow" ? financeScheduleItemId || null : null,
          budget_line_id: reviewed.target_kind === "cash_flow" ? financeBudgetLineId || null : null,
        }),
      });
      if (!current()) return false;
      // Applying this request's result keeps its own review generation current.
      setInvoiceExtractionProposalState(confirmed);
      setNotice("Счёт подтверждён человеком; финансовая строка создана как предложение.");
      await loadFinance();
      return current();
    } catch (error) {
      if (current()) {
        const message = error instanceof Error ? error.message : "Не удалось подтвердить счёт.";
        setInvoiceConfirmationError(message);
        setError(message);
      }
      return false;
    } finally {
      invoiceConfirmationRequest.current = false;
      setInvoiceConfirming(false);
    }
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
    const current = captureProject();
    if (!current()) return;
    setSelectedFinanceContractId(contractId);
    try {
      const preview = await api<FinanceStructuredPreview>(`/execution/documents/${documentId}/structured-preview?project_id=${projectId}&kind=${kind}&plan_year=${currentPlanYear}`);
      if (!current()) return;
      setFinanceStructuredPreview(preview);
      setFinanceStructuredRows(preview.rows.filter((row) => row.importable).map((row) => row.selection_id));
      setNotice(`«${name}» распознан как ${kind === "schedule" ? "ГПР" : kind === "budget" ? "бюджет" : "ДДС"}. Проверьте строки перед созданием предложений.`);
      window.setTimeout(() => document.getElementById("structured-import")?.scrollIntoView({ behavior: "smooth", block: "start" }), 100);
    } catch (error) {
      if (current()) setError((error as Error).message);
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
          row_overrides: Object.fromEntries(financeStructuredPreview.rows
            .filter((row) => financeStructuredRows.includes(row.selection_id))
            .map((row) => [row.selection_id, {
              title: row.title,
              planned_date: row.planned_date || undefined,
              amount: row.amount || undefined,
              direction: row.direction || undefined,
              category: row.category || undefined,
            }])),
          plan_year: financeStructuredPreview.plan_year || null,
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

  function editStructuredFinanceRow(selectionId: number, patch: Record<string, string>) {
    setFinanceStructuredPreview((current) => current ? {
      ...current,
      rows: current.rows.map((row) => row.selection_id === selectionId ? { ...row, ...patch } : row),
    } : current);
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

  async function mutateCashFlowPlan(id: number, operation: "edit" | "move" | "copy", plannedDate: string,
                                    plannedAmount: number, expectedRecordVersion: number) {
    const result = await api<{ mutation_id: number; result_id: number; record_version: number }>(
      `/execution/cash-flow/${id}/plan-mutations`, {
        method: "POST",
        body: JSON.stringify({
          operation, planned_date: plannedDate, planned_amount: plannedAmount,
          expected_record_version: expectedRecordVersion,
          idempotency_key: crypto.randomUUID(),
        }),
      },
    );
    setNotice(operation === "copy" ? "Плановая сумма скопирована. Факт не изменён." : "План ДДС обновлён. Факт не изменён.");
    await loadFinance();
    return result;
  }

  async function undoCashFlowPlanMutation(mutationId: number) {
    await api(`/execution/cash-flow/plan-mutations/${mutationId}/undo`, { method: "POST" });
    setNotice("Последнее изменение плана ДДС отменено.");
    await loadFinance();
  }

  async function confirmFinance(kind: string, id: number, status: string) {
    try {
      await api(`/execution/${kind}/${id}/status`, { method: "PATCH", body: JSON.stringify({ status }) });
      setNotice(status === "cancelled"
        ? "Операция отменена и исключена из расчётов. История сохранена."
        : "Статус финансовой записи подтверждён и сохранён в аудите");
      await loadFinance();
    } catch (error) { setError((error as Error).message); }
  }

  async function refreshFinanceVat(kind: string, id: number): Promise<boolean> {
    if (vatRefreshRequest.current || projectContext.current.projectId !== projectId) return false;
    const current = captureProject();
    const rows = kind === "budget" ? finance?.budget : kind === "cash-flow" ? finance?.cash_flow
      : kind === "acts" ? finance?.acts : undefined;
    const row = rows?.find(item => item.id === id);
    if (!row || row.status !== "proposed" || !row.vat_refresh_state_hash
        || !row.vat_contract_record_version) {
      setError("Обновите экран: НДС можно принять только для неподтверждённой записи с договором.");
      return false;
    }
    const intent = `${projectId}:${kind}:${id}:${row.vat_refresh_state_hash}:${row.vat_contract_record_version}`;
    let key = vatRefreshKeys.current.get(intent);
    if (!key) {
      key = crypto.randomUUID();
      vatRefreshKeys.current.set(intent, key);
    }
    vatRefreshRequest.current = true;
    try {
      await api(`/execution/${kind}/${id}/refresh-vat`, { method: "POST", body: JSON.stringify({
        expected_state_hash: row.vat_refresh_state_hash,
        expected_contract_record_version: row.vat_contract_record_version,
        idempotency_key: key,
      }) });
      if (!current()) return false;
      setNotice("Условия НДС приняты для одной записи. Сумма, даты и статус не изменены.");
      await loadFinance();
      return current();
    } catch (error) {
      if (current()) setError(error instanceof Error ? error.message : "Не удалось принять условия НДС.");
      return false;
    } finally {
      vatRefreshRequest.current = false;
    }
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
    if (kind === "cash-flow") {
      try {
        if (status !== "approved") throw new Error("Пакет ДДС поддерживает только подтверждение плана");
        const selected = ids.map(id => finance?.cash_flow.find(row => row.id === id));
        if (selected.some(row => !row)) throw new Error("Состав ДДС изменился. Обновите экран перед подтверждением.");
        const result = await api<{ atomic: boolean; confirmed_count: number; rows: { id: number; status: string }[] }>(
          "/execution/cash-flow/confirm-batch", { method: "POST", body: JSON.stringify({ project_id: projectId,
            items: selected.map(row => ({ id: row!.id, expected_record_version: row!.record_version })) }) });
        if (!result.atomic || result.confirmed_count !== ids.length || result.rows.length !== ids.length
          || result.rows.some(row => row.status !== "approved" || !ids.includes(row.id))) {
          throw new Error("Ответ подтверждения неясен. Обновите экран и проверьте аудит; не повторяйте вслепую.");
        }
        if (projectContext.current.projectId === projectId) {
          setNotice(`Атомарно подтверждено записей: ${result.confirmed_count}. Фактические платежи не создавались.`);
          await loadFinance();
        }
        return;
      } catch (error) {
        if (projectContext.current.projectId === projectId) setError((error as Error).message);
        throw error;
      }
    }
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
    finance, financeCandidates, financeStructuredPreview, financeStructuredRows, costCategories, invoiceExtractionProposal, invoiceConfirmationError, invoiceConfirming, invoiceAiRetrying,
    selectedFinanceContractId, financeKind, financeTitle, financeAmount, financeDate,
    financeExtra, financeObject, financeCategory, financeNote, financeSourceDocumentId, financeScheduleItemId, financeBudgetLineId, financeBaselineId,
    setFinanceStructuredPreview, setFinanceStructuredRows, setSelectedFinanceContractId,
    setFinanceKind, setFinanceTitle, setFinanceAmount, setFinanceDate, setFinanceExtra, setFinanceObject, setFinanceCategory, setFinanceNote,
    setFinanceSourceDocumentId, setFinanceScheduleItemId, setFinanceBudgetLineId, setFinanceBaselineId,
    setInvoiceExtractionProposal, editInvoiceExtraction,
    loadFinance, prepareFinanceItem, useFinanceCandidate, reviewUploadedFinanceDocuments,
    prepareDroppedFinanceDocument, importStructuredFinance, editStructuredFinanceRow,
    addFinanceItem, addCostCategory, confirmInvoiceExtraction, rejectInvoiceExtraction, retryInvoiceAiAnalysis,
    confirmFinance, confirmFinanceMany, refreshFinanceVat, confirmCashPayment, linkCashFlowControls, mutateCashFlowPlan, undoCashFlowPlanMutation,
    updateScheduleActual, updateScheduleTask, bulkUpdateSchedule, cloneScheduleBaseline, recordFinanceActual,
  };
}
