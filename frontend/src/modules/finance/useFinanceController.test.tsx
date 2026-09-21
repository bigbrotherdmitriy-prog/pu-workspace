import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../../api/client";
import { useFinanceController } from "./useFinanceController";

vi.mock("../../api/client", () => ({ api: vi.fn() }));

afterEach(cleanup);
beforeEach(() => vi.mocked(api).mockReset());

describe("uploaded finance document routing", () => {
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
