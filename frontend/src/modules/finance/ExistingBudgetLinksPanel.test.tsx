import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api, ApiError } from "../../api/client";
import { ExistingBudgetLinksPanel } from "./ExistingBudgetLinksPanel";

vi.mock("../../api/client", async (importOriginal) => ({ ...(await importOriginal<typeof import("../../api/client")>()), api: vi.fn() }));
const mockApi = vi.mocked(api);
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const proposal = {
  preview_hash: "b".repeat(64), document_version_id: 1001, document_sha256: "a".repeat(64),
  expense_total: "6.98", budget_total: "7.00", difference: "-0.02", currency: "RUB", apply_allowed: true, conflicts: [],
  rows: [{ id: 1101, title: "Материалы (Объект-Б)", amount: "0.98", planned_date: "2026-01-28", status: "proposed",
    record_version: 2, current_budget_line_id: null, budget_line_id: 1201, budget_title: "Материалы (Объект-Б)", category: "Материалы" }],
};
const operation = { operation_id: 1301, link_count: 1, expense_total: "0.98", created_at: "2026-01-01", undone: false, active_link_count: 1, replayed: false };
const options = { projectId: 701, contractId: 801, documentId: 901, planYear: 2026, budgetRevision: 1, onApplied: vi.fn() };
beforeEach(() => {
  vi.clearAllMocks();
  mockApi.mockImplementation(async (path) => path.includes("budget-link-operations?") ? [] : proposal);
});
async function ready() { await screen.findByText(/Расходов: 1/); }
function writes() { return mockApi.mock.calls.filter(([path]) => path.endsWith("budget-links-apply") || path.endsWith("/undo")); }

it("only reads on mount, shows row targets and amounts, and rejects owner cancellation", async () => {
  render(<ExistingBudgetLinksPanel {...options} />); await ready();
  expect(screen.getByText("нет → 1201")).toBeTruthy();
  expect(screen.getByText(/0\.98 RUB/)).toBeTruthy();
  expect(writes()).toHaveLength(0);
  vi.spyOn(window, "confirm").mockReturnValue(false);
  fireEvent.click(screen.getByRole("button", { name: "Подтвердить применение связей" }));
  expect(writes()).toHaveLength(0);
});

it("explicit confirmation pins the preview and every version, without sending financial changes", async () => {
  render(<ExistingBudgetLinksPanel {...options} />); await ready();
  vi.spyOn(window, "confirm").mockReturnValue(true);
  mockApi.mockResolvedValueOnce(operation);
  fireEvent.click(screen.getByRole("button", { name: "Подтвердить применение связей" }));
  await waitFor(() => expect(options.onApplied).toHaveBeenCalledTimes(1));
  const payload = JSON.parse(writes()[0][1]?.body as string);
  expect(payload).toMatchObject({ project_id: 701, contract_id: 801, plan_year: 2026, budget_revision: 1,
    preview_hash: "b".repeat(64), expected_document_version_id: 1001, expected_document_sha256: "a".repeat(64),
    expected_cash_versions: { 1101: 2 }, owner_confirmed: true });
  expect(Object.keys(payload).sort()).toEqual(["budget_revision", "contract_id", "expected_cash_versions", "expected_document_sha256",
    "expected_document_version_id", "idempotency_key", "owner_confirmed", "plan_year", "preview_hash", "project_id"].sort());
  expect(screen.getByRole("button", { name: "Подтвердить применение связей" }).hasAttribute("disabled")).toBe(true);
});

it("double click creates only one request, and an uncertain explicit retry keeps the idempotency key", async () => {
  render(<ExistingBudgetLinksPanel {...options} />); await ready();
  vi.spyOn(window, "confirm").mockReturnValue(true);
  let reject!: (reason: Error) => void;
  mockApi.mockImplementationOnce(() => new Promise((_done, fail) => { reject = fail; }));
  const button = screen.getByRole("button", { name: "Подтвердить применение связей" });
  fireEvent.click(button); fireEvent.click(button);
  expect(writes()).toHaveLength(1);
  await act(async () => { reject(new ApiError("Network uncertainty", null, "synthetic-request")); });
  await screen.findByRole("alert");
  expect(writes()).toHaveLength(1); // No automatic retry.
  mockApi.mockResolvedValueOnce(operation); fireEvent.click(button);
  await waitFor(() => expect(writes()).toHaveLength(2));
  expect(JSON.parse(writes()[0][1]?.body as string).idempotency_key).toBe(JSON.parse(writes()[1][1]?.body as string).idempotency_key);
});

