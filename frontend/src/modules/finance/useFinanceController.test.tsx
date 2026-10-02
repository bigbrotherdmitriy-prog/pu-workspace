import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../../api/client";
import { useFinanceController } from "./useFinanceController";
import type { FinanceDocumentCandidate, FinanceOverview } from "./types";

vi.mock("../../api/client", () => ({ api: vi.fn() }));

afterEach(cleanup);
beforeEach(() => { vi.mocked(api).mockReset(); });

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((ok, fail) => { resolve = ok; reject = fail; });
  return { promise, resolve, reject };
}

const overview: FinanceOverview = {
  summary: { budget_planned: 0, budget_committed: 0, budget_actual: 0, budget_forecast: 0,
    budget_variance: 0, cash_balance_forecast: 0, cash_gap: 0, delayed_schedule: 0,
    late_procurement: 0, acts_pending: 0, pending_payments: 0, unlinked_invoices: 0 },
  baselines: [], schedule: [], budget: [], cash_flow: [], procurement: [], acts: [],
};
const candidate: FinanceDocumentCandidate = {
  document_id: 91, name: "plan.xlsx", source: "upload", kind: "cash-flow", score: 98,
  reasons: [], hints: {}, already_linked: false, originals_changed: false,
};
const categories = [{ id: 1, name: "ФОТ", is_active: true, sort_order: 1 }];

describe("atomic DDS confirmation", () => {
  it("sends one versioned request for all selected cash-flow rows", async () => {
    const cash = [1, 2].map(id => ({ id, contract_id: 4, direction: "inflow", title: `Synthetic ${id}`,
      planned_date: "2035-01-31", planned_amount: 10, actual_amount: 0, currency: "RUB", status: "proposed", record_version: id + 2 }));
    vi.mocked(api).mockImplementation(async path => path.startsWith("/execution/overview")
      ? { ...overview, cash_flow: cash } : path === "/execution/cash-flow/confirm-batch"
        ? { atomic: true, confirmed_count: 2, rows: cash.map(r => ({ id: r.id, status: "approved" })) }
        : path.startsWith("/execution/document-candidates") ? { candidates: [] } : { categories });
    const { result } = renderHook(() => useFinanceController({ ready: false, projectId: 7, setNotice: vi.fn(), setError: vi.fn() }));
    await act(async () => result.current.loadFinance());
    vi.mocked(api).mockClear();
    await act(async () => result.current.confirmFinanceMany("cash-flow", [1, 2], "approved"));
    const writes = vi.mocked(api).mock.calls.filter(([, init]) => init?.method);
    expect(writes).toHaveLength(1);
    expect(writes[0][0]).toBe("/execution/cash-flow/confirm-batch");
    expect(JSON.parse(String(writes[0][1]?.body))).toEqual({ project_id: 7,
      items: [{ id: 1, expected_record_version: 3 }, { id: 2, expected_record_version: 4 }] });
  });
});

