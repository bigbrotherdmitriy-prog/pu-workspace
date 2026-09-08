import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useFinanceController } from "./useFinanceController";

const { request } = vi.hoisted(() => ({ request: vi.fn() }));
vi.mock("../../api/client", () => ({ api: request }));
function deferred() {
  let resolve!: (value: unknown) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<unknown>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function setup() {
  const calls: ReturnType<typeof deferred>[] = [];
  request.mockImplementation(() => { const next = deferred(); calls.push(next); return next.promise; });
  const setError = vi.fn();
  const hook = renderHook(({ projectId, ready }) => useFinanceController({ projectId, ready, setError, setNotice: vi.fn() }), {
    initialProps: { projectId: 1, ready: true },
  });
  return { ...hook, calls, setError };
}
async function finish(calls: ReturnType<typeof deferred>[], offset: number, id: number) {
  await act(async () => {
    calls[offset].resolve({ baselines: [{ id }], schedule: [] });
    calls[offset + 1].resolve({ candidates: [] });
  });
}
beforeEach(() => { request.mockReset(); });
afterEach(cleanup);
describe("finance project response isolation", () => {
  it("hides the previous project's graph list immediately on switching", async () => {
    const { result, rerender, calls } = setup();
    await finish(calls, 0, 11);
    expect(result.current.finance?.baselines[0].id).toBe(11);
    rerender({ projectId: 2, ready: true });
    expect(result.current.finance).toBeNull();
    await finish(calls, 2, 22);
    expect(result.current.finance?.baselines[0].id).toBe(22);
  });
  it("ignores a late response from the previous project", async () => {
    const { result, rerender, calls } = setup();
    rerender({ projectId: 2, ready: true });
    await finish(calls, 2, 22);
    await finish(calls, 0, 11);
    expect(result.current.finance?.baselines[0].id).toBe(22);
  });
  it("rejects old responses even after switching back to the same project", async () => {
    const { result, rerender, calls } = setup();
    rerender({ projectId: 2, ready: true });
    rerender({ projectId: 1, ready: true });
    await finish(calls, 4, 33);
    await finish(calls, 0, 11);
    expect(result.current.finance?.baselines[0].id).toBe(33);
  });
  it("suppresses stale errors after losing readiness", async () => {
    const { result, rerender, calls, setError } = setup();
    rerender({ projectId: 1, ready: false });
    await act(async () => { calls[0].reject(new Error("old request")); calls[1].resolve({ candidates: [] }); });
    expect(setError).not.toHaveBeenCalled();
    expect(result.current.finance).toBeNull();
  });
  it("uses the latest reload and does not carry a contract across projects", async () => {
    const { result, rerender, calls } = setup();
    act(() => result.current.setSelectedFinanceContractId(7));
    await waitFor(() => expect(calls).toHaveLength(4));
    rerender({ projectId: 2, ready: true });
    expect(result.current.selectedFinanceContractId).toBe(0);
    expect(request).toHaveBeenLastCalledWith("/execution/document-candidates?project_id=2");
    await finish(calls, 4, 22);
    await finish(calls, 2, 11);
    expect(result.current.finance?.baselines[0].id).toBe(22);
  });
});