it("CAS rejection blocks further apply until a fresh preview, without overwriting or automatic retry", async () => {
  render(<ExistingBudgetLinksPanel {...options} />); await ready();
  vi.spyOn(window, "confirm").mockReturnValue(true);
  mockApi.mockRejectedValueOnce(new ApiError("CASH_FLOW_VERSION_MISMATCH", 409, "synthetic-request"));
  const button = screen.getByRole("button", { name: "Подтвердить применение связей" });
  fireEvent.click(button); await screen.findByText("CASH_FLOW_VERSION_MISMATCH");
  expect(button.hasAttribute("disabled")).toBe(true);
  fireEvent.click(button); expect(writes()).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "Обновить предпросмотр связей" }));
  await waitFor(() => expect(button.hasAttribute("disabled")).toBe(false));
  expect(writes()).toHaveLength(1);
});

it("ambiguous preview never enables apply", async () => {
  mockApi.mockImplementation(async (path) => path.includes("budget-link-operations?") ? [] : {
    ...proposal, apply_allowed: false, conflicts: [{ code: "ARTICLE_AMBIGUOUS", message: "Дубликат", cash_flow_id: 1101 }],
  });
  render(<ExistingBudgetLinksPanel {...options} />); await screen.findByText(/ARTICLE_AMBIGUOUS/);
  expect(screen.getByRole("button", { name: "Подтвердить применение связей" }).hasAttribute("disabled")).toBe(true);
  expect(writes()).toHaveLength(0);
});

it("persisted history allows only whole-operation undo even when preview fails", async () => {
  mockApi.mockImplementation(async (path) => {
    if (path.includes("budget-link-operations?")) return [operation];
    throw new ApiError("SOURCE_VERSION_MISMATCH", 409, "synthetic-request");
  });
  render(<ExistingBudgetLinksPanel {...options} />);
  const button = await screen.findByRole("button", { name: "Отменить всю операцию #1301" });
  vi.spyOn(window, "confirm").mockReturnValue(false); fireEvent.click(button); expect(writes()).toHaveLength(0);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  mockApi.mockResolvedValueOnce({ ...operation, undone: true, active_link_count: 0 }); fireEvent.click(button);
  await screen.findByText(/отменена целиком/);
  expect(writes()[0]).toEqual(["/execution/budget-link-operations/1301/undo", { method: "POST" }]);
  expect(options.onApplied).toHaveBeenCalledTimes(1);
});

it("late responses from the old project cannot replace the new preview or history", async () => {
  let resolve!: (value: unknown) => void;
  mockApi.mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
  const view = render(<ExistingBudgetLinksPanel {...options} />);
  view.rerender(<ExistingBudgetLinksPanel {...options} projectId={702} documentId={902} />); await ready();
  await act(async () => { resolve({ ...proposal, rows: [{ ...proposal.rows[0], title: "Старый проект" }] }); });
  expect(screen.queryByText(/Старый проект/)).toBeNull();
});

it("a scope/revision change invalidates the old preview immediately and reloads without writes", async () => {
  const view = render(<ExistingBudgetLinksPanel {...options} />); await ready();
  let resolve!: (value: unknown) => void;
  mockApi.mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
  view.rerender(<ExistingBudgetLinksPanel {...options} budgetRevision={2} />);
  expect(screen.getByRole("button", { name: "Подтвердить применение связей" }).hasAttribute("disabled")).toBe(true);
  await act(async () => { resolve(proposal); });
  const call = mockApi.mock.calls.find(([path, init]) => path.endsWith("budget-links-preview") && JSON.parse(init?.body as string).budget_revision === 2);
  expect(call).toBeTruthy(); expect(writes()).toHaveLength(0);
});
