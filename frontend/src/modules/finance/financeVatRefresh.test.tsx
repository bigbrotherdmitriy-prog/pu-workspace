import { act, cleanup, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { FinanceOperations } from "./FinanceOperations";
import { DdsWorkspace } from "./DdsWorkspace";
import { useFinanceController } from "./useFinanceController";
import type { FinanceOverview } from "./types";

vi.mock("../../api/client", () => ({ api: vi.fn() }));
afterEach(cleanup);
beforeEach(() => { vi.mocked(api).mockReset(); });

const hash = "a".repeat(64);
const row = { id: 81, contract_id: 41, title: "Synthetic cash proposal", direction: "outflow",
  planned_amount: 122, actual_amount: 0, planned_date: "2035-01-31", currency: "RUB",
  record_version: 2, status: "proposed", vat_snapshot: null, vat_refresh_state_hash: hash,
  vat_contract_record_version: 3, vat_snapshot_stale: false };
const overview = { summary: {}, budget: [], cash_flow: [row], acts: [], baselines: [],
  schedule: [], procurement: [] } as unknown as FinanceOverview;

function routes(post: () => Promise<unknown> = async () => ({ changed: true })) {
  vi.mocked(api).mockImplementation(async path => {
    if (path.endsWith("/refresh-vat")) return post();
    if (path.startsWith("/execution/overview")) return overview;
    if (path.startsWith("/execution/document-candidates")) return { candidates: [] };
    return { categories: [] };
  });
}

describe("explicit pending VAT refresh", () => {
  it("shows the proposed VAT and refresh action in the main DDS details view", () => {
    const refresh = vi.fn(), noop = vi.fn();
    render(<DdsWorkspace finance={{ ...overview, cash_flow: [{ ...row,
      vat_proposed_snapshot: { schema_version: 1, source_contract_id: 41, source_contract_record_version: 3,
        mode: "rate", rate: "0.00" } }] } as FinanceOverview}
      selectedContractId={41} onPrepare={noop} onConfirm={noop} onConfirmMany={noop}
      onConfirmPayment={noop} onLinkControls={noop} onRefreshVat={refresh} />);
    fireEvent.click(screen.getByRole("tab", { name: "Детализация" }));
    expect(screen.getByText(/Предлагается: 0.00 %/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Обновить условия НДС" }));
    expect(refresh).toHaveBeenCalledWith("cash-flow", 81);
  });
  it("sends only server state/contract CAS and an idempotency key, not money or tax values", async () => {
    routes();
    const setNotice = vi.fn(), setError = vi.fn();
    const { result } = renderHook(() => useFinanceController({ ready: false, projectId: 7, setNotice, setError }));
    await act(async () => result.current.loadFinance());
    vi.mocked(api).mockClear();
    await act(async () => { expect(await result.current.refreshFinanceVat("cash-flow", 81)).toBe(true); });
    const call = vi.mocked(api).mock.calls.find(([path]) => path.endsWith("/refresh-vat"))!;
    expect(call[0]).toBe("/execution/cash-flow/81/refresh-vat");
    const body = JSON.parse(call[1]!.body as string);
    expect(body).toEqual({ expected_state_hash: hash, expected_contract_record_version: 3,
      idempotency_key: expect.any(String) });
    expect(body.idempotency_key.length).toBeGreaterThanOrEqual(8);
    expect(result.current.finance?.cash_flow[0].planned_amount).toBe(122);
    expect(result.current.finance?.cash_flow[0].planned_date).toBe("2035-01-31");
    expect(result.current.finance?.cash_flow[0].status).toBe("proposed");
    expect(setNotice).toHaveBeenCalled();
    expect(setError).not.toHaveBeenCalled();
  });

  it("keeps the original request key on retry and shows refusal without success", async () => {
    let attempts = 0;
    routes(async () => { if (++attempts === 1) throw new Error("VAT_STATE_CHANGED"); return { changed: true }; });
    const setNotice = vi.fn(), setError = vi.fn();
    const { result } = renderHook(() => useFinanceController({ ready: false, projectId: 7, setNotice, setError }));
    await act(async () => result.current.loadFinance());
    vi.mocked(api).mockClear();
    await act(async () => { expect(await result.current.refreshFinanceVat("cash-flow", 81)).toBe(false); });
    expect(setError).toHaveBeenCalledWith("VAT_STATE_CHANGED");
    expect(setNotice).not.toHaveBeenCalled();
    await act(async () => { expect(await result.current.refreshFinanceVat("cash-flow", 81)).toBe(true); });
    const calls = vi.mocked(api).mock.calls.filter(([path]) => path.endsWith("/refresh-vat"));
    expect(calls).toHaveLength(2);
    expect(calls[0][1]!.body).toBe(calls[1][1]!.body);
  });

  it("does not refresh a confirmed row", async () => {
    routes();
    vi.mocked(api).mockImplementation(async path => path.startsWith("/execution/overview")
      ? { ...overview, cash_flow: [{ ...row, status: "approved" }] }
      : path.includes("document-candidates") ? { candidates: [] } : { categories: [] });
    const setError = vi.fn();
    const { result } = renderHook(() => useFinanceController({ ready: false, projectId: 7, setNotice: vi.fn(), setError }));
    await act(async () => result.current.loadFinance());
    vi.mocked(api).mockClear();
    await act(async () => { expect(await result.current.refreshFinanceVat("cash-flow", 81)).toBe(false); });
    expect(api).not.toHaveBeenCalled();
    expect(setError).toHaveBeenCalled();
  });

  it("suppresses duplicate requests and ignores an old-project result", async () => {
    let release!: (value: unknown) => void;
    routes(() => new Promise(resolve => { release = resolve; }));
    const setNotice = vi.fn(), setError = vi.fn();
    const { result, rerender } = renderHook(({ projectId }) => useFinanceController({ ready: false, projectId,
      setNotice, setError }), { initialProps: { projectId: 7 } });
    await act(async () => result.current.loadFinance());
    vi.mocked(api).mockClear();
    let pending!: Promise<boolean>;
    act(() => { pending = result.current.refreshFinanceVat("cash-flow", 81); });
    await act(async () => { expect(await result.current.refreshFinanceVat("cash-flow", 81)).toBe(false); });
    expect(vi.mocked(api).mock.calls.filter(([path]) => path.endsWith("/refresh-vat"))).toHaveLength(1);
    rerender({ projectId: 8 });
    await act(async () => { release({ changed: true }); expect(await pending).toBe(false); });
    expect(setNotice).not.toHaveBeenCalled();
    expect(result.current.finance).toBeNull();
  });

  it("offers a single-row action for unknown pending VAT, not for a confirmed row", () => {
    const refresh = vi.fn();
    const noop = vi.fn();
    render(<FinanceOperations finance={{ ...overview, cash_flow: [row, { ...row, id: 82, status: "approved" }] } as FinanceOverview}
      preview={null} selectedRows={[]} setSelectedRows={noop} selectedContractId={0}
      kind="cash-out" title="" amount="" date="" extra="" objectName="" category="" note=""
      sourceDocumentId={0} scheduleItemId={0} budgetLineId={0} baselineId={0}
      setKind={noop} setTitle={noop} setAmount={noop} setDate={noop} setExtra={noop}
      setObjectName={noop} setCategory={noop} setNote={noop} setScheduleItemId={noop}
      setBudgetLineId={noop} setBaselineId={noop} onClosePreview={noop} onImport={noop} onAdd={noop}
      onConfirm={noop} onConfirmPayment={noop} onRefreshVat={refresh} includeEditor={false} />);
    const buttons = screen.getAllByRole("button", { name: "Обновить условия НДС" });
    expect(buttons).toHaveLength(1);
    fireEvent.click(buttons[0]);
    expect(refresh).toHaveBeenCalledWith("cash-flow", 81);
  });
});