describe("project-scoped independent finance loading", () => {
  it("clears the previous contract and project-bound editor state before loading another project", async () => {
    const pending = deferred<unknown>();
    const setError = vi.fn();
    vi.mocked(api).mockImplementation(async (path) => {
      if (path.includes("project_id=18")) return pending.promise;
      if (path.startsWith("/execution/overview")) return overview;
      if (path.startsWith("/execution/document-candidates")) return { candidates: [candidate] };
      return { categories };
    });
    const { result, rerender } = renderHook(({ projectId }) => useFinanceController({
      ready: true, projectId, setNotice: vi.fn(), setError,
    }), { initialProps: { projectId: 17 } });
    await waitFor(() => expect(result.current.finance).toEqual(overview));
    act(() => {
      result.current.setSelectedFinanceContractId(321);
      result.current.setFinanceScheduleItemId(41);
      result.current.setFinanceBudgetLineId(42);
      result.current.setFinanceSourceDocumentId(43);
      result.current.setFinanceBaselineId(44);
      result.current.setFinanceTitle("Старый проект");
      result.current.setFinanceAmount("123");
      result.current.setFinanceStructuredRows([1]);
    });
    await waitFor(() => expect(api).toHaveBeenCalledWith(expect.stringContaining("contract_id=321")));
    rerender({ projectId: 18 });
    expect(result.current.selectedFinanceContractId).toBe(0);
    expect(result.current.finance).toBeNull();
    expect(result.current.financeCandidates).toEqual([]);
    expect(result.current.costCategories).toEqual([]);
    expect(result.current.financeScheduleItemId).toBe(0);
    expect(result.current.financeBudgetLineId).toBe(0);
    expect(result.current.financeSourceDocumentId).toBe(0);
    expect(result.current.financeBaselineId).toBe(0);
    expect(result.current.financeTitle).toBe("");
    expect(result.current.financeAmount).toBe("");
    expect(result.current.financeStructuredRows).toEqual([]);
    const newRequests = vi.mocked(api).mock.calls.map(([path]) => path).filter(path => path.includes("project_id=18"));
    expect(newRequests.length).toBeGreaterThan(0);
    expect(newRequests.every(path => !path.includes("contract_id="))).toBe(true);
    expect(setError).not.toHaveBeenCalled();
  });

  it.each(["overview", "document-candidates", "cost-categories"])(
    "loads the other two blocks even when %s fails", async (failedBlock) => {
      const setError = vi.fn();
      vi.mocked(api).mockImplementation(async (path) => {
        if (path.startsWith(`/execution/${failedBlock}`)) throw new Error("Недоступен блок");
        if (path.startsWith("/execution/overview")) return overview;
        if (path.startsWith("/execution/document-candidates")) return { candidates: [candidate] };
        return { categories };
      });
      const { result } = renderHook(() => useFinanceController({ ready: false, projectId: 17,
        setNotice: vi.fn(), setError }));
      await act(async () => result.current.loadFinance());
      expect(result.current.finance).toEqual(failedBlock === "overview" ? null : overview);
      expect(result.current.financeCandidates).toEqual(failedBlock === "document-candidates" ? [] : [candidate]);
      expect(result.current.costCategories).toEqual(failedBlock === "cost-categories" ? [] : categories);
      expect(setError).toHaveBeenCalledWith(expect.stringContaining("Недоступен блок"));
    },
  );

  it("publishes each completed block without waiting for a pending sibling", async () => {
    const pending = deferred<unknown>();
    vi.mocked(api).mockImplementation(async (path) => {
      if (path.startsWith("/execution/overview")) return overview;
      if (path.startsWith("/execution/document-candidates")) return pending.promise;
      return { categories };
    });
    const { result } = renderHook(() => useFinanceController({ ready: true, projectId: 17,
      setNotice: vi.fn(), setError: vi.fn() }));
    await waitFor(() => expect(result.current.finance).toEqual(overview));
    expect(result.current.costCategories).toEqual(categories);
    expect(result.current.financeCandidates).toEqual([]);
    await act(async () => pending.resolve({ candidates: [candidate] }));
    expect(result.current.financeCandidates).toEqual([candidate]);
  });

  it("ignores old-project successes and errors after switching away and back", async () => {
    const oldOverview = deferred<unknown>();
    const oldSuggestions = deferred<unknown>();
    const oldCategories = deferred<unknown>();
    const setError = vi.fn();
    let firstLoad = true;
    vi.mocked(api).mockImplementation(async (path) => {
      if (firstLoad) {
        if (path.startsWith("/execution/overview")) return oldOverview.promise;
        if (path.startsWith("/execution/document-candidates")) return oldSuggestions.promise;
        firstLoad = false;
        return oldCategories.promise;
      }
      if (path.startsWith("/execution/overview")) return overview;
      if (path.startsWith("/execution/document-candidates")) return { candidates: [candidate] };
      return { categories };
    });
    const { result, rerender } = renderHook(({ projectId }) => useFinanceController({ ready: true,
      projectId, setNotice: vi.fn(), setError }), { initialProps: { projectId: 17 } });
    rerender({ projectId: 18 });
    await waitFor(() => expect(result.current.finance).toEqual(overview));
    rerender({ projectId: 17 });
    await waitFor(() => expect(result.current.financeCandidates).toEqual([candidate]));
    await act(async () => {
      oldOverview.resolve({ ...overview, cash_flow: [{ id: 999 }] });
      oldSuggestions.resolve({ candidates: [] });
      oldCategories.reject(new Error("Старая ошибка"));
    });
    expect(result.current.finance).toEqual(overview);
    expect(result.current.financeCandidates).toEqual([candidate]);
    expect(result.current.costCategories).toEqual(categories);
    expect(setError).not.toHaveBeenCalled();
  });

  it("ignores an earlier reload in the same project after a newer reload finishes", async () => {
    const older = deferred<unknown>();
    let requestCount = 0;
    vi.mocked(api).mockImplementation(async (path) => {
      requestCount += 1;
      if (requestCount <= 3) return older.promise;
      if (path.startsWith("/execution/overview")) return overview;
      if (path.startsWith("/execution/document-candidates")) return { candidates: [candidate] };
      return { categories };
    });
    const { result } = renderHook(() => useFinanceController({ ready: false, projectId: 17,
      setNotice: vi.fn(), setError: vi.fn() }));
    let first!: Promise<void>;
    act(() => { first = result.current.loadFinance(); });
    await act(async () => result.current.loadFinance());
    await act(async () => { older.resolve({ candidates: [], categories: [] }); await first; });
    expect(result.current.finance).toEqual(overview);
    expect(result.current.financeCandidates).toEqual([candidate]);
    expect(result.current.costCategories).toEqual(categories);
  });

  it("does not reopen a late structured preview after switching projects", async () => {
    const pending = deferred<unknown>();
    const setNotice = vi.fn();
    const setError = vi.fn();
    vi.mocked(api).mockImplementation(async () => pending.promise);
    const { result, rerender } = renderHook(({ projectId }) => useFinanceController({ ready: false,
      projectId, setNotice, setError }), { initialProps: { projectId: 17 } });
    let review!: Promise<void>;
    act(() => { review = result.current.useFinanceCandidate(candidate); });
    rerender({ projectId: 18 });
    await act(async () => {
      pending.resolve({ document_id: 91, name: "old.xlsx", kind: "cash-flow", rows: [], issues: [], mapping: {}, truncated: false });
      await review;
    });
    expect(result.current.financeStructuredPreview).toBeNull();
    expect(setNotice).not.toHaveBeenCalled();
    expect(setError).not.toHaveBeenCalled();
  });

  it.each(["invoice", "uploaded-candidates", "dropped-preview"])(
    "ignores late %s document state after switching projects", async (flow) => {
      const pending = deferred<unknown>();
      const setNotice = vi.fn();
      const setError = vi.fn();
      vi.mocked(api).mockImplementation(async () => pending.promise);
      const { result, rerender } = renderHook(({ projectId }) => useFinanceController({ ready: false,
        projectId, setNotice, setError }), { initialProps: { projectId: 17 } });
      let review!: Promise<void>;
      act(() => {
        review = flow === "invoice" ? result.current.useFinanceCandidate({ ...candidate, kind: "invoice" })
          : flow === "uploaded-candidates" ? result.current.reviewUploadedFinanceDocuments([91])
          : result.current.prepareDroppedFinanceDocument(91, "old.xlsx", "cash-flow", 321);
      });
      rerender({ projectId: 18 });
      await act(async () => {
        pending.resolve({ id: 12, document_id: 91, name: "old.xlsx", rows: [], candidates: [candidate] });
        await review;
      });
      expect(result.current.invoiceExtractionProposal).toBeNull();
      expect(result.current.financeStructuredPreview).toBeNull();
      expect(result.current.financeCandidates).toEqual([]);
      expect(result.current.selectedFinanceContractId).toBe(0);
      expect(setNotice).not.toHaveBeenCalled();
      expect(setError).not.toHaveBeenCalled();
      expect(api).toHaveBeenCalledTimes(1);
    },
  );
});

