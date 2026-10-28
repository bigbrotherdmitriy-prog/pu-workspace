import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { PurchaseInvoices } from "./PurchaseInvoices";

vi.mock("../../api/client", async (importOriginal) => ({ ...(await importOriginal<typeof import("../../api/client")>()), api: vi.fn() }));
const mockApi = vi.mocked(api);
afterEach(() => { cleanup(); vi.restoreAllMocks(); mockApi.mockReset(); });

const invoices = [
  { entry_id: 10, title: "Счёт №1", counterparty: "ООО Ромашка", planned_date: "2025-01-10", amount: "1000", paid: true, lines: 2, confirmed: 1, suggested: 0 },
  { entry_id: 11, title: "Счёт №2", counterparty: "ИП Иванов", planned_date: null, amount: "500", paid: false, lines: 0, confirmed: 0, suggested: 0 },
];

const lines = [
  { id: 100, cash_flow_entry_id: 10, invoice_title: "Счёт №1", counterparty: "ООО Ромашка", name: "Кабель", unit: "м", qty: "10", match_status: "suggested", match_score: "87", purchase_item_id: null, purchase_item_name: null },
  { id: 101, cash_flow_entry_id: 10, invoice_title: "Счёт №1", counterparty: "ООО Ромашка", name: "Автомат", unit: "шт", qty: "3", match_status: "confirmed", match_score: "100", purchase_item_id: 2, purchase_item_name: "Автомат ВА47" },
  { id: 102, cash_flow_entry_id: 10, invoice_title: "Счёт №1", counterparty: "ООО Ромашка", name: "Розетка", unit: "шт", qty: "5", match_status: "none", match_score: null, purchase_item_id: null, purchase_item_name: null },
];

const purchaseItems = { items: [{ id: 1, name: "Кабель ВВГ" }, { id: 2, name: "Автомат ВА47" }], summary: {} };

it("shows invoices list and lines with badges", async () => {
  mockApi.mockImplementation(async (path: string) => {
    if (path.endsWith("/purchase/invoices")) return { invoices };
    if (path.includes("/invoice-lines?entry_id=10")) return lines;
    if (path.endsWith("/purchase")) return purchaseItems;
    return {};
  });
  render(<PurchaseInvoices projectId={7} />);
  expect(await screen.findByText("Счёт №1")).toBeTruthy();
  expect(screen.getByText("оплачен")).toBeTruthy();
  expect(screen.getByText("не оплачен")).toBeTruthy();
  fireEvent.click(screen.getByText("Счёт №1"));
  expect(await screen.findByText("Кабель")).toBeTruthy();
  expect(screen.getByText("предложено 87%")).toBeTruthy();
  expect(screen.getByText("подтверждено")).toBeTruthy();
  expect(screen.getByText("нет")).toBeTruthy();
});

it("Подтвердить posts match with purchase_item_id", async () => {
  mockApi.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path.endsWith("/purchase/invoices")) return { invoices };
    if (path.includes("/invoice-lines?entry_id=10")) return lines;
    if (path.endsWith("/purchase")) return purchaseItems;
    if (path.match(/\/invoice-lines\/100\/match$/)) return lines[0];
    if (init?.method === "POST") return {};
    return {};
  });
  render(<PurchaseInvoices projectId={7} />);
  fireEvent.click(await screen.findByText("Счёт №1"));
  const selects = await screen.findAllByLabelText("Позиция закупки") as HTMLSelectElement[];
  fireEvent.change(selects[0], { target: { value: "1" } });
  const buttons = screen.getAllByText("Подтвердить");
  fireEvent.click(buttons[0]);
  await waitFor(() => expect(mockApi).toHaveBeenCalledWith(
    "/projects/7/invoice-lines/100/match",
    expect.objectContaining({ method: "POST", body: JSON.stringify({ purchase_item_id: 1 }) }),
  ));
});

it("Снять posts match with null", async () => {
  mockApi.mockImplementation(async (path: string) => {
    if (path.endsWith("/purchase/invoices")) return { invoices };
    if (path.includes("/invoice-lines?entry_id=10")) return lines;
    if (path.endsWith("/purchase")) return purchaseItems;
    return {};
  });
  render(<PurchaseInvoices projectId={7} />);
  fireEvent.click(await screen.findByText("Счёт №1"));
  await screen.findByText("Автомат");
  fireEvent.click(screen.getByText("Снять"));
  await waitFor(() => expect(mockApi).toHaveBeenCalledWith(
    "/projects/7/invoice-lines/101/match",
    expect.objectContaining({ method: "POST", body: JSON.stringify({ purchase_item_id: null }) }),
  ));
});

it("extract posts and 422 error shows in role=alert", async () => {
  mockApi.mockImplementation(async (path: string) => {
    if (path.endsWith("/purchase/invoices")) return { invoices };
    if (path.includes("/invoice-lines?entry_id=10")) return lines;
    if (path.endsWith("/purchase")) return purchaseItems;
    if (path.endsWith("/extract")) throw new Error("У счёта нет исходного документа");
    return {};
  });
  render(<PurchaseInvoices projectId={7} />);
  fireEvent.click(await screen.findByText("Счёт №1"));
  await screen.findByText("Автомат");
  fireEvent.click(screen.getByText("Извлечь строки из счёта"));
  await waitFor(() => expect(mockApi).toHaveBeenCalledWith(
    "/projects/7/cash-flow/10/invoice-lines/extract",
    expect.objectContaining({ method: "POST" }),
  ));
  const alert = await screen.findByRole("alert");
  expect(alert.textContent).toMatch(/У счёта нет исходного документа/);
});

it("empty invoices shows hint", async () => {
  mockApi.mockImplementation(async (path: string) => {
    if (path.endsWith("/purchase/invoices")) return { invoices: [] };
    if (path.endsWith("/purchase")) return { items: [] };
    return {};
  });
  render(<PurchaseInvoices projectId={7} />);
  expect(await screen.findByText("Нет счетов с исходным документом")).toBeTruthy();
});
