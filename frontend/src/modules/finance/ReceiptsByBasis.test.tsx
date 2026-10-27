import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { ReceiptsByBasis } from "./ReceiptsByBasis";

vi.mock("../../api/client", async (importOriginal) => ({ ...(await importOriginal<typeof import("../../api/client")>()), api: vi.fn() }));
const mockApi = vi.mocked(api);
afterEach(() => { cleanup(); vi.restoreAllMocks(); mockApi.mockReset(); });

const response = {
  contracts: [
    {
      contract_id: 1,
      number: "Д-42",
      counterparty: "ООО «Ромашка»",
      documents: [
        { id: 101, title: "КС-3 № 1", kind: "КС-3", date: "2025-09-10", amount: "1000.00", received: "500.00", remaining: "500.00", state: "expected", source_document_id: null },
        { id: 102, title: "Акт № 7", kind: "Акт", date: "2025-08-01", amount: "300.00", received: "0.00", remaining: "300.00", state: "overdue", source_document_id: 55 },
        { id: 103, title: "Счёт № 9", kind: "Счёт", date: "2025-07-15", amount: "200.00", received: "200.00", remaining: "0.00", state: "received", source_document_id: null },
      ],
      totals: { invoiced: "1500.00", received: "700.00", remaining: "800.00" },
    },
  ],
  totals: { invoiced: "1500.00", received: "700.00", remaining: "800.00" },
};

it("shows contracts, documents, statuses and totals", async () => {
  mockApi.mockResolvedValue(response);
  render(<ReceiptsByBasis projectId={5} />);
  expect(await screen.findByText(/ООО «Ромашка»/)).toBeTruthy();
  expect(screen.getByText("КС-3 № 1")).toBeTruthy();
  expect(screen.getByText("просрочено")).toBeTruthy();
  expect(screen.getByText("получено")).toBeTruthy();
  expect(screen.getByText(/Долг заказчика/)).toBeTruthy();
});

it("opens the receipt form with remaining by default and posts record-receipt", async () => {
  mockApi.mockResolvedValue(response);
  render(<ReceiptsByBasis projectId={5} />);
  fireEvent.click((await screen.findAllByRole("button", { name: "Получено" }))[0]);
  const amount = screen.getByLabelText("Сумма поступления") as HTMLInputElement;
  expect(amount.value).toBe("500.00");
  fireEvent.click(screen.getByText("Сохранить"));
  await waitFor(() => expect(mockApi).toHaveBeenCalledWith(
    "/projects/5/cash-flow/101/record-receipt",
    expect.objectContaining({ method: "POST", body: expect.stringContaining('"amount":"500.00"') }),
  ));
});

it("shows server error in an alert", async () => {
  mockApi.mockImplementation(async (path: string) => {
    if (path.endsWith("/record-receipt")) throw new Error("Сервер недоступен");
    return response;
  });
  render(<ReceiptsByBasis projectId={5} />);
  fireEvent.click((await screen.findAllByRole("button", { name: "Получено" }))[0]);
  fireEvent.click(screen.getByText("Сохранить"));
  expect(await screen.findByRole("alert")).toBeTruthy();
});

it("does not crash on empty response", async () => {
  mockApi.mockResolvedValue({ contracts: [], totals: { invoiced: "0", received: "0", remaining: "0" } });
  render(<ReceiptsByBasis projectId={5} />);
  expect(await screen.findByText("Приходов по основаниям пока нет")).toBeTruthy();
});
