import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { ProjectMoneyRings } from "./ProjectMoneyRings";

vi.mock("../../api/client", async (importOriginal) => ({ ...(await importOriginal<typeof import("../../api/client")>()), api: vi.fn() }));
const mockApi = vi.mocked(api);
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("renders the money rings with percentages and the cash figure", async () => {
  mockApi.mockResolvedValue({
    income: { plan: "1000", fact: "250" },
    expense_total: { plan: "800", fact: "200", invoiced: "400" },
    profit: { plan: "200", fact: "50" },
    margin_pct: { plan: "20.00", fact: "20.00" },
  });

  render(<ProjectMoneyRings projectId={7} />);

  expect(await screen.findByLabelText("Получено от заказчика: 25%")).toBeTruthy();
  expect(screen.getByLabelText("Оплачено расходов: 25%")).toBeTruthy();
  expect(screen.getByLabelText("Счета оплачены: 50%")).toBeTruthy();
  expect(screen.getByText("Деньги сейчас")).toBeTruthy();
  expect(screen.getAllByText(/50/).length).toBeGreaterThan(0);
  expect(screen.getByText(/Прибыль по плану/)).toBeTruthy();
});
