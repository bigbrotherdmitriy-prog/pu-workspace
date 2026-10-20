import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { PortfolioModule } from "./PortfolioModule";

vi.mock("../../api/client", async (importOriginal) => ({ ...(await importOriginal<typeof import("../../api/client")>()), api: vi.fn() }));
const mockApi = vi.mocked(api);
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("renders portfolio cards with statuses and cash gap month", async () => {
  mockApi.mockResolvedValue({
    projects: [
      {
        project_id: 1,
        name: "Риск-проект",
        currency: "RUB",
        plan_inflow: "1000.00",
        plan_outflow: "900.00",
        received_inflow: "100.00",
        paid_outflow: "500.00",
        overdue_count: 2,
        overdue_amount: "15000.00",
        invoices_to_confirm: 1,
        cash_gap_month: "2026-02",
        cash_gap_amount: "400000.00",
        cash_gap_explanation: "Разрыв из-за задержки оплаты",
        status: "risk",
      },
      {
        project_id: 2,
        name: "Норм-проект",
        currency: "RUB",
        plan_inflow: "500.00",
        plan_outflow: "400.00",
        received_inflow: "500.00",
        paid_outflow: "400.00",
        overdue_count: 0,
        overdue_amount: "0.00",
        invoices_to_confirm: 0,
        cash_gap_month: null,
        cash_gap_amount: null,
        cash_gap_explanation: null,
        status: "ok",
      },
    ],
    totals: {
      projects: 2,
      risk: 1,
      attention: 0,
      overdue_amount: "15000.00",
      invoices_to_confirm: 1,
    },
  });

  render(<PortfolioModule onOpenProject={() => undefined} />);

  expect(await screen.findByText("Риск-проект")).toBeTruthy();
  expect(await screen.findByText("Норм-проект")).toBeTruthy();
  expect(await screen.findByText("Риск")).toBeTruthy();
  expect(await screen.findByText("В норме")).toBeTruthy();
  expect(await screen.findByText(/февраль 2026/)).toBeTruthy();
});

it("calls onOpenProject with the project id when «Открыть проект» is clicked", async () => {
  mockApi.mockResolvedValue({
    projects: [
      {
        project_id: 42,
        name: "Тестовый",
        currency: "RUB",
        plan_inflow: "0.00",
        plan_outflow: "0.00",
        received_inflow: "0.00",
        paid_outflow: "0.00",
        overdue_count: 0,
        overdue_amount: "0.00",
        invoices_to_confirm: 0,
        cash_gap_month: null,
        cash_gap_amount: null,
        cash_gap_explanation: null,
        status: "ok",
      },
    ],
    totals: {
      projects: 1,
      risk: 0,
      attention: 0,
      overdue_amount: "0.00",
      invoices_to_confirm: 0,
    },
  });

  const onOpenProject = vi.fn();
  render(<PortfolioModule onOpenProject={onOpenProject} />);

  fireEvent.click(await screen.findByLabelText("Открыть проект Тестовый"));
  expect(onOpenProject).toHaveBeenCalledWith(42);
});
