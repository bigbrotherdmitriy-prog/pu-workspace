import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { useContractDetailsState } from "./useContractDetailsState";
import type { ContractRow } from "./types";

vi.mock("../../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/client")>()),
  api: vi.fn(),
}));
beforeEach(() => { vi.mocked(api).mockReset(); });
afterEach(() => { vi.restoreAllMocks(); });

const contract = { id: 41, record_version: 3, number: "C-41", title: "Фасад", status: "active" } as ContractRow;

function setup() {
  const projectIdRef = { current: 1 };
  const callbacks = {
    setContracts: vi.fn(), load: vi.fn().mockResolvedValue(undefined), loadFinance: vi.fn().mockResolvedValue(undefined),
    setNotice: vi.fn(), setError: vi.fn(), setSelectedFinanceContractId: vi.fn(), setGprDdsTab: vi.fn(), setActive: vi.fn(),
  };
  const hook = renderHook(() => useContractDetailsState({ projectId: 1, projectIdRef, contracts: [contract], ...callbacks }));
  /** What the shell does on a project switch: new selection, then the reset. */
  const switchProject = () => act(() => { projectIdRef.current = 2; hook.result.current.resetForProjectChange(); });
  return { hook, callbacks, switchProject };
}

/** An API reply the test releases by hand. */
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((ok, fail) => { resolve = ok; reject = fail; });
  return { promise, resolve, reject };
}

describe("useContractDetailsState — replies after a project switch", () => {
  it("keeps document suggestions of the old project out of the new one", async () => {
    const { hook, callbacks, switchProject } = setup();
    const reply = deferred<unknown>();
    vi.mocked(api).mockReturnValueOnce(reply.promise as Promise<never>);
    let pending!: Promise<void>;
    act(() => { pending = hook.result.current.suggestContractDocuments(41); });
    expect(hook.result.current.contractCandidateBusy).toBe(41);
    switchProject();
    expect(hook.result.current.contractCandidateBusy).toBe(0);
    await act(async () => { reply.resolve({ candidates: [{ document_id: 7, name: "Старый.pdf" }], recommended_document_id: 7 }); await pending; });
    expect(hook.result.current.contractSourceCandidates).toEqual({});
    expect(hook.result.current.contractDocumentTabs).toEqual({});
    expect(callbacks.setNotice).not.toHaveBeenCalled();
  });

  it("does not store an old analysis, reload or report its error", async () => {
    const { hook, callbacks, switchProject } = setup();
    const ok = deferred<unknown>();
    vi.mocked(api).mockReturnValueOnce(ok.promise as Promise<never>);
    let pending!: Promise<void>;
    act(() => { pending = hook.result.current.analyzeContract(41); });
    switchProject();
    await act(async () => {
      ok.resolve({ financial_check: { applied: [], mismatches: [], terms: {} }, analysis: { tasks: 1, obligations: 0 }, source: { name: "Старый.pdf" } });
      await pending;
    });
    expect(hook.result.current.contractFinancialChecks).toEqual({});
    expect(callbacks.load).not.toHaveBeenCalled();
    expect(callbacks.setNotice).not.toHaveBeenCalled();

    const failed = deferred<unknown>();
    vi.mocked(api).mockReturnValueOnce(failed.promise as Promise<never>);
    const { hook: second, callbacks: secondCallbacks, switchProject: switchSecond } = setup();
    act(() => { pending = second.result.current.analyzeContract(41); });
    switchSecond();
    await act(async () => { failed.reject(new Error("Договор не найден")); await pending; });
    expect(secondCallbacks.setError).not.toHaveBeenCalledWith("Договор не найден");
  });

  it("does not open the old project's contract chain on the new project", async () => {
    const { hook, callbacks, switchProject } = setup();
    const reply = deferred<unknown>();
    vi.mocked(api).mockReturnValueOnce(reply.promise as Promise<never>);
    let pending!: Promise<void>;
    act(() => { pending = hook.result.current.openContractControl(41); });
    switchProject();
    await act(async () => { reply.resolve({ created: true }); await pending; });
    expect(callbacks.setSelectedFinanceContractId).not.toHaveBeenCalled();
    expect(callbacks.setGprDdsTab).not.toHaveBeenCalled();
    expect(callbacks.setActive).not.toHaveBeenCalled();
    expect(callbacks.load).not.toHaveBeenCalled();
  });

  it("reports a stale document link as not done", async () => {
    const { hook, callbacks, switchProject } = setup();
    const reply = deferred<unknown>();
    vi.mocked(api).mockReturnValueOnce(reply.promise as Promise<never>);
    let pending!: Promise<boolean>;
    act(() => { pending = hook.result.current.linkContractDocument(41, 7); });
    switchProject();
    let linked: boolean | undefined;
    await act(async () => { reply.resolve({}); linked = await pending; });
    expect(linked).toBe(false);
    expect(callbacks.setNotice).not.toHaveBeenCalled();
  });

  it.each([
    ["proposeContractBudget", (h: ReturnType<typeof setup>["hook"]) => h.result.current.proposeContractBudget(41)],
    ["rejectContractBudget", (h: ReturnType<typeof setup>["hook"]) => h.result.current.rejectContractBudget(9)],
    ["archiveContract", (h: ReturnType<typeof setup>["hook"]) => h.result.current.archiveContract(contract)],
    ["linkContractParent", (h: ReturnType<typeof setup>["hook"]) => h.result.current.linkContractParent(41, "customer", 0)],
  ])("%s ignores a reply from the previous project", async (_name, run) => {
    const { hook, callbacks, switchProject } = setup();
    const reply = deferred<unknown>();
    vi.mocked(api).mockReturnValueOnce(reply.promise as Promise<never>);
    let pending!: Promise<unknown>;
    act(() => { pending = run(hook); });
    switchProject();
    await act(async () => { reply.resolve({ id: 9, amount: 100, description: "Бюджет", operation: "create" }); await pending; });
    expect(callbacks.setNotice).not.toHaveBeenCalled();
    expect(callbacks.load).not.toHaveBeenCalled();
    expect(hook.result.current.contractBudgetDrafts).toEqual({});
  });
});

describe("useContractDetailsState — same project", () => {
  it("still applies suggestions, notices and navigation when the project did not change", async () => {
    const { hook, callbacks } = setup();
    vi.mocked(api)
      .mockResolvedValueOnce({ candidates: [{ document_id: 7, name: "Договор.pdf" }], recommended_document_id: 7 })
      .mockResolvedValueOnce({ created: false });
    await act(async () => { await hook.result.current.suggestContractDocuments(41); });
    expect(hook.result.current.contractSourceCandidates[41]).toHaveLength(1);
    expect(hook.result.current.contractDocumentTabs[41]).toBe("recommended");
    expect(hook.result.current.contractCandidateBusy).toBe(0);
    await act(async () => { await hook.result.current.openContractControl(41); });
    expect(callbacks.setSelectedFinanceContractId).toHaveBeenCalledWith(41);
    expect(callbacks.setActive).toHaveBeenCalledWith("ГПР и ДДС");
    expect(callbacks.load).toHaveBeenCalledTimes(1);
    expect(callbacks.setNotice).toHaveBeenCalledTimes(2);
  });
});
