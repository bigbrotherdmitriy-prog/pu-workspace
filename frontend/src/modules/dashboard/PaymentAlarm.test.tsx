import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { PaymentAlarm } from "./PaymentAlarm";

vi.mock("../../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/client")>()),
  api: vi.fn(),
}));
const mockApi = vi.mocked(api);
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it("shows overdue receive in Приходы card", async () => {
  mockApi.mockResolvedValue({
    items: [
      {
        kind: "receive",
        title: "Оплата по акту №1",
        counterparty: "ООО Ромашка",
        contract_id: 1,
        amount: "150000.00",
        due_date: "2026-01-01",
        days_left: -3,
        level: "overdue",
      },
    ],
    counts: {},
    totals: { to_pay: "0", to_receive: "150000.00" },
  });

  render(<PaymentAlarm projectId={7} />);

  const section = await screen.findByLabelText("Сигнализация оплат");
  const receiveCard = within(section).getByText(/Приходы — есть просрочка/);
  expect(receiveCard).toBeTruthy();
  expect(within(section).getAllByText(/150\s?000,00/).length).toBeGreaterThan(0);
});

it("shows critical pay_invoice count in Расходы card", async () => {
  mockApi.mockResolvedValue({
    items: [
      {
        kind: "pay_invoice",
        title: "Счёт №42",
        counterparty: "ООО Поставщик",
        contract_id: 2,
        amount: "5000.00",
        due_date: "2026-02-02",
        days_left: 1,
        level: "critical",
      },
    ],
    counts: {},
    totals: { to_pay: "5000.00", to_receive: "0" },
  });

  render(<PaymentAlarm projectId={7} />);

  await screen.findByLabelText("Сигнализация оплат");
  const criticalLabels = screen.getAllByText("В ближайшие 2 дня");
  expect(criticalLabels.length).toBeGreaterThan(0);
  const payCardTitle = screen.getByText("Расходы");
  const card = payCardTitle.closest(".payment-alarm__card") as HTMLElement | null;
  expect(card).toBeTruthy();
  const criticalRow = Array.from(card!.querySelectorAll(".payment-alarm__level")).find(
    (el) => el.textContent?.includes("В ближайшие 2 дня"),
  );
  expect(criticalRow).toBeTruthy();
  expect(criticalRow!.textContent).toContain("1");
});

it("shows всё в порядке in both cards with empty items", async () => {
  mockApi.mockResolvedValue({
    items: [],
    counts: {},
    totals: { to_pay: "0", to_receive: "0" },
  });

  render(<PaymentAlarm projectId={7} />);

  await screen.findByLabelText("Сигнализация оплат");
  const ok = screen.getAllByText("всё в порядке");
  expect(ok.length).toBe(2);
});
