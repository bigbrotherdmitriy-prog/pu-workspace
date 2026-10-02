import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../../api/client";

vi.mock("../../api/client", () => ({ api: vi.fn() }));
const mockApi = vi.mocked(api);
afterEach(cleanup);

const proposal = {
  document_id: 2428, document_version_id: 1810, document_sha256: "a".repeat(64),
  preview_hash: "b".repeat(64), expense_total: "2392718.00", currency: "RUB",
  conflicts: [], warnings: ["Годовой итог отличается на 14000.00"],
  articles: [{ article_id: 2, title: "Материалы (Городец)", source_coordinate: "ДДС!12",
    monthly_total: "2392718.00", annual_total: "2378718.00", annual_difference: "-14000.00",
    budget_line_id: null, months: [{ month: 7, source_coordinate: "ДДС!I12", raw_amount: "14000",
      ordinary_amount: "14000.00", amount: "14000.00", adjustment: "0.00" }] }],
  existing_rows: [{ id: 29, title: "Материалы (Городец)", source_coordinate: "ДДС!C12",
    amount: "14811906.53", planned_date: "2026-01-31", status: "proposed", record_version: 2,
    current_budget_line_id: null, proposed_budget_line_id: null, proposed_article_id: 2, source_difference: "-0.02" }],
};
const options = { projectId: 17, contractId: 25, documentId: 2428, planYear: 2026,
  categories: [{ id: 1, name: "Материалы", is_active: true, sort_order: 1 }], onApplied: vi.fn() };

beforeEach(() => { vi.clearAllMocks(); mockApi.mockResolvedValue(proposal); });
async function panel() { return (await import("./ArticleBudgetPreviewPanel")).ArticleBudgetPreviewPanel; }

it("initially requests read-only preview and shows annual warning and old manual change", async () => {
  const Panel = await panel(); render(<Panel {...options} />);
  expect(await screen.findByText("Годовой итог отличается на 14000.00")).toBeTruthy();
  expect(screen.getByText(/Ручное отличие: -0.02/)).toBeTruthy();
  expect(mockApi.mock.calls.every(([path]) => path.endsWith("article-budget-preview"))).toBe(true);
  expect(screen.getByText(/Существующие записи.*не изменяются/)).toBeTruthy();
});

it("category and revision changes invalidate preview until refreshed", async () => {
  const Panel = await panel(); render(<Panel {...options} />);
  await screen.findByText("Годовой итог отличается на 14000.00");
  fireEvent.change(screen.getByLabelText("Категория статьи Материалы (Городец)"), { target: { value: "1" } });
  expect(screen.getByRole("button", { name: /Подтвердить создание/ }).hasAttribute("disabled")).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Обновить предпросмотр бюджета" }));
  await waitFor(() => expect(mockApi).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(screen.getByRole("button", { name: /Подтвердить создание/ }).hasAttribute("disabled")).toBe(false));
  fireEvent.change(screen.getByLabelText("Ревизия расходного бюджета"), { target: { value: "2" } });
  expect(screen.getByRole("button", { name: /Подтвердить создание/ }).hasAttribute("disabled")).toBe(true);
});

it("owner cancellation performs no financial write", async () => {
  const Panel = await panel(); render(<Panel {...options} />);
  await screen.findByText("Годовой итог отличается на 14000.00");
  vi.spyOn(window, "confirm").mockReturnValue(false);
  fireEvent.click(screen.getByRole("button", { name: /Подтвердить создание/ }));
  expect(mockApi.mock.calls.some(([path]) => path.endsWith("article-budget-import"))).toBe(false);
});

it("explicit confirmation sends exact pin/hash and no legacy row IDs or approval", async () => {
  const Panel = await panel(); render(<Panel {...options} />);
  await screen.findByText("Годовой итог отличается на 14000.00");
  vi.spyOn(window, "confirm").mockReturnValue(true);
  mockApi.mockResolvedValueOnce({ operation_id: 1, created_budget_ids: [100], used_budget_ids: [], created_cash_flow_ids: [], status: "proposed" });
  fireEvent.click(screen.getByRole("button", { name: /Подтвердить создание/ }));
  await waitFor(() => expect(options.onApplied).toHaveBeenCalled());
  const payload = JSON.parse(mockApi.mock.calls[1][1]?.body as string);
  expect(payload).toMatchObject({ project_id: 17, contract_id: 25, mode: "create_budget", owner_confirmed: true,
    expected_document_version_id: 1810, expected_document_sha256: "a".repeat(64), preview_hash: "b".repeat(64) });
  expect(payload).not.toHaveProperty("existing_rows");
  expect(payload).not.toHaveProperty("status");
});

it("conflicts block confirmation and show reason code", async () => {
  mockApi.mockResolvedValue({ ...proposal, conflicts: [{ code: "ARTICLE_AMBIGUOUS", message: "Дубликат" }] });
  const Panel = await panel(); render(<Panel {...options} />);
  await screen.findByText(/ARTICLE_AMBIGUOUS/);
  expect(screen.getByRole("button", { name: /Подтвердить создание/ }).hasAttribute("disabled")).toBe(true);
});

it("a late response from the old project cannot replace the new preview", async () => {
  let resolve!: (value: unknown) => void;
  mockApi.mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
  const Panel = await panel(); const view = render(<Panel {...options} />);
  view.rerender(<Panel {...options} projectId={18} documentId={2440} />);
  await screen.findByText("Годовой итог отличается на 14000.00");
  resolve({ ...proposal, warnings: ["Старый проект"] });
  await waitFor(() => expect(screen.queryByText("Старый проект")).toBeNull());
});