describe("uploaded finance document routing", () => {
  it("persists DDS cancellation through the status API and reloads finance", async () => {
    const setNotice = vi.fn();
    const setError = vi.fn();
    vi.mocked(api).mockResolvedValue({ cash_flow: [] });
    const { result } = renderHook(() => useFinanceController({ ready: false, projectId: 7, setNotice, setError }));
    await act(async () => { await result.current.confirmFinance("cash-flow", 2, "cancelled"); });
    expect(api).toHaveBeenNthCalledWith(1, "/execution/cash-flow/2/status", {
      method: "PATCH", body: JSON.stringify({ status: "cancelled" }),
    });
    expect(api).toHaveBeenCalledWith(expect.stringContaining("project_id=7"));
    expect(setNotice).toHaveBeenCalledWith("Операция отменена и исключена из расчётов. История сохранена.");
    expect(setError).not.toHaveBeenCalled();
  });

  it("shows a server refusal without reporting cancellation as successful", async () => {
    const setNotice = vi.fn();
    const setError = vi.fn();
    vi.mocked(api).mockImplementation(async () => { throw new Error("Недостаточно прав"); });
    const { result } = renderHook(() => useFinanceController({ ready: false, projectId: 7, setNotice, setError }));
    await result.current.confirmFinance("cash-flow", 2, "cancelled");
    expect(setNotice).not.toHaveBeenCalled();
    expect(setError).toHaveBeenCalledWith("Недостаточно прав");
  });

  it("opens invoice review only for the exact document returned by the upload job", async () => {
    const setNotice = vi.fn();
    const setError = vi.fn();
    const candidate = {
      document_id: 91,
      name: "invoice.pdf",
      kind: "invoice",
      score: 98,
      reasons: ["счёт"],
      hints: {},
      already_linked: false,
    };
    const proposal = {
      id: 17, project_id: 7, source_document_id: 91,
      source_document_version_id: 1, source_document_sha256: "a".repeat(64),
      currency: "RUB", confidence: 0.9, extraction_method: "regex",
      target_kind: "cash_flow", status: "proposed", requires_confirmation: true,
    };
    vi.mocked(api)
      .mockResolvedValueOnce({ candidates: [candidate, { ...candidate, document_id: 92, name: "other.pdf" }] })
      .mockResolvedValueOnce(proposal);
    const { result } = renderHook(() => useFinanceController({
      ready: false, projectId: 7, setNotice, setError,
    }));

    await act(async () => result.current.reviewUploadedFinanceDocuments([91]));

    expect(api).toHaveBeenNthCalledWith(1, "/execution/document-candidates?project_id=7");
    expect(api).toHaveBeenNthCalledWith(2, "/execution/documents/91/invoice-extraction-proposals", {
      method: "POST",
      body: JSON.stringify({ project_id: 7, target_kind: "cash_flow" }),
    });
    expect(result.current.invoiceExtractionProposal).toEqual(proposal);
    expect(setError).not.toHaveBeenCalled();
  });

  it("retries a temporary invoice fallback without uploading the document again", async () => {
    const setNotice = vi.fn();
    const setError = vi.fn();
    const candidate = {
      document_id: 91, name: "invoice.pdf", kind: "invoice", score: 98,
      reasons: ["счёт"], hints: {}, already_linked: false,
    };
    const fallback = {
      id: 17, project_id: 7, source_document_id: 91,
      source_document_version_id: 1, source_document_sha256: "a".repeat(64),
      currency: "RUB", confidence: 0.35, extraction_method: "regex",
      fallback_reason: "temporarily_unavailable", target_kind: "cash_flow",
      status: "proposed", requires_confirmation: true,
    };
    const llm = {
      ...fallback, extraction_method: "llm", fallback_reason: undefined,
      counterparty: "ООО Бетон", payment_purpose: "Материалы", confidence: 0.92,
    };
    vi.mocked(api)
      .mockResolvedValueOnce({ candidates: [candidate] })
      .mockResolvedValueOnce(fallback)
      .mockResolvedValueOnce(llm);
    const { result } = renderHook(() => useFinanceController({
      ready: false, projectId: 7, setNotice, setError,
    }));
    await act(async () => result.current.reviewUploadedFinanceDocuments([91]));

    await act(async () => result.current.retryInvoiceAiAnalysis());

    expect(api).toHaveBeenNthCalledWith(
      3, "/execution/invoice-extraction-proposals/17/retry-ai", { method: "POST" },
    );
    expect(result.current.invoiceExtractionProposal).toEqual(llm);
    expect(setNotice).toHaveBeenLastCalledWith(
      "AI-анализ выполнен повторно. Проверьте обновлённые реквизиты и основания.",
    );
    expect(setError).not.toHaveBeenCalled();
  });
});
